"""Check any result, from any place, without a reference answer.

    from h3lowpoints.verify import check_result, spot_check
    problems = check_result(res)              # [] when every invariant holds
    problems = spot_check("big_dem.tif")      # [] when tiled and whole runs agree on sample windows

    h3lowpoints verify results_folder         # the same for a saved result

`check_result` tests invariants that hold for every correct result, whatever the terrain: the
cell table adds up to the depression table, every lowest point is in a listed cell, resolutions
and coverage are consistent, values are finite and in range. `spot_check` cuts random windows from
a DEM and requires the whole-raster and tiled analyses to agree on each. Neither needs ground
truth, so both can be run on any region the library is used on.
"""
import tempfile
from pathlib import Path

import h3
import numpy as np
import rasterio
from rasterio.windows import Window

from .resolution import FINEST_H3
from .store import CHUNK_ROWS, ids_to_int, iter_table, missing_files, read_meta, table_path

REL = 1e-6        # relative tolerance for sums compared across tables (float summation order)


def _close(a, b, rel=REL):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)


def check_tables(depressions, cells, meta, catchments=None, depression_cells=None):
    """Problems found in a result given as its tables (empty list = all invariants hold)."""
    return _check([depressions], [cells], meta, None if catchments is None else [catchments],
                  None if depression_cells is None else [depression_cells])


class _Depressions:
    """What the checks need from the depression table, gathered one piece at a time.

    Every check on single rows runs on each piece; only what the checks across rows and tables
    need is kept (ids, totals, the lowest points' cells, and per depression the values the other
    tables must add up to), so the whole table never has to be held at once.
    """

    def __init__(self, meta):
        params = meta.get("params") or {}
        self.res, self.min_depth = params.get("res"), params.get("min_depth", 0.0)
        self.flags, self.counts, self.parts = set(), {}, {}
        self.n, self.area, self.volume, self.deepest = 0, 0.0, 0.0, -np.inf
        self.routing = self.details = False

    def _keep(self, name, values):
        self.parts.setdefault(name, []).append(np.asarray(values))

    def _count(self, name, k):
        self.counts[name] = self.counts.get(name, 0) + int(k)

    def add(self, d):
        if len(d) == 0:
            return
        from .hydro.details import AREA_DOWN, FILL_LEVELS, VOLUME_DOWN

        flag = self.flags.add
        self.n += len(d)
        self._keep("id", d["id"].to_numpy())
        depth = d["max_depth_m"].to_numpy(dtype=float)
        area = d["area_m2"].to_numpy(dtype=float)
        volume = d["volume_m3"].to_numpy(dtype=float)
        self.area += float(area.sum())
        self.volume += float(volume.sum())
        self.deepest = max(self.deepest, float(depth.max()))
        if not np.isfinite(d[["max_depth_m", "area_m2", "volume_m3", "lat", "lon"]].to_numpy(dtype=float)).all():
            flag("nonfinite")
        self._count("shallow", (depth < self.min_depth - 1e-9).sum())
        if (area <= 0).any():
            flag("area")
        if (volume < 0).any() or (volume > depth * area * (1 + REL)).any():
            flag("volume")
        if "deeper_than_wide" in d and (d["deeper_than_wide"].to_numpy(dtype=bool) != (depth > np.sqrt(area))).any():
            flag("deeper_than_wide")
        if not d["lat"].between(-90, 90).all() or not d["lon"].between(-180, 180).all():
            flag("latlon")
        low = d["h3_lowpoint"].tolist()
        valid = np.array([_valid(x) for x in low], dtype=bool)
        if self.res is not None:
            self._count("low_res", sum(1 for x, ok in zip(low, valid)
                                       if not ok or h3.get_resolution(x) != self.res))
        self._keep("low", ids_to_int([x for x, ok in zip(low, valid) if ok]))
        if "h3_lowpoint_15" in d:
            self._count("low15", sum(cell != h3.latlng_to_cell(la, lo, FINEST_H3) for cell, la, lo
                                     in zip(d["h3_lowpoint_15"], d["lat"], d["lon"])))
        if "catchment_area_m2" in d:                  # catchments=True
            self.routing = True
            catch = d["catchment_area_m2"].to_numpy(dtype=float)
            if not np.isfinite(d[["catchment_area_m2", "spill_lat", "spill_lon"]].to_numpy(dtype=float)).all():
                flag("routing nonfinite")
            if (catch < area * (1 - REL)).any():
                flag("catchment small")
            if not d["spill_lat"].between(-90, 90).all() or not d["spill_lon"].between(-180, 180).all():
                flag("spill latlon")
            to = d["spill_to"].to_numpy()
            if (to == d["id"].to_numpy()).any():
                flag("self spill")
            self._keep("spill_to", to)
            self._keep("catchment_area_m2", catch)
        if "water_level_m" in d:                      # details=True
            self.details = True
            cols = ["ground_m", "water_level_m", "length_m", "width_m", "orientation_deg"] + AREA_DOWN + VOLUME_DOWN
            if not np.isfinite(d[cols].to_numpy(dtype=float)).all():
                flag("details nonfinite")
            rise = (d["water_level_m"] - d["ground_m"]).to_numpy(dtype=float)
            if not np.allclose(rise, depth, rtol=1e-9, atol=1e-6):
                flag("rise")
            if ((d["width_m"] <= 0) | (d["width_m"] > d["length_m"] * (1 + REL))).any():
                flag("width")
            if not d["orientation_deg"].between(0, 180, inclusive="left").all():
                flag("orientation")
            curve_a = np.c_[area, d[AREA_DOWN].to_numpy(dtype=float)]
            curve_v = np.c_[volume, d[VOLUME_DOWN].to_numpy(dtype=float)]
            if (curve_a < 0).any() or (np.diff(curve_a, axis=1) > REL * curve_a[:, :1]).any():
                flag("area curve")
            if (curve_v < 0).any() or (np.diff(curve_v, axis=1) > REL * curve_v[:, :1] + 1e-9).any():
                flag("volume curve")
            for (t, _), a_name, v_name in zip(FILL_LEVELS, AREA_DOWN, VOLUME_DOWN):
                room = np.maximum(depth - t, 0.0)
                if (d[v_name].to_numpy(dtype=float) > room * d[a_name].to_numpy(dtype=float) * (1 + REL) + 1e-9).any():
                    flag(f"room {v_name}")
            for name in ["area_m2", "volume_m3", "max_depth_m"] + AREA_DOWN:
                self._keep(f"d_{name}", d[name].to_numpy(dtype=float))

    def array(self, name, dtype=np.float64):
        parts = self.parts.pop(name, [])
        return np.concatenate(parts) if parts else np.zeros(0, dtype=dtype)

    def problems(self, meta):
        out = []
        add = out.append
        if self.counts.get("shallow"):
            add(f"{self.counts['shallow']} depressions are shallower than min_depth")
        for key, message in (("nonfinite", "depressions hold NaN or infinite values"),
                             ("area", "depressions with zero or negative area"),
                             ("volume", "a volume is negative or larger than its deepest depth times its area"),
                             ("deeper_than_wide", "deeper_than_wide does not match the depths and areas"),
                             ("latlon", "a lowest point lies outside valid latitude/longitude")):
            if key in self.flags:
                add(message)
        if self.counts.get("low_res"):
            add(f"{self.counts['low_res']} h3_lowpoint ids are not at the table's resolution {self.res}")
        if self.counts.get("low15"):
            add(f"{self.counts['low15']} h3_lowpoint_15 ids do not contain their lowest point")
        return out


def _by_id(ids, chunks, columns, maximum=None):
    """Per depression (in the order of `ids`): sums of `columns` over a side table's rows, the
    largest value of `maximum`, and whether any row named an unknown depression."""
    order = np.argsort(ids)
    sums = {c: np.zeros(len(ids)) for c in columns}
    top = np.zeros(len(ids)) if maximum else None
    stray, bad_cells = False, False
    for c in chunks:
        if len(c) == 0:
            continue
        if not all(_valid(x) for x in c["h3"]):
            bad_cells = True
        at = np.minimum(np.searchsorted(ids[order], c["id"].to_numpy()), max(len(ids) - 1, 0))
        hit = ids[order][at] == c["id"].to_numpy()
        stray |= not hit.all()
        for name in columns:
            np.add.at(sums[name], order[at[hit]], c[name].to_numpy(dtype=float)[hit])
        if maximum:
            np.maximum.at(top, order[at[hit]], c[maximum].to_numpy(dtype=float)[hit])
    return sums, top, stray, bad_cells


def _check_routing(s, ids, catchment_chunks, meta):
    """Catchment and spill invariants (results computed with catchments=True)."""
    problems = []
    add = problems.append
    for key, message in (("routing nonfinite", "catchment or spill columns hold NaN or infinite values"),
                         ("catchment small", "a catchment is smaller than its own depression"),
                         ("spill latlon", "a spill point lies outside valid latitude/longitude"),
                         ("self spill", "a depression spills into itself")):
        if key in s.flags:
            add(message)
    to = s.array("spill_to", np.int64)
    catch = s.array("catchment_area_m2")
    order = np.argsort(ids)
    pos = np.minimum(np.searchsorted(ids[order], to), len(ids) - 1)
    known = (ids[order][pos] == to) & (to != 0)
    if not meta.get("clip") and ((to != 0) & ~known).any():
        add("spill_to names depressions that are not in the table")
    # following spill_to must end (at 0, or outside a clipped table) without going round in a loop
    nxt = np.where(known, order[pos], -1)
    del to, pos, known
    for _ in range(64):                          # pointer doubling: chains of 2**64 steps end
        nxt = np.where(nxt >= 0, nxt[np.maximum(nxt, 0)], -1)
    if (nxt >= 0).any():
        add("following spill_to goes round in a loop")
    del nxt
    if catchment_chunks is None:
        return problems
    sums, _, stray, bad_cells = _by_id(ids, catchment_chunks, ["area_m2"])
    if bad_cells:
        add("the catchment table holds ids that are not valid H3 cells")
    if stray:
        add("the catchment table names depressions that are not in the depression table")
    if not np.allclose(sums["area_m2"], catch, rtol=REL, atol=1e-6):
        add("the catchment table does not add up to catchment_area_m2")
    return problems


def _check_details(s, ids, chunks):
    """Level, shape and fill-curve invariants (results computed with details=True)."""
    from .hydro.details import AREA_DOWN, VOLUME_DOWN

    problems = []
    add = problems.append
    for key, message in (("details nonfinite", "level, shape or fill-curve columns hold NaN or infinite values"),
                         ("rise", "water_level_m - ground_m is not max_depth_m"),
                         ("width", "a width is not positive or exceeds its length"),
                         ("orientation", "an orientation lies outside [0, 180)"),
                         ("area curve", "the flooded area grows as the water drops"),
                         ("volume curve", "the volume grows as the water drops")):
        if key in s.flags:
            add(message)
    for v_name in VOLUME_DOWN:
        if f"room {v_name}" in s.flags:
            add(f"{v_name} is larger than the depth left times the area")
            break
    own = {name: s.array(f"d_{name}") for name in ["area_m2", "volume_m3", "max_depth_m"] + AREA_DOWN}
    if chunks is None:
        return problems
    sums, deepest, stray, _ = _by_id(ids, chunks, ["area_m2", "volume_m3"] + AREA_DOWN, "max_depth_m")
    if stray:
        add("the depression_cells table names depressions that are not in the depression table")
    for name, total in sums.items():
        if not np.allclose(total, own[name], rtol=REL, atol=1e-6):
            add(f"the depression_cells table does not add up to {name}")
    if not np.allclose(deepest, own["max_depth_m"], rtol=REL, atol=1e-9):
        add("the deepest depression_cells row is not the depression's max_depth_m")
    return problems


def _check(dep_chunks, cell_chunks, meta, catchment_chunks=None, detail_chunks=None):
    """The checks, with every table given as any number of pieces (read one at a time)."""
    res = (meta.get("params") or {}).get("res")
    s = _Depressions(meta)
    for d in dep_chunks:
        s.add(d)
    problems = []
    add = problems.append
    if meta.get("n_depressions") is not None and meta["n_depressions"] != s.n:
        add(f"meta says {meta['n_depressions']} depressions, the table has {s.n}")
    if s.n == 0:
        n_cells = sum(len(c) for c in cell_chunks)
        if n_cells:
            add(f"no depressions but {n_cells} cells")
        return problems

    # --- depressions
    ids = s.array("id", np.int64)
    if len(np.unique(ids)) != len(ids):
        add("depression ids are not unique")
    if s.routing:
        problems += _check_routing(s, ids, catchment_chunks, meta)
    if s.details:
        problems += _check_details(s, ids, detail_chunks)
    problems += s.problems(meta)
    del ids

    # --- cells, one piece at a time; only their ids (8 bytes per cell) are kept to the end
    low_ids, low_counts = np.unique(s.array("low", np.uint64), return_counts=True)
    n_valid_low = int(low_counts.sum())
    ids, flags = [], set()
    area = volume = 0.0
    deepest, n_low, n_low_found, has_counts = -np.inf, 0, 0, False
    max_dep = s.deepest
    for c in cell_chunks:
        if len(c) == 0:
            continue
        valid = np.array([_valid(x) for x in c["h3"]])
        if not valid.all():
            flags.add("invalid")
        cell = ids_to_int(["0" if not ok else x for x, ok in zip(c["h3"], valid)])
        ids.append(cell)
        if res is not None and ((cell >> np.uint64(52)) & np.uint64(15) != res)[valid].any():
            flags.add("res")
        area += float(c["depress_area_m2"].sum())
        volume += float(c["depress_volume_m3"].sum())
        deepest = max(deepest, float(c["max_depth_m"].max()))
        if (c["max_depth_m"] > max_dep * (1 + REL)).any():
            flags.add("too deep")
        if "n_lowpoints" in c:
            has_counts = True
            n = c["n_lowpoints"].to_numpy()
            n_low += int(n.sum())
            if len(low_ids):
                at = np.minimum(np.searchsorted(low_ids, cell), len(low_ids) - 1)
                expected = np.where(low_ids[at] == cell, low_counts[at], 0)
            else:
                expected = np.zeros(len(cell), dtype=np.int64)
            n_low_found += int(expected.sum())
            if (n != expected).any():
                flags.add("n_lowpoints")
        if "coverage" in c:
            cov = c["coverage"]
            if not cov.between(0, 1).all() or (cov <= 0).any():
                flags.add("coverage range")
            nominal = np.array([h3.cell_area(x, unit="m^2") if ok else np.nan
                                for x, ok in zip(c["h3"], valid)])
            if (c["covered_area_m2"] > nominal * (1 + REL)).any():
                flags.add("covered too big")
            if not np.allclose(c["covered_area_m2"][valid], (c["coverage"] * nominal)[valid], rtol=1e-6):
                flags.add("covered not coverage")
        if "depressed_share" in c and not c["depressed_share"].between(0, 1).all():
            flags.add("share")
    ids = np.concatenate(ids) if ids else np.zeros(0, dtype=np.uint64)

    if "invalid" in flags:
        add("the cell table holds ids that are not valid H3 cells")
    if len(np.unique(ids)) != len(ids):
        add("cell ids are not unique")
    if "res" in flags:
        add(f"cells are not all at resolution {res}")
    # a clipped result keeps, for depressions across the boundary, only their cells inside it, so
    # the two tables' totals no longer match; every other invariant still holds
    if not meta.get("clip"):
        if not _close(area, s.area):
            add(f"cell areas add to {area:.6g} m2, depressions to {s.area:.6g}")
        if not _close(volume, s.volume):
            add(f"cell volumes add to {volume:.6g} m3, depressions to {s.volume:.6g}")
        if not _close(deepest, max_dep):
            add("the deepest cell and the deepest depression differ")
        if "too deep" in flags:
            add("a cell is deeper than every depression")
    if has_counts:
        if n_low != s.n:
            add(f"n_lowpoints adds to {n_low}, not {s.n} depressions")
        if n_low_found < n_valid_low:
            add(f"{n_valid_low - n_low_found} lowest points lie in cells missing from the cell table")
        elif "n_lowpoints" in flags:
            add("n_lowpoints does not match the lowest points in each cell")
    if "coverage range" in flags:
        add("coverage outside (0, 1]")
    if "covered too big" in flags:
        add("a covered area is larger than its H3 cell")
    if "covered not coverage" in flags:
        add("covered_area_m2 is not coverage times the cell's area")
    if "share" in flags:
        add("depressed_share outside [0, 1]")
    return problems


def _valid(cell):
    return isinstance(cell, str) and h3.is_valid_cell(cell)


def check_result(result):
    """Problems found in a `Result` (empty list = all invariants hold).

    A result written to disk (`analyze_tiled(out_dir=...)`) is checked from its files.
    """
    if result.depressions is None and result.path is not None:
        return check_saved(result.path)
    return check_tables(result.depressions, result.cells, result.meta, result.catchments,
                        result.depression_cells)


def check_saved(folder, rows=CHUNK_ROWS):
    """Problems found in a saved result folder (CSV or Parquet), reading the cells in pieces."""
    missing = missing_files(folder)
    if missing:
        return [f"{folder} is missing {', '.join(missing)}"]
    optional = [iter_table(folder, name, rows=rows) if table_path(folder, name) else None
                for name in ("catchments", "depression_cells")]
    return _check(iter_table(folder, "depressions", rows=rows), iter_table(folder, "cells", rows=rows),
                  read_meta(folder), *optional)


def compare_results(a, b, rel=1e-9):
    """Differences between two results of the same DEM (empty list = the same depressions and cells)."""
    out = []
    key = ["lat", "lon"]
    da = a.depressions.round({"lat": 7, "lon": 7}).sort_values(key).reset_index(drop=True)
    db = b.depressions.round({"lat": 7, "lon": 7}).sort_values(key).reset_index(drop=True)
    if len(da) != len(db):
        return [f"{len(da)} depressions against {len(db)}"]
    if len(da) == 0:
        return [] if len(a.cells) == len(b.cells) == 0 else ["no depressions, but the cell tables differ"]
    for col in ("max_depth_m", "area_m2", "volume_m3"):
        if not np.allclose(da[col], db[col], rtol=rel, atol=1e-9):
            out.append(f"{col} differs")
    if not (da[key].to_numpy() == db[key].to_numpy()).all():
        out.append("lowest points differ")
    ca, cb = a.cells.sort_values("h3").reset_index(drop=True), b.cells.sort_values("h3").reset_index(drop=True)
    if list(ca["h3"]) != list(cb["h3"]):
        out.append("the cell tables list different cells")
    else:
        for col in ("max_depth_m", "depress_area_m2", "depress_volume_m3"):
            if not np.allclose(ca[col], cb[col], rtol=rel, atol=1e-9):
                out.append(f"cell {col} differs")
    return out


def spot_check(dem_path, *, windows=3, size=600, tile=170, seed=0, **params):
    """Cut `windows` random size x size windows from a DEM and require tiled == whole on each.

    Extra keyword arguments go to both analyses (res, min_depth, ...). Returns a list of
    problems, each naming its window. Works on any DEM, including a VRT of remote tiles.
    """
    from .pipeline import analyze
    from .sources._common import gdal_env
    from .tiling import analyze_tiled

    rng = np.random.default_rng(seed)
    problems = []
    with gdal_env(), rasterio.open(dem_path) as src, tempfile.TemporaryDirectory() as tmp:
        h, w = src.height, src.width
        s = min(size, h, w)
        for k in range(windows):
            r0, c0 = int(rng.integers(0, h - s + 1)), int(rng.integers(0, w - s + 1))
            win = Window(c0, r0, s, s)
            profile = src.profile.copy()
            profile.update(driver="GTiff", width=s, height=s, transform=src.window_transform(win),
                           tiled=False, compress=None)
            for name in ("blockxsize", "blockysize", "interleave"):
                profile.pop(name, None)
            path = Path(tmp) / f"window_{k}.tif"
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(src.read(1, window=win), 1)
            whole = analyze(str(path), **params)
            tiled = analyze_tiled(str(path), tile=tile, **params)
            for p in compare_results(whole, tiled) + [f"whole: {x}" for x in check_result(whole)] + \
                    [f"tiled: {x}" for x in check_result(tiled)]:
                problems.append(f"window {k} (row {r0}, col {c0}): {p}")
    return problems
