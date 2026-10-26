"""How easily each depression fills: the rain reaching it compared with the water it can hold.

    from h3lowpoints.susceptibility import susceptibility, write_susceptibility
    table = susceptibility("results", impervious="imperv.tif", rain="atlas14")
    write_susceptibility("results", table)          # susceptibility.<csv|parquet> + meta.json

    h3lowpoints index results --impervious imperv.tif --rain atlas14

Needs a result computed with catchments=True: each depression's catchment (the ground whose rain
ends up in it), its volume, and the depression it overflows into (`spill_to`).

- Runoff. Of the rain on a catchment, a share runs off: the runoff coefficient. From an
  imperviousness raster (percent paved or built, such as NLCD), each H3 cell's coefficient is
  0.05 + 0.009 x its mean imperviousness (Schueler's Simple Method); or one coefficient is given
  for everything. A depression's runoff area is its catchment area times the catchment's mean
  coefficient: the rain depth times it is the runoff.
- Rain to fill. `rain_to_fill_mm` is the uniform rain depth that fills a depression from its own
  catchment alone (volume / runoff area). `rain_to_fill_upstream_mm` also counts the overflow of
  the depressions upstream that fill first: it is never larger.
- Storms. With design storms (NOAA Atlas 14, or depths given), each depression's own runoff comes
  from the rain on each cell of its catchment, and overflow passes down the spill links
  (`hydro.cascade`). `fill_ratio_<N>yr` is the water reaching it over its volume (1 or more: it
  fills), and `fills_in_years` the smallest return period whose storm fills it (inf: none does).

Where an input does not cover enough of a catchment (`min_coverage`, by area for imperviousness,
by runoff for rain), the columns that need it are NaN, and so are the storm columns of every
depression downstream. Nothing here knows about drains, pipes or soils beyond the coefficient;
see docs/susceptibility.md for the method, its evidence and its limits.
"""
from pathlib import Path

import h3
import h3.api.basic_int as h3i
import numpy as np
import pandas as pd

from .hydro.cascade import cascade, rain_to_fill_upstream, spill_order
from .pipeline import Result, _package_version
from .store import (CHUNK_ROWS, ids_to_int, iter_file, iter_table, missing_files, read_meta,
                    table_path, write_meta, write_table)

SIMPLE_METHOD = (0.05, 0.009)  # runoff coefficient = 0.05 + 0.009 x impervious percent
RUNOFF_RULE = "Schueler's Simple Method: runoff coefficient = 0.05 + 0.009 x impervious percent"
BASE_COLUMNS = ["id", "impervious_pct", "runoff_coef", "runoff_area_m2", "rain_to_fill_mm",
                "rain_to_fill_upstream_mm"]
RATIO_YEARS = (2, 10, 100)
UPSTREAM_REL = 0.01           # rain_to_fill_upstream_mm is exact to within this share
RASTER_SUFFIXES = (".tif", ".tiff", ".vrt", ".img")


def runoff_coefficient(impervious_pct):
    """Share of the rain that runs off, from the percent of the ground that is impervious."""
    base, slope = SIMPLE_METHOD
    return base + slope * np.asarray(impervious_pct, dtype=float)


def cell_means(raster, res, *, parent=None, rows=1024, workers=1):
    """Mean of a raster's values on each H3 cell at resolution `res`.

    Each pixel belongs to the cell containing its centre; pixels with no data are left out.
    `parent` (an H3 cell, coarser than `res`) keeps only the cells inside it, so a large area can
    be done in pieces whose cells are complete. Returns a table sorted by cell: `h3`, `mean` and
    `n_pixels`.
    """
    import rasterio
    from rasterio.windows import Window

    from .h3agg.aggregate import h3_ids, lonlat, pixel_centers

    keep = None if parent is None else (h3.str_to_int(parent), h3.get_resolution(parent))
    parts = []
    with rasterio.open(raster) as src:
        for r0 in range(0, src.height, rows):
            n = min(rows, src.height - r0)
            band = src.read(1, window=Window(0, r0, src.width, n), masked=True)
            rr, cc = np.nonzero(~np.ma.getmaskarray(band))
            if len(rr) == 0:
                continue
            lon, lat = lonlat(src.crs, *pixel_centers(src.transform, rr + r0, cc))
            cell = h3_ids(lat, lon, res, workers)
            values = band.data[rr, cc].astype(float)
            if keep is not None:
                distinct = np.unique(cell)
                inside = np.array([h3i.cell_to_parent(int(c), keep[1]) == keep[0] for c in distinct],
                                  dtype=bool)
                hit = np.isin(cell, distinct[inside])
                cell, values = cell[hit], values[hit]
            frame = pd.DataFrame({"cell": cell, "value": values})
            parts.append(frame.groupby("cell")["value"].agg(["sum", "count"]))
    if not parts:
        return pd.DataFrame({"h3": pd.Series(dtype=object), "mean": pd.Series(dtype=float),
                             "n_pixels": pd.Series(dtype="int64")})
    sums = pd.concat(parts).groupby(level=0).sum().sort_index()
    return pd.DataFrame({"h3": [h3.int_to_str(int(c)) for c in sums.index],
                         "mean": sums["sum"].to_numpy() / sums["count"].to_numpy(),
                         "n_pixels": sums["count"].to_numpy(dtype="int64")})


class _InMemory:
    """Per-cell values from a table held in memory."""

    def __init__(self, cells, values):
        order = np.argsort(cells)
        self.keys, self.values = cells[order], values[order]

    def lookup(self, cells):
        if len(self.keys) == 0:
            return np.full(len(cells), np.nan)
        at = np.minimum(np.searchsorted(self.keys, cells), len(self.keys) - 1)
        return np.where(self.keys[at] == cells, self.values[at], np.nan)


class _Streamed:
    """Per-cell values read from a file sorted by cell, as the catchment table (also sorted by
    cell) is read: only the rows between the cells asked for so far are held."""

    def __init__(self, path, column, rows):
        self.chunks = iter_file(path, rows, columns=["h3", column])
        self.path, self.column = path, column
        self.keys, self.values = np.zeros(0, dtype=np.uint64), np.zeros(0)
        self.done, self.last = False, None

    def lookup(self, cells):
        # rows of one cell may continue into the next piece, so a piece may start at the last
        # cell of the one before, never before it
        if self.last is not None and cells[0] < self.last:
            raise ValueError("the catchment table is not ordered by cell; give the per-cell table "
                             "in memory (a DataFrame) instead of a file")
        self.last = cells[-1]
        start = np.searchsorted(self.keys, cells[0])
        self.keys, self.values = self.keys[start:], self.values[start:]
        while not self.done and (len(self.keys) == 0 or self.keys[-1] < cells[-1]):
            chunk = next(self.chunks, None)
            if chunk is None:
                self.done = True
                break
            keys = ids_to_int(list(chunk["h3"]))
            if len(keys) and ((np.diff(keys.astype(np.int64)) < 0).any()
                              or (len(self.keys) and keys[0] < self.keys[-1])):
                raise ValueError(f"{self.path} is not sorted by h3")
            self.keys = np.concatenate([self.keys, keys])
            self.values = np.concatenate([self.values, chunk[self.column].to_numpy(dtype=float)])
        return _InMemory(self.keys, self.values).lookup(cells)


def _impervious_source(impervious, res, rows, workers):
    """(lookup, description) for the `impervious` argument of `susceptibility`."""
    if isinstance(impervious, pd.DataFrame):
        table = impervious
    else:
        path = Path(impervious)
        if path.suffix.lower() in RASTER_SUFFIXES:
            table = cell_means(path, res, workers=workers).rename(columns={"mean": "impervious_pct"})
            return (_InMemory(ids_to_int(list(table["h3"])), table["impervious_pct"].to_numpy(float)),
                    f"raster {path.name}, mean per H3 cell")
        if path.suffix.lower() in (".csv", ".parquet"):
            return _Streamed(path, "impervious_pct", rows), f"per-cell table {path.name}"
        raise ValueError(f"impervious must be a raster ({', '.join(RASTER_SUFFIXES)}), a .csv or "
                         f".parquet table of h3 and impervious_pct, or a DataFrame; got {impervious}")
    if not {"h3", "impervious_pct"} <= set(table.columns):
        raise ValueError("a per-cell imperviousness table needs the columns h3 and impervious_pct")
    return (_InMemory(ids_to_int(list(table["h3"])), table["impervious_pct"].to_numpy(dtype=float)),
            "per-cell table")


def _storms(rain, cache_dir):
    """(keys, samplers, description): each sampler gives mm at (lat, lon), or is a number."""
    if rain is None:
        return [], [], None
    if isinstance(rain, str):
        if rain != "atlas14":
            raise ValueError(f"rain must be None, 'atlas14', a depth in mm or a dict, got {rain!r}")
        from .sources.rainfall import ATLAS14_YEARS, Atlas14Grids

        grids = Atlas14Grids(ATLAS14_YEARS, "24h", cache_dir=cache_dir)
        samplers = [lambda lat, lon, y=y: grids.depth(y, lat, lon) for y in ATLAS14_YEARS]
        return list(ATLAS14_YEARS), samplers, "NOAA Atlas 14, 24-hour storms (grids)"
    if isinstance(rain, (int, float)):
        if not rain > 0:
            raise ValueError("a rain depth must be positive (mm)")
        return [None], [float(rain)], f"uniform {float(rain):g} mm"
    if isinstance(rain, dict) and rain:
        keys = list(rain)
        if not all(isinstance(k, int) and k > 0 for k in keys):
            raise ValueError("the keys of a rain dict are return periods in years (positive integers)")
        for v in rain.values():
            if not callable(v) and not (isinstance(v, (int, float)) and v > 0):
                raise ValueError("each storm is a depth in mm or a function of (lat, lon) giving mm")
        order = sorted(keys)
        return order, [rain[k] for k in order], "given per storm"
    raise ValueError(f"rain must be None, 'atlas14', a depth in mm or a dict, got {rain!r}")


def _tables(result, rows):
    """(depressions, catchment chunks, meta) of a result in memory or a folder."""
    columns = ["id", "volume_m3", "catchment_area_m2", "spill_to"]
    if isinstance(result, Result) and result.depressions is not None:
        deps, meta = result.depressions, result.meta
        if result.catchments is None or "spill_to" not in deps:
            raise ValueError("susceptibility needs a result computed with catchments=True")
        return deps[columns], (lambda: [result.catchments]), meta
    folder = Path(result.path if isinstance(result, Result) else result)
    missing = missing_files(folder)
    if missing:
        raise FileNotFoundError(f"{folder} is missing {', '.join(missing)}")
    if table_path(folder, "catchments") is None:
        raise ValueError("susceptibility needs a result computed with catchments=True "
                         f"({folder} has no catchments table)")
    deps = pd.concat(list(iter_table(folder, "depressions", rows, columns=columns)), ignore_index=True)
    return deps, (lambda: iter_table(folder, "catchments", rows)), read_meta(folder)


def susceptibility(result, *, impervious=None, runoff_coef=None, rain=None, ratio_years=RATIO_YEARS,
                   min_coverage=0.9, cache_dir=None, rows=CHUNK_ROWS, workers=1):
    """One row per depression: its runoff, the rain that fills it and, with storms, how full they
    leave it. See the module text and docs/susceptibility.md.

    result        a `Result` computed with catchments=True, or its folder
    impervious    percent impervious (0-100): a raster in any CRS (its mean per H3 cell is used),
                  a .csv or .parquet table with columns h3 and impervious_pct at the result's
                  resolution (a file is read in pieces and must be sorted by h3), or a DataFrame
    runoff_coef   instead of `impervious`: one runoff coefficient (0 to 1) for all ground
    rain          None (no storms), "atlas14" (24-hour storms of 1 to 1000 years from NOAA
                  Atlas 14 grids, US only), a depth in mm (one uniform storm, column
                  `fill_ratio`), or a dict {return period in years: mm or function(lat, lon) -> mm}
    ratio_years   the return periods given a `fill_ratio_<N>yr` column (all storms count for
                  `fills_in_years`)
    min_coverage  share of a catchment an input must cover for the columns that need it
    cache_dir     keep downloaded rainfall grids here
    rows          rows of the catchment table read at a time
    workers       processes for labelling raster pixels with H3 cells
    """
    if (impervious is None) == (runoff_coef is None):
        raise ValueError("give either impervious (a raster or per-cell table) or runoff_coef")
    if runoff_coef is not None and not 0 < runoff_coef <= 1:
        raise ValueError("runoff_coef must be in (0, 1]")
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")
    deps, catchment_chunks, meta = _tables(result, rows)
    res = meta["params"]["res"]
    lookup, imp_text = (_impervious_source(impervious, res, rows, workers) if impervious is not None
                        else (None, None))
    keys, samplers, rain_text = _storms(rain, cache_dir)
    n, n_storms = len(deps), len(keys)
    ids = deps["id"].to_numpy(dtype=np.int64)
    by_id = np.argsort(ids, kind="stable")
    volume = deps["volume_m3"].to_numpy(dtype=float)
    catch = deps["catchment_area_m2"].to_numpy(dtype=float)

    area_imp, sum_imp = np.zeros(n), np.zeros(n)               # impervious data: area and a x I
    weight, weighted = np.zeros((n, n_storms)), np.zeros((n, n_storms))   # sum a x C, a x C x P
    for chunk in catchment_chunks():
        if len(chunk) == 0:
            continue
        at = np.minimum(np.searchsorted(ids[by_id], chunk["id"].to_numpy()), max(n - 1, 0))
        hit = ids[by_id][at] == chunk["id"].to_numpy() if n else np.zeros(len(chunk), bool)
        pos, area = by_id[at[hit]], chunk["area_m2"].to_numpy(dtype=float)[hit]
        cells, inverse = np.unique(ids_to_int(list(chunk["h3"][hit])), return_inverse=True)
        if lookup is not None:
            imp = lookup.lookup(cells)[inverse] if len(cells) else np.zeros(0)
            known = ~np.isnan(imp)
            coef = np.where(known, runoff_coefficient(np.where(known, imp, 0.0)), np.nan)
        else:
            known = np.ones(len(area), dtype=bool)
            imp, coef = np.full(len(area), np.nan), np.full(len(area), float(runoff_coef))
        rain_cells = _rain_on(cells, samplers) if n_storms else None
        u, inv = np.unique(pos, return_inverse=True)

        def add(target, values, col=None):
            sums = np.bincount(inv, weights=values, minlength=len(u))
            if col is None:
                target[u] += sums
            else:
                target[u, col] += sums
        if lookup is not None:
            add(area_imp, np.where(known, area, 0.0))
            add(sum_imp, np.where(known, area * np.nan_to_num(imp), 0.0))
        for s in range(n_storms):
            p = rain_cells[s][inverse]
            ok = known & ~np.isnan(p)
            add(weight, np.where(ok, area * np.nan_to_num(coef), 0.0), s)
            add(weighted, np.where(ok, area * np.nan_to_num(coef) * np.nan_to_num(p), 0.0), s)

    with np.errstate(invalid="ignore", divide="ignore"):
        if lookup is not None:
            enough = area_imp >= min_coverage * catch
            imp_pct = np.where(enough, sum_imp / area_imp, np.nan)
            coef = runoff_coefficient(imp_pct)
            known_weight = SIMPLE_METHOD[0] * area_imp + SIMPLE_METHOD[1] * sum_imp   # sum a x C
        else:
            imp_pct, coef = np.full(n, np.nan), np.full(n, float(runoff_coef))
            known_weight = coef * catch
        runoff_area = coef * catch
        out = pd.DataFrame({"id": ids, "impervious_pct": imp_pct, "runoff_coef": coef,
                            "runoff_area_m2": runoff_area,
                            "rain_to_fill_mm": 1000.0 * volume / runoff_area})
        order, target = spill_order(ids, deps["spill_to"].to_numpy())
        out["rain_to_fill_upstream_mm"] = 1000.0 * rain_to_fill_upstream(
            order, target, runoff_area, volume, UPSTREAM_REL)
        if n_storms:
            covered = weight >= min_coverage * known_weight[:, None]
            mean_rain = np.where(covered, weighted / weight, np.nan)          # mm
            own = runoff_area[:, None] * mean_rain / 1000.0                   # m3
            inflow, _ = cascade(order, target, own, volume)
            ratio = inflow / volume[:, None]
            if keys == [None]:
                out["fill_ratio"] = ratio[:, 0]
            else:
                for y in ratio_years:
                    if y in keys:
                        out[f"fill_ratio_{y}yr"] = ratio[:, keys.index(y)]
                fills = ratio >= 1
                first = np.where(fills.any(axis=1), np.asarray(keys, float)[np.argmax(fills, axis=1)],
                                 np.inf)
                # unknown when no storm is known, or when none known fills it and some are unknown
                unknown = np.isnan(ratio).any(axis=1) & ~fills.any(axis=1)
                out["fills_in_years"] = np.where(unknown, np.nan, first)
    out.attrs["settings"] = {
        "runoff": RUNOFF_RULE if lookup is not None else f"one runoff coefficient, {runoff_coef:g}",
        "impervious": imp_text, "rain": rain_text,
        "years": [k for k in keys if k is not None], "ratio_years": [y for y in ratio_years if y in keys],
        "min_coverage": min_coverage, "upstream_rel": UPSTREAM_REL,
        "package_version": _package_version()}
    return out


def _rain_on(cells, samplers):
    """Rain in mm on each cell (by its centre) for each storm."""
    if all(not callable(s) for s in samplers):
        return [np.full(len(cells), float(s)) for s in samplers]
    centres = np.array([h3.cell_to_latlng(h3.int_to_str(int(c))) for c in cells]).reshape(-1, 2)
    return [np.asarray(s(centres[:, 0], centres[:, 1]), dtype=float) if callable(s)
            else np.full(len(cells), float(s)) for s in samplers]


def write_susceptibility(folder, table):
    """Write `table` (from `susceptibility`) into a result folder as `susceptibility.<format>`, in
    the format of the folder's tables, and record its settings in meta.json."""
    folder = Path(folder)
    fmt = table_path(folder, "depressions").suffix.lstrip(".")
    for other in ("csv", "parquet"):
        if other != fmt:
            (folder / f"susceptibility.{other}").unlink(missing_ok=True)
    write_table(table, folder / f"susceptibility.{fmt}", fmt)
    meta = read_meta(folder)
    meta["susceptibility"] = dict(table.attrs.get("settings", {}))
    write_meta(folder, meta)
    return folder / f"susceptibility.{fmt}"
