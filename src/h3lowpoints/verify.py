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
from .store import CHUNK_ROWS, ids_to_int, iter_table, missing_files, read_meta, read_table

REL = 1e-6        # relative tolerance for sums compared across tables (float summation order)


def _close(a, b, rel=REL):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)


def check_tables(depressions, cells, meta):
    """Problems found in a result given as its three tables (empty list = all invariants hold)."""
    return _check(depressions, [cells], meta)


def _check(d, cell_chunks, meta):
    """The checks, with the cell table given as any number of pieces (read one at a time)."""
    problems = []
    res = (meta.get("params") or {}).get("res")
    min_depth = (meta.get("params") or {}).get("min_depth", 0.0)
    add = problems.append

    if meta.get("n_depressions") is not None and meta["n_depressions"] != len(d):
        add(f"meta says {meta['n_depressions']} depressions, the table has {len(d)}")
    if len(d) == 0:
        n_cells = sum(len(c) for c in cell_chunks)
        if n_cells:
            add(f"no depressions but {n_cells} cells")
        return problems

    # --- depressions
    if d["id"].duplicated().any():
        add("depression ids are not unique")
    numbers = d[["max_depth_m", "area_m2", "volume_m3", "lat", "lon"]].to_numpy(dtype=float)
    if not np.isfinite(numbers).all():
        add("depressions hold NaN or infinite values")
    if (d["max_depth_m"] < min_depth - 1e-9).any():
        add(f"{int((d['max_depth_m'] < min_depth - 1e-9).sum())} depressions are shallower than min_depth")
    if (d["area_m2"] <= 0).any():
        add("depressions with zero or negative area")
    if (d["volume_m3"] < 0).any() or (d["volume_m3"] > d["max_depth_m"] * d["area_m2"] * (1 + REL)).any():
        add("a volume is negative or larger than its deepest depth times its area")
    if "deeper_than_wide" in d:
        want = d["max_depth_m"].to_numpy(dtype=float) > np.sqrt(d["area_m2"].to_numpy(dtype=float))
        if (d["deeper_than_wide"].to_numpy(dtype=bool) != want).any():
            add("deeper_than_wide does not match the depths and areas")
    if not d["lat"].between(-90, 90).all() or not d["lon"].between(-180, 180).all():
        add("a lowest point lies outside valid latitude/longitude")
    if res is not None:
        wrong = [x for x in d["h3_lowpoint"] if not _valid(x) or h3.get_resolution(x) != res]
        if wrong:
            add(f"{len(wrong)} h3_lowpoint ids are not at the table's resolution {res}")
    if "h3_lowpoint_15" in d:
        mismatched = sum(cell != h3.latlng_to_cell(la, lo, FINEST_H3)
                         for cell, la, lo in zip(d["h3_lowpoint_15"], d["lat"], d["lon"]))
        if mismatched:
            add(f"{mismatched} h3_lowpoint_15 ids do not contain their lowest point")

    # --- cells, one piece at a time; only their ids (8 bytes per cell) are kept to the end
    valid_low = [x for x in d["h3_lowpoint"] if _valid(x)]
    low_ids, low_counts = np.unique(ids_to_int(valid_low), return_counts=True)
    ids, flags = [], set()
    area = volume = 0.0
    deepest, n_low, n_low_found, has_counts = -np.inf, 0, 0, False
    max_dep = d["max_depth_m"].max()
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
        if not _close(area, d["area_m2"].sum()):
            add(f"cell areas add to {area:.6g} m2, depressions to {d['area_m2'].sum():.6g}")
        if not _close(volume, d["volume_m3"].sum()):
            add(f"cell volumes add to {volume:.6g} m3, depressions to {d['volume_m3'].sum():.6g}")
        if not _close(deepest, max_dep):
            add("the deepest cell and the deepest depression differ")
        if "too deep" in flags:
            add("a cell is deeper than every depression")
    if has_counts:
        if n_low != len(d):
            add(f"n_lowpoints adds to {n_low}, not {len(d)} depressions")
        if n_low_found < len(valid_low):
            add(f"{len(valid_low) - n_low_found} lowest points lie in cells missing from the cell table")
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
    return check_tables(result.depressions, result.cells, result.meta)


def check_saved(folder, rows=CHUNK_ROWS):
    """Problems found in a saved result folder (CSV or Parquet), reading the cells in pieces."""
    missing = missing_files(folder)
    if missing:
        return [f"{folder} is missing {', '.join(missing)}"]
    return _check(read_table(folder, "depressions"), iter_table(folder, "cells", rows=rows),
                  read_meta(folder))


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
