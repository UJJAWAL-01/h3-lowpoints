"""Storms that last: how depressions fill, overflow and drain as the rain falls, hour by hour.

    from h3lowpoints.storms import storm_response
    table = storm_response("results", years=100, hours=6, curve_numbers="cn.tif", cache_dir="atlas14")
    table = storm_response("results", depth_mm=300, hours=6, method="green-ampt",
                           impervious="imp.tif", soil=fetch_soil_infiltration(...), cache_dir="atlas14")

    h3lowpoints storm results --years 100 --hours 6 --curve-numbers cn.tif

The susceptibility index (`susceptibility`) uses storm totals: 300 mm in 6 hours and in 3 days
give the same answer. Here the storm is followed in time steps (about 5 minutes), so its length
matters in three ways, each a separate term:

- How rare it is. NOAA Atlas 14 gives the depth of the N-year storm for any duration from
  5 minutes to 60 days (`sources.rainfall.Atlas14Storms`), and so the return period of any
  (depth, duration): `storm_years`.
- How the rain falls. By default, NOAA Atlas 14's temporal distribution of the region at the
  depression's lowest point (the all-cases median curve), or uniform rain, or a curve given.
- How much runs off, two methods to be compared on real storms:
  "curve-number" (A): the SCS runoff of the rain fallen so far (`runoff.runoff_mm`), step by
  step; the total is that of the susceptibility index. Dry or wet ground: `condition`.
  "green-ampt" (B): the paved share sheds all its rain; the rest takes in water as fast as the
  Green-Ampt equation allows for the soil's conductivity, wetting-front suction and water
  deficit (`sources.soils.fetch_soil_infiltration`), so intense rain runs off more.
- How a depression empties: seepage through its floor (the soil's conductivity at the lowest
  point times the wet area, the paved share not seeping), and, if asked, storm drains that carry
  away up to the rate of a chosen Atlas 14 storm from drained (paved enough) catchments: an
  assumption, since no national map of storm drains exists.

Each depression is a bowl whose volume grows with depth as V(h) = V (h/H)^k, k = A H / V, from
its full volume V, maximum depth H and area A (exact when full; a power law, as used for natural
prairie depressions by Hayashi and van der Kamp, 2000). Runoff reaches it in the step it falls,
overflow passes down the spill links (`spill_to`) in the same step, it starts empty and nothing
evaporates. See docs/storms.md for the method, the sources of every number, and the limits.
"""
import numpy as np
import pandas as pd
from numba import njit

from .hydro.cascade import spill_order
from .pipeline import _package_version
from .runoff import CONDITIONS, adjust_curve_number, retention_mm
from .store import CHUNK_ROWS, ids_to_int, write_table, table_path, read_meta, write_meta
from .susceptibility import _cell_source, _tables, catchment_sums

METHODS = ("curve-number", "green-ampt")
STORM_COLUMNS = ["id", "storm_mm", "storm_hours", "storm_years", "runoff_mm", "inflow_m3",
                 "peak_volume_m3", "peak_depth_m", "peak_fill", "time_to_fill_h", "overflow_m3",
                 "seepage_m3", "drained_m3", "water_left_m3", "ponding_hours"]
PONDING_DEPTH_M = 0.1          # a convention for "water standing", not calibrated
AFTER_HOURS = 72.0             # how long the run continues after the rain stops
STEP_MINUTES = 5.0


@njit(cache=True)
def green_ampt_step(F, ks, sd, hours):
    """Cumulative infiltration (mm) after `hours` of water standing on the ground, from F mm, for
    conductivity `ks` (mm/h) and suction head x water deficit `sd` (mm): the implicit Green-Ampt
    equation F2 - F - sd ln((F2 + sd) / (F + sd)) = ks hours, solved by Newton's method."""
    target = ks * hours
    if target <= 0.0:
        return F
    if sd <= 0.0:
        return F + target
    hi = F + target + np.sqrt(2.0 * sd * target)
    while hi - F - sd * np.log1p((hi - F) / (F + sd)) - target < 0.0:
        hi = F + 2.0 * (hi - F)
    x = hi                          # the function is convex and rising: from above, Newton
    for _ in range(100):            # steps down to the root without passing it
        g = (x - F) - sd * np.log1p((x - F) / (F + sd)) - target
        step = g * (x + sd) / x
        x -= step
        if step <= 1e-12 * (1.0 + x):
            break
    return x


@njit(cache=True)
def _simulate(order, target, unknown, method, cum, pattern_of, depth, n_rain, n_steps, dt,
              catch_area, retention, impervious, ks, sd, volume, area, max_depth, k, seep_rate,
              drain_rate, ponding_depth):
    n = len(order)
    store, F, rain_so_far, runoff_so_far = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    spill_in = np.zeros(n)
    own, inflow, peak = np.zeros(n), np.zeros(n), np.zeros(n)
    fill_time = np.full(n, np.nan)                          # in steps, within the step
    seeped, drained, over = np.zeros(n), np.zeros(n), np.zeros(n)
    ponded = np.zeros(n, dtype=np.int64)
    leaving = 0.0
    hours = dt / 3600.0
    for t in range(n_steps):
        spill_in[:] = 0.0
        for i in order:
            if unknown[i]:
                continue
            q = 0.0                                             # runoff of this step, mm
            if t < n_rain:
                p = depth[i] * (cum[pattern_of[i], t + 1] - cum[pattern_of[i], t])
                if method == 0:
                    rain_so_far[i] += p
                    excess = rain_so_far[i] - 0.2 * retention[i]
                    total = excess * excess / (excess + retention[i]) if excess > 0.0 else 0.0
                    q = max(total - runoff_so_far[i], 0.0)
                    runoff_so_far[i] = total
                else:
                    taken = min(p, green_ampt_step(F[i], ks[i], sd[i], hours) - F[i])
                    F[i] += taken
                    q = impervious[i] * p + (1.0 - impervious[i]) * (p - taken)
            mine = q / 1000.0 * catch_area[i]                   # m3
            own[i] += mine
            water = mine + spill_in[i]
            inflow[i] += water
            s = store[i] + water
            if s > 0.0:
                wet = area[i] * min(s / volume[i], 1.0) ** ((k[i] - 1.0) / k[i])
                lost = min(seep_rate[i] * wet * dt, s)
                seeped[i] += lost
                s -= lost
                lost = min(drain_rate[i] * dt, s)
                drained[i] += lost
                s -= lost
            if s >= volume[i] * (1.0 - 1e-12) and np.isnan(fill_time[i]):
                # the water of this step arrives evenly through it
                before = s - water
                fill_time[i] = t + (min(max((volume[i] - before) / water, 0.0), 1.0) if water > 0.0 else 1.0)
            extra = s - volume[i]
            if extra > 0.0:
                over[i] += extra
                s = volume[i]
                if target[i] >= 0:
                    spill_in[target[i]] += extra
                else:
                    leaving += extra
            store[i] = s
            if s > peak[i]:
                peak[i] = s
            if s > 0.0 and max_depth[i] * (s / volume[i]) ** (1.0 / k[i]) >= ponding_depth:
                ponded[i] += 1
    return own, inflow, peak, fill_time, seeped, drained, over, store, ponded, leaving


def pattern_steps(times, cumulative, n_steps):
    """A cumulative rain curve (time share, cumulative share) at the ends of `n_steps` equal steps."""
    times, cumulative = np.asarray(times, float), np.asarray(cumulative, float)
    if (times[0] != 0 or times[-1] != 1 or abs(cumulative[0]) > 1e-9 or abs(cumulative[-1] - 1) > 1e-3
            or (np.diff(times) <= 0).any() or (np.diff(cumulative) < 0).any()):
        raise ValueError("a rain curve runs from (0, 0) to (1, 1), rising, in shares of the "
                         "duration and of the rain")
    out = np.interp(np.linspace(0.0, 1.0, n_steps + 1), times, cumulative)
    return out / out[-1]


def simulate(spill_to_index, depth_mm, cum, *, catch_area, volume, area, max_depth, dt,
             n_after=0, method="curve-number", curve_number=None, impervious=None, ksat_mm_h=None,
             suction_deficit_mm=None, seepage_mm_h=None, drain_mm_h=None,
             ponding_depth_m=PONDING_DEPTH_M, pattern_of=None):
    """The time-stepping model on arrays, one value per depression (see the module text).

    spill_to_index  position of the depression each spills into, -1 for none
    depth_mm        storm depth on each catchment, mm
    cum             cumulative rain shares at the ends of the rain steps: one curve (1-d) or one
                    per pattern (2-d, chosen by `pattern_of`)
    catch_area, volume, area, max_depth   catchment area (m2), volume (m3), area (m2), depth (m)
    dt              step, seconds; the rain lasts len(cum) - 1 steps, then `n_after` more are run
    curve_number    method "curve-number": each catchment's curve number
    impervious, ksat_mm_h, suction_deficit_mm   method "green-ampt": paved share (0 to 1), and
                    the conductivity and suction x water deficit of the rest
    seepage_mm_h    conductivity at the floor (mm/h, already reduced for paving), or None
    drain_mm_h      rain rate drains carry away from the whole catchment, mm/h, or None
    NaN in an input a method needs makes that depression, and every one downstream, unknown.

    Returns a dict of arrays (inflow_m3 ... ponding_hours as in STORM_COLUMNS, NaN where
    unknown) and the water that left the table (m3).
    """
    target = np.asarray(spill_to_index, dtype=np.int64)
    n = len(target)
    order = _spill_order_of(target)
    cum = np.atleast_2d(np.asarray(cum, dtype=float))
    pattern_of = np.zeros(n, np.int64) if pattern_of is None else np.asarray(pattern_of, np.int64)
    n_rain = cum.shape[1] - 1

    def arr(v, default=0.0):
        return np.full(n, default) if v is None else np.broadcast_to(np.asarray(v, float), (n,)).copy()
    depth, catch_area, volume = arr(depth_mm), arr(catch_area), arr(volume)
    area, max_depth = arr(area), arr(max_depth)
    if method == "curve-number":
        cn = arr(curve_number, np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            retention = retention_mm(cn)
        imp, ks, sd = arr(None), arr(None), arr(None)
        need = [cn]
    elif method == "green-ampt":
        retention = arr(None)
        imp, ks, sd = arr(impervious, np.nan), arr(ksat_mm_h, np.nan), arr(suction_deficit_mm, np.nan)
        need = [imp, ks, sd]
    else:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}")
    seep = arr(seepage_mm_h) / 1000.0 / 3600.0                           # m/s
    drain = arr(drain_mm_h) / 1000.0 / 3600.0 * catch_area               # m3/s
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(volume > 0, area * max_depth / volume, 1.0)
    bad = np.zeros(n, bool)
    for v in need + [depth, seep, drain, catch_area, volume, area, max_depth]:
        bad |= np.isnan(v)
    bad |= ~(volume > 0) | ~(area > 0) | ~(max_depth > 0) | np.isnan(cum[pattern_of]).any(axis=1)
    unknown = _downstream(order, target, bad)
    k = np.where(unknown, 1.0, np.maximum(k, 1.0))
    safe = [np.where(unknown, 0.0, v) for v in (depth, retention, imp, ks, sd, seep, drain)]
    own, inflow, peak, fill_time, seeped, drained, over, store, ponded, leaving = _simulate(
        order, target, unknown, 0 if method == "curve-number" else 1, cum, pattern_of, safe[0],
        n_rain, n_rain + int(n_after), float(dt), catch_area, safe[1], safe[2], safe[3], safe[4],
        np.where(unknown, 1.0, volume), np.where(unknown, 1.0, area),
        np.where(unknown, 1.0, max_depth), k, safe[5], safe[6], float(ponding_depth_m))
    with np.errstate(divide="ignore", invalid="ignore"):
        end_depth = max_depth * (store / volume) ** (1.0 / k)
        out = {"own_m3": own, "inflow_m3": inflow, "peak_volume_m3": peak,
               "peak_depth_m": max_depth * (peak / volume) ** (1.0 / k),
               "peak_fill": peak / volume,
               "time_to_fill_h": fill_time * dt / 3600.0,
               "overflow_m3": over, "seepage_m3": seeped, "drained_m3": drained,
               "water_left_m3": store,
               "ponding_hours": np.where(end_depth >= ponding_depth_m, np.inf, ponded * dt / 3600.0)}
    for name, v in out.items():
        out[name] = np.where(unknown, np.nan, v)
    return out, leaving


def _spill_order_of(target):
    from .hydro.cascade import _order

    order = _order(target)
    if len(order) != len(target):
        raise ValueError("following spill_to goes round in a loop")
    return order


def _downstream(order, target, bad):
    """bad, spread to every depression downstream of a bad one."""
    out = bad.copy()
    for i in order:
        if out[i] and target[i] >= 0:
            out[target[i]] = True
    return out


def _sources(names, given, res, rows, workers):
    """{column: (lookup, description)} for the per-cell inputs given (rasters, tables, frames)."""
    return {name: _cell_source(src, name, res, rows, workers) for name, src in zip(names, given)
            if src is not None and not isinstance(src, (int, float))}


def _at_cells(source, column, cells, res, rows, workers):
    """Values of a per-cell input at H3 cells (ints), any order; a number is used as it is."""
    if isinstance(source, (int, float)):
        return np.full(len(cells), float(source))
    lookup, _ = _cell_source(source, column, res, rows, workers)
    unique, inverse = np.unique(cells, return_inverse=True)
    return lookup.lookup(unique)[inverse] if len(unique) else np.zeros(0)


def storm_response(result, *, hours, years=None, depth_mm=None, method="curve-number",
                   curve_numbers=None, curve_number=None, condition="average", impervious=None,
                   soil=None, pattern="atlas14", case="all", percent=50, seepage=None, drains=None,
                   ponding_depth_m=PONDING_DEPTH_M, after_hours=AFTER_HOURS,
                   step_minutes=STEP_MINUTES, rarity=True, min_coverage=0.9, cache_dir=None,
                   rows=CHUNK_ROWS, workers=1):
    """One row per depression: how one storm fills, overflows and drains it (STORM_COLUMNS). See
    the module text and docs/storms.md.

    result         a Result computed with catchments=True, or its folder
    hours          the storm's duration, hours (5 minutes to 60 days for Atlas 14 storms)
    years          the storm: NOAA Atlas 14's storm of this return period (ATLAS14_YEARS) ...
    depth_mm       ... or a depth in mm, or a function of (lat, lon) giving mm
    method         "curve-number" (A) or "green-ampt" (B)
    curve_numbers, curve_number   method A: as in `susceptibility`
    condition      "average", "dry" or "wet" ground before the storm: for method A, NRCS ARC II,
                   I or III curve numbers; for method B, soil water at field capacity, the
                   wilting point or saturation
    impervious     percent paved per cell (raster, table or DataFrame, column impervious_pct);
                   needed by method B and by drains
    soil           method B and seepage: the dict of `sources.soils.fetch_soil_infiltration`, or
                   a dict with ksat (mm/h), suction (mm), deficit_average and deficit_dry (shares)
                   and optionally fallback, each a raster, table, DataFrame or number
    pattern        "atlas14" (NOAA's curve for the region at the lowest point; `case` and
                   `percent` choose it), "uniform", or (time shares, cumulative rain shares)
    seepage        let ponded water seep through the floor (needs `soil`; default: when soil is
                   given)
    drains         None, or {"years": N, "hours": T, "min_impervious": pct}: catchments at least
                   pct% paved lose up to the intensity of the N-year, T-hour Atlas 14 storm
    ponding_depth_m   depth counted as standing water for `ponding_hours`
    after_hours    hours run after the rain stops (a depression still holding water at least
                   ponding_depth_m deep at the end has ponding_hours inf)
    step_minutes   the step is the storm's length divided evenly into steps of about this long
    rarity         give each depression the return period of its storm (`storm_years`, from NOAA
                   Atlas 14 grids; NaN when False)
    min_coverage, cache_dir, rows, workers   as in `susceptibility`
    """
    from .sources.rainfall import Atlas14Storms, TemporalPatterns

    if (years is None) == (depth_mm is None):
        raise ValueError("give either years (an Atlas 14 storm) or depth_mm")
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}")
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {list(CONDITIONS)}, got {condition!r}")
    if method == "curve-number" and (curve_numbers is None) == (curve_number is None):
        raise ValueError("method curve-number needs either curve_numbers or curve_number")
    if method == "green-ampt" and (soil is None or impervious is None):
        raise ValueError("method green-ampt needs soil and impervious")
    seepage = soil is not None if seepage is None else bool(seepage)
    if seepage and soil is None:
        raise ValueError("seepage needs soil (or pass seepage=False)")
    if drains is not None and impervious is None:
        raise ValueError("drains need impervious")
    if not hours > 0 or not step_minutes > 0 or after_hours < 0:
        raise ValueError("hours and step_minutes must be positive, after_hours not negative")
    deps, catchment_chunks, meta = _tables(result, rows, extra=["max_depth_m", "area_m2", "lat", "lon",
                                                                "h3_lowpoint"])
    res = meta["params"]["res"]
    ids = deps["id"].to_numpy(dtype=np.int64)
    n = len(ids)
    lat, lon = deps["lat"].to_numpy(float), deps["lon"].to_numpy(float)
    low_cells = ids_to_int(list(deps["h3_lowpoint"])) if n else np.zeros(0, np.uint64)
    catch = deps["catchment_area_m2"].to_numpy(float)
    storms = Atlas14Storms(cache_dir=cache_dir) if years is not None or drains or rarity else None

    # per-cell inputs averaged over each catchment
    soil = dict(soil or {})
    deficit_key = {"average": "deficit_average", "dry": "deficit_dry", "wet": None}[condition]
    names, given = [], []
    if method == "curve-number" and curve_numbers is not None:
        names.append("curve_number"), given.append(curve_numbers)
    if impervious is not None:
        names.append("impervious_pct"), given.append(impervious)
    if method == "green-ampt":
        for name in ("ksat", "suction") + ((deficit_key,) if deficit_key else ()):
            if name not in soil:
                raise ValueError(f"soil needs {name}")
            names.append(name), given.append(soil[name])
        if soil.get("fallback") is not None:
            names.append("fallback"), given.append(soil["fallback"])
    sources = _sources(names, given, res, rows, workers)
    if years is not None:
        samplers = [lambda la, lo: storms.depth(years, hours, la, lo)]
    elif callable(depth_mm):
        samplers = [depth_mm]
    else:
        if not depth_mm > 0:
            raise ValueError("depth_mm must be positive")
        samplers = [float(depth_mm)]
    covered, summed, rain_area, rain_sum = catchment_sums(ids, catchment_chunks, sources, samplers)
    with np.errstate(invalid="ignore", divide="ignore"):
        def mean(name, fixed=None):
            if name not in sources:
                return np.full(n, np.nan if fixed is None else float(fixed))
            return np.where(covered[name] >= min_coverage * catch, summed[name] / covered[name], np.nan)
        depth = np.where(rain_area[:, 0] >= min_coverage * catch, rain_sum[:, 0] / rain_area[:, 0], np.nan)
    imp_pct = mean("impervious_pct")
    out_extra = {}
    if method == "curve-number":
        cn = mean("curve_number", curve_number)
        cn_used = adjust_curve_number(cn, condition)
        kwargs = {"curve_number": cn_used}
        out_extra["curve_number"] = cn_used
    else:
        def fixed(name):
            v = soil.get(name)
            return v if isinstance(v, (int, float)) else None
        ks, suction = mean("ksat", fixed("ksat")), mean("suction", fixed("suction"))
        deficit = mean(deficit_key, fixed(deficit_key)) if deficit_key else np.zeros(n)
        kwargs = {"impervious": imp_pct / 100.0, "ksat_mm_h": ks, "suction_deficit_mm": suction * deficit}
        out_extra.update({"impervious_pct": imp_pct, "ksat_mm_h": ks, "suction_mm": suction,
                          "water_deficit": deficit})
        if "fallback" in sources:
            out_extra["soil_fallback_share"] = mean("fallback")

    # at the lowest point: seepage, drains, the rain's time pattern and the storm's rarity
    seep = None
    if seepage:
        k_low = _at_cells(soil["ksat"], "ksat", low_cells, res, rows, workers)
        imp_low = (_at_cells(impervious, "impervious_pct", low_cells, res, rows, workers) / 100.0
                   if impervious is not None else np.zeros(n))
        seep = k_low * (1.0 - np.clip(np.nan_to_num(imp_low), 0.0, 1.0))
        out_extra["seepage_mm_h"] = seep
    drain = None
    if drains is not None:
        d_years, d_hours = drains["years"], drains.get("hours", 1.0)
        floor = drains.get("min_impervious", 20.0)
        rate = storms.depth(d_years, d_hours, lat, lon) / d_hours if n else np.zeros(0)
        drain = np.where(imp_pct >= floor, rate, 0.0)
        drain = np.where(np.isnan(imp_pct), np.nan, drain)
        out_extra["drain_mm_h"] = drain
    n_rain = max(1, int(round(hours * 60.0 / step_minutes)))
    dt = hours * 3600.0 / n_rain
    n_after = int(np.ceil(after_hours * 3600.0 / dt))
    if isinstance(pattern, str) and pattern == "atlas14":
        temporal = TemporalPatterns(cache_dir=cache_dir)
        regions = temporal.region(lat, lon) if n else np.zeros(0, object)
        names_r = sorted({r for r in regions if r is not None})
        curves = [pattern_steps(*temporal.pattern(r, hours, case, percent), n_rain) for r in names_r]
        curves.append(np.full(n_rain + 1, np.nan))                     # no region: unknown
        cum = np.vstack(curves)
        pattern_of = np.array([names_r.index(r) if r is not None else len(names_r) for r in regions],
                              dtype=np.int64)
        pattern_text = (f"NOAA Atlas 14 temporal distribution of the region at the lowest point, "
                        f"{case} cases, {percent}% curve, of the {_nearest(hours)}-hour storm")
    else:
        if isinstance(pattern, str) and pattern == "uniform":
            cum, pattern_text = pattern_steps([0.0, 1.0], [0.0, 1.0], n_rain), "uniform"
        elif isinstance(pattern, str):
            raise ValueError(f"pattern must be 'atlas14', 'uniform' or (times, shares), got {pattern!r}")
        else:
            cum, pattern_text = pattern_steps(*pattern, n_rain), "given"
        pattern_of = None
    target = spill_order(ids, deps["spill_to"].to_numpy())[1]
    sim, leaving = simulate(target, depth, cum, catch_area=catch, volume=deps["volume_m3"].to_numpy(float),
                            area=deps["area_m2"].to_numpy(float), max_depth=deps["max_depth_m"].to_numpy(float),
                            dt=dt, n_after=n_after, method=method, seepage_mm_h=seep, drain_mm_h=drain,
                            ponding_depth_m=ponding_depth_m, pattern_of=pattern_of, **kwargs)
    with np.errstate(invalid="ignore", divide="ignore"):
        runoff = 1000.0 * sim["own_m3"] / catch
    storm_years = _years(storms, depth, hours, lat, lon) if rarity else np.full(n, np.nan)
    table = pd.DataFrame({"id": ids, "storm_mm": depth, "storm_hours": float(hours),
                          "storm_years": storm_years, "runoff_mm": runoff,
                          **{c: sim[c] for c in STORM_COLUMNS[5:]}, **out_extra})
    table.attrs["settings"] = {
        "method": method, "condition": f"{condition} ({CONDITIONS[condition]})" if method == "curve-number"
        else condition, "hours": float(hours),
        "storm": f"NOAA Atlas 14, {years}-year" if years is not None else
                 ("given per point" if callable(depth_mm) else f"uniform {float(depth_mm):g} mm"),
        "pattern": pattern_text, "step_minutes": dt / 60.0, "after_hours": n_after * dt / 3600.0,
        "seepage": bool(seepage), "drains": drains, "ponding_depth_m": ponding_depth_m,
        "min_coverage": min_coverage, "water_left_table_m3": float(leaving),
        "inputs": {name: desc for name, (_, desc) in sources.items()},
        "package_version": _package_version()}
    return table


def _nearest(hours):
    from .sources.rainfall import nearest_temporal_hours

    return nearest_temporal_hours(hours)


def _years(storms, depth, hours, lat, lon):
    """Return period of each depression's storm at its lowest point; NaN where Atlas 14 cannot
    say (outside it, or a duration it does not cover)."""
    if len(depth) == 0:
        return np.zeros(0)
    try:
        return storms.return_period(depth, hours, lat, lon)
    except ValueError:
        return np.full(len(depth), np.nan)


def write_storm(folder, table, name):
    """Write a `storm_response` table as `storm_<name>.<format>` in a result folder (the format of
    its tables) and record its settings in meta.json under storms.<name>."""
    from pathlib import Path

    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("a storm name uses letters, digits, - and _ only")
    folder = Path(folder)
    fmt = table_path(folder, "depressions").suffix.lstrip(".")
    for other in ("csv", "parquet"):
        if other != fmt:
            (folder / f"storm_{name}.{other}").unlink(missing_ok=True)
    path = write_table(table, folder / f"storm_{name}.{fmt}", fmt)
    meta = read_meta(folder)
    meta.setdefault("storms", {})[name] = table.attrs.get("settings", {})
    write_meta(folder, meta)
    return path
