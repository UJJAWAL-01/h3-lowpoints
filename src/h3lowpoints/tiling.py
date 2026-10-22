"""Analyze a DEM tile by tile and get exactly the result of analyzing it whole.

`analyze_tiled` fills the DEM in two passes over the tiles (see `hydro/tiled.py` for the method),
labels the depressions inside each tile, and joins the pieces of depressions that cross tile
borders. Memory is one tile, a graph over the tile-border cells and a summary of each depression,
so a raster far larger than memory can be analyzed. With `out_dir` the tables are written to disk
piece by piece instead of returned, and the per-cell sums wait on disk, so the size of the output
does not limit the size of the region either. The result equals `analyze` on the same DEM, to the
last digit of every depth and to rounding in area and volume sums; `tests/test_tiling.py` checks
that on random terrain, nodata, flats, mazes and geographic grids.
"""
import time
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .context.buildings import rasterize_buildings
from .context.drains import drain_pixels, near_drain_mask
from .coverage import (Footprint, file_opener, invalid_block_flags, pack_invalid, tile_opener,
                       unpack_invalid)
from .geometry import crs_info, nearest_distance_m, pixel_areas_m2
from .h3agg.aggregate import h3_ids, lonlat, pixel_centers
from .hydro.depressions import EIGHT
from .hydro.tiled import (flood_from_seeds, neighbour_pairs, region_edges, solve_levels,
                          tile_seeds)
from .hydro.routing import _distances, pixel_spacing_m
from .hydro.tiled_routing import (E_FLAT, E_FP, E_HALO, E_KEPT, E_NONE, E_OUT, E_SINK, OUT_CODE,
                                  Ends, collapse, decode, encode, join_surfaces,
                                  settle_flats, tile_route)
from .pipeline import (CATCHMENT_TABLE, CELL_TABLE, Result, add_coverage, base_meta,
                       deeper_than_wide, dep_columns, empty_catchments, empty_cells,
                       empty_depressions, plan_analysis)
from .resolution import FINEST_H3, metres_to_cells
from .sources._common import gdal_env
from .store import (CHUNK_ROWS, CellParts, Checkpoints, Paused, TableWriter, check_format,
                    remove_other_formats, write_meta)

_TIE = 1e-9  # depths this close to the maximum count as tied for the lowest point


def _tile_boxes(height, width, tile):
    for r0 in range(0, height, tile):
        for c0 in range(0, width, tile):
            yield r0, min(r0 + tile, height), c0, min(c0 + tile, width)


def _bounds(transform, shape):
    h, w = shape
    left, top = transform.c, transform.f
    right, bottom = left + w * transform.a, top + h * transform.e
    return left, min(bottom, top), right, max(bottom, top)


def _buildings_in(buildings, bounds):
    if buildings is None or not hasattr(buildings, "cx"):
        return buildings
    left, bottom, right, top = bounds
    return buildings.cx[left:right, bottom:top]


def _read_tile(src, mask_src, box):
    """One tile as (elevation with invalid cells set to 0, valid mask)."""
    r0, r1, c0, c1 = box
    window = Window(c0, r0, c1 - c0, r1 - r0)
    arr = src.read(1, window=window)
    invalid = np.isnan(arr)                 # on the native dtype, exactly as analyze() does
    if src.nodata is not None:
        invalid |= arr == src.nodata
    if mask_src is not None:
        invalid |= mask_src.read(1, window=window).astype(bool)
    return np.where(invalid, 0.0, arr.astype(np.float64)), ~invalid


def _runs(*keys):
    """Start index of each run of equal keys (the arrays must already be sorted by them)."""
    change = np.zeros(len(keys[0]), dtype=bool)
    change[0] = True
    for k in keys:
        change[1:] |= k[1:] != k[:-1]
    return np.flatnonzero(change)


def _cell_summary(rec, cell, depth, area, volume):
    """Deepest depth and summed area and volume per (piece, H3 cell), as plain arrays."""
    order = np.lexsort((cell, rec))
    rec, cell = rec[order], cell[order]
    start = _runs(rec, cell)
    return (rec[start], cell[start], np.maximum.reduceat(depth[order], start),
            np.add.reduceat(area[order], start), np.add.reduceat(volume[order], start))


def _cell_chunks(parts, comp, kept, lowpoints, footprint, rows=CHUNK_ROWS):
    """The cell table, in pieces of about `rows` cells in ascending id order.

    One partition of `parts` is loaded at a time; sums from pieces of depressions that were
    dropped (`kept[comp[rec]]` is False) are left out. `lowpoints` are the integer ids of the
    depressions' lowest-point cells, counted into `n_lowpoints`.
    """
    low_ids, low_counts = np.unique(lowpoints, return_counts=True)
    pending, n_pending = [], 0
    for key in parts.keys():
        arr = parts.load(key)
        arr = arr[kept[comp[arr["rec"]]]]
        if len(arr) == 0:
            continue
        order = np.argsort(arr["cell"], kind="stable")
        cell = arr["cell"][order]
        start = _runs(cell)
        cell = cell[start]
        at = np.minimum(np.searchsorted(low_ids, cell), max(len(low_ids) - 1, 0))
        n_low = (np.where(low_ids[at] == cell, low_counts[at], 0) if len(low_ids)
                 else np.zeros(len(cell), dtype=np.int64))
        pending.append(pd.DataFrame({
            "h3": [h3.int_to_str(int(c)) for c in cell],
            "max_depth_m": np.maximum.reduceat(arr["depth"][order], start),
            "depress_area_m2": np.add.reduceat(arr["area"][order], start),
            "depress_volume_m3": np.add.reduceat(arr["volume"][order], start),
            "n_lowpoints": n_low.astype(np.int64)}))
        n_pending += len(cell)
        if n_pending >= rows:
            yield add_coverage(pd.concat(pending, ignore_index=True), footprint)[CELL_TABLE]
            pending, n_pending = [], 0
    if pending:
        yield add_coverage(pd.concat(pending, ignore_index=True), footprint)[CELL_TABLE]


def _solve_borders(tiles, width, height):
    """Exact fill level of every border cell, from the per-tile crossings (pass 1 summaries)."""
    sizes = [len(t["z"]) for t in tiles]
    offsets = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    n = int(offsets[-1])
    z = np.empty(n)
    pinned = np.zeros(n, dtype=bool)
    keys = np.empty(n, dtype=np.int64)
    on_border = np.zeros(n, dtype=bool)
    edge_a, edge_b, edge_w, bad_keys = [], [], [], []
    for t, off in zip(tiles, offsets):
        sl = slice(off, off + len(t["z"]))
        z[sl], pinned[sl], on_border[sl] = t["z"], t["pinned"], t["border"]
        keys[sl] = t["keys"]
        edge_a.append(t["ea"] + off)
        edge_b.append(t["eb"] + off)
        edge_w.append(t["ew"])
        bad_keys.append(t["invalid_border_keys"])
    # single steps between border cells, and border cells next to an invalid cell of another tile
    border_nodes = np.nonzero(on_border)[0]
    bad = np.concatenate(bad_keys) if bad_keys else np.empty(0, dtype=np.int64)
    i, j = neighbour_pairs(np.concatenate([keys[border_nodes], bad]), width, height)
    nb = len(border_nodes)
    both = (i < nb) & (j < nb)
    ni, nj = border_nodes[i[both]], border_nodes[j[both]]
    edge_a.append(ni)
    edge_b.append(nj)
    edge_w.append(np.maximum(z[ni], z[nj]))
    touching_invalid = (i < nb) != (j < nb)
    flagged = np.where(i[touching_invalid] < nb, i[touching_invalid], j[touching_invalid])
    pinned[border_nodes[flagged]] = True
    levels = solve_levels(z, pinned, np.concatenate(edge_a), np.concatenate(edge_b),
                          np.concatenate(edge_w))
    stats = {"n_border_cells": n, "n_crossings": int(sum(len(a) for a in edge_a))}
    return [levels[off:off + len(t["z"])] for t, off in zip(tiles, offsets)], stats


def _tile_records(depth, box, width, areas, bmask, near, min_depth, labels=None, n=None):
    """Per-tile summary of its depression pieces, before they are joined across tile borders."""
    r0, r1, c0, c1 = box
    h, w = depth.shape
    if labels is None:
        labels, n = ndimage.label(depth > 0, structure=EIGHT)
    if n == 0:
        return None
    rows, cols = np.nonzero(labels)                 # row-major, as scipy numbers labels
    lab, d = labels[rows, cols], depth[rows, cols]
    a = areas[rows]
    df = pd.DataFrame({"lab": lab, "r": rows + r0, "c": cols + c0, "d": d, "a": a, "v": d * a})
    spec = dict(npx=("d", "size"), area=("a", "sum"), volume=("v", "sum"), maxd=("d", "max"),
                rmin=("r", "min"), rmax=("r", "max"), cmin=("c", "min"), cmax=("c", "max"),
                first_r=("r", "first"), first_c=("c", "first"))
    if bmask is not None:
        df["b"] = bmask[rows, cols].astype(np.float64)
        spec["bsum"] = ("b", "sum")
    if near is not None:
        df["nd"] = np.where(near[rows, cols], d, -1.0)
        spec["near_max"] = ("nd", "max")
    agg = df.groupby("lab", sort=True).agg(**spec)
    top_depth = np.zeros(n + 1)
    top_depth[agg.index.to_numpy()] = agg["maxd"].to_numpy()
    tied = df[df["d"].to_numpy() >= top_depth[lab] - _TIE]
    low = tied.groupby("lab", sort=True).agg(low_r=("r", "first"), low_c=("c", "first"))
    agg = agg.join(low)
    # a piece wholly inside the tile and shallower than min_depth can never qualify: drop it now
    on_border = ((agg["rmin"] == r0) | (agg["rmax"] == r1 - 1)
                 | (agg["cmin"] == c0) | (agg["cmax"] == c1 - 1))
    keep = ((agg["maxd"] >= min_depth) | on_border).to_numpy()
    agg = agg[keep]
    if agg.empty:
        return None
    labs = agg.index.to_numpy()
    wanted = np.zeros(n + 1, dtype=bool)
    wanted[labs] = True
    sel = wanted[lab]
    return agg.reset_index(), df.loc[sel, ["lab", "r", "c", "d", "a", "v"]], labels


def analyze_tiled(dem_path, *, drains_xy=None, buildings=None, mask_path=None, tile=4096,
                  res="auto", min_depth=0.15, drain_radius_m=5.0, drain_core_frac=0.8,
                  verbose=False, out_dir=None, format="csv", hours=None, catchments=False,
                  workers=1):
    """Analyze a large DEM in tiles. The result equals `analyze` on the same DEM.

    tile: tile edge in cells. It only trades memory for speed; it never changes the result.
    mask_path: optional raster on the same grid as the DEM; non-zero cells are ignored.
    out_dir: write `depressions`, `cells` and `meta.json` to this folder as they are produced,
        instead of holding the tables in memory. The returned `Result` then has
        `depressions = cells = None` and `path = out_dir`; `Result.load()` reads them back.
    format: "csv" (default) or "parquet" (needs the `parquet` extra), used with `out_dir`.
        With `out_dir`, every tile's result is also saved as it is computed (in `out_dir/_work`),
        so a run that is interrupted continues where it stopped when called again with the same
        arguments; the checkpoints are removed once the tables are written.
    hours: with `out_dir`, stop after about this many hours, at the end of a tile, raising
        `Paused`; calling again with the same arguments continues. Splits a long run into parts.
    catchments: also route the water, exactly as `analyze(..., catchments=True)` does (see
        `hydro.tiled_routing`); with `out_dir` the catchment table is written as `catchments.<fmt>`.
    workers: label pixels with H3 cells in this many processes (same answer, less time).
    Other arguments are as in `analyze`. `Result.labels` and `Result.depth` are not returned,
    and `id` numbers the depressions 1..n in raster-scan order of their first cell.
    """
    if tile <= 0:
        raise ValueError("tile must be positive")
    if out_dir is not None:
        check_format(format)
    if hours is not None and (out_dir is None or not hours > 0):
        raise ValueError("hours needs out_dir (the checkpoints live there) and must be positive")
    has_drains = drains_xy is not None and len(drains_xy) > 0
    drains = np.asarray(drains_xy, dtype="float64").reshape(-1, 2) if has_drains else None
    with gdal_env(), rasterio.open(dem_path) as src:
        height, width, crs, transform = src.height, src.width, src.crs, src.transform
        requested = res
        size, tier, res = plan_analysis(crs, transform, height, res, min_depth)
        geographic, _ = crs_info(crs)
        areas = pixel_areas_m2(transform, crs, height)
        mask_src = rasterio.open(mask_path) if mask_path else None
        try:
            if mask_src is not None and mask_src.shape != src.shape:
                raise ValueError(f"mask {mask_src.shape} and DEM {src.shape} differ in shape")
            return _run(src, mask_src, dem_path, mask_path, height, width, crs, transform, size,
                        tier, geographic, areas, drains, buildings, tile, res, min_depth,
                        drain_radius_m, drain_core_frac, verbose, requested, out_dir, format, hours,
                        catchments, workers)
        finally:
            if mask_src is not None:
                mask_src.close()


def _settings(dem_path, shape, transform, crs, tile, res, min_depth, drain_radius_m,
              drain_core_frac, drains, buildings, mask_path, catchments=False):
    """What a resumed run must share with the run it continues."""
    import hashlib

    from .pipeline import _package_version

    return {
        "dem": str(dem_path), "shape": list(shape), "transform": list(transform)[:6],
        "crs": crs.to_wkt(), "tile": tile, "res": res, "min_depth": min_depth,
        "drain_radius_m": drain_radius_m, "drain_core_frac": drain_core_frac,
        "drains": None if drains is None else hashlib.sha256(drains.tobytes()).hexdigest(),
        "buildings": None if buildings is None else len(buildings),
        "mask": None if mask_path is None else str(mask_path),
        "package_version": _package_version(), "catchments": bool(catchments),
        "checkpoint_format": 2}                     # 2: one outlet node per tile


def _run(src, mask_src, dem_path, mask_path, height, width, crs, transform, size, tier,
         geographic, areas, drains, buildings, tile, res, min_depth, drain_radius_m,
         drain_core_frac, verbose, requested, out_dir, fmt, hours, catchments=False, workers=1):
    work = None
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        work = Checkpoints(out_dir / "_work", _settings(
            dem_path, (height, width), transform, crs, tile, res, min_depth, drain_radius_m,
            drain_core_frac, drains, buildings, mask_path, catchments))
    ctx = {"src": src, "mask_src": mask_src, "height": height, "width": width, "crs": crs,
           "transform": transform, "areas": areas, "buildings": buildings, "geographic": geographic,
           "res": res, "min_depth": min_depth, "radius": metres_to_cells(drain_radius_m, size),
           "drain_rc": drain_pixels(drains, transform, (height, width)) if drains is not None else None,
           "deadline": None if hours is None else time.monotonic() + hours * 3600,
           "verbose": verbose, "invalid": {}, "catchments": catchments, "workers": workers,
           "spacing": pixel_spacing_m(transform, crs, height) if catchments else None}
    boxes = list(_tile_boxes(height, width, tile))
    blocks = np.zeros((-(-height // 16), -(-width // 16)), dtype=bool)   # blocks holding invalid pixels
    pieces, graph = _two_passes(boxes, blocks, ctx, work)

    meta = base_meta(str(dem_path), crs, (height, width), size, tier, geographic, res, min_depth,
                     drain_radius_m, drain_core_frac, drains, buildings, res_requested=requested)
    meta["tiling"] = {"tile": tile, "n_tiles": len(boxes), **graph}
    has_buildings = buildings is not None
    columns = dep_columns(drains is not None, has_buildings, catchments)
    # coverage reads which pixels are valid from the records pass 1 kept, not from the DEM again
    # (a streamed DEM would be read over the internet once per partly covered cell)
    footprint = Footprint((height, width), transform, crs, blocks,
                          tile_opener(tile, (height, width), _invalid_reader(boxes, ctx, work)))
    parts = CellParts(res, None if out_dir is None else out_dir / "_spill")
    cparts = (CellParts(res, None if out_dir is None else out_dir / "_spill_catchments")
              if catchments else None)
    try:
        records, rings = [], []
        routing = _RoutingParts() if catchments else None
        for piece in pieces:                     # tile by tile, in order: identical ids every time
            _add_piece(piece, records, rings, parts, routing, cparts)
        if not records:
            out, lowpoints = None, np.zeros(0, dtype=np.uint64)
            comp, kept = np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool)
            dep_comp = np.zeros(0, dtype=np.int64)
        else:
            out, lowpoints, comp, kept, dep_comp = _join_pieces(
                records, rings, width, height, transform, crs, res, min_depth, drains,
                has_buildings, drain_core_frac)
        del records, rings
        catchment_chunks = None
        if catchments:
            out, ends, id_of_comp = _settle_routing(routing, out, comp, kept, dep_comp, width,
                                                    height, transform, crs)
            del routing
            catchment_chunks = _catchment_chunks(cparts, ends, id_of_comp)
        meta.update({"n_depressions": 0 if out is None else len(out),
                     "n_edge": 0 if out is None else int(out["edge"].sum())})
        cells = _cell_chunks(parts, comp, kept, lowpoints, footprint)
        if out_dir is not None:
            result = _write(out_dir, fmt, out, lowpoints, res, columns, cells, meta, transform, crs,
                            footprint, catchment_chunks)
            work.remove()                        # finished: the checkpoints are no longer needed
            footprint._opener = file_opener(dem_path, mask_path)   # their records went with them
            return result
        deps = empty_depressions(columns) if out is None else _with_ids(out, lowpoints)[columns]
        chunks = list(cells)
        table = pd.concat(chunks, ignore_index=True) if chunks else empty_cells()
        catch = None
        if catchments:
            pieces_c = list(catchment_chunks)
            catch = pd.concat(pieces_c, ignore_index=True) if pieces_c else empty_catchments()
        return Result(deps, table, meta, transform=transform, crs=crs, footprint=footprint,
                      catchments=catch)
    finally:
        parts.close()
        if cparts is not None:
            cparts.close()


def _two_passes(boxes, blocks, ctx, work):
    """Both passes over the tiles. Returns the per-tile pieces (an iterable) and the graph sizes.

    With checkpoints (`work`), every tile's result is saved as soon as it is computed and tiles
    saved by an earlier, interrupted run are loaded instead of computed again.
    """
    n = len(boxes)
    say = print if ctx["verbose"] else (lambda *a, **k: None)
    graph = work.load_json("graph") if work is not None else None
    if work is not None and graph is not None and work.count("p2") == n:
        # both passes finished earlier: only the footprint is needed from pass 1
        say(f"resuming: both passes were finished earlier ({n} tiles)", flush=True)
        for k, box in enumerate(boxes, 1):
            _mark_blocks(blocks, work.load("p1", k, keys=["blk_r", "blk_c", "blk"]))
        return (_unpack_piece(work.load("p2", k)) for k in range(1, n + 1)), graph
    if work is not None and graph is not None and work.count("lv") == n:
        # pass 1 and the border levels finished earlier: pass 2 loads each tile's seeds and
        # levels when it needs them, so a resumed pass 2 neither repeats the solve nor holds
        # every tile in memory
        say(f"resuming: pass 1 and the border levels were finished earlier; pass 2 has "
            f"{work.count('p2')} of {n} tiles done", flush=True)
        for k, box in enumerate(boxes, 1):
            _mark_blocks(blocks, work.load("p1", k, keys=["blk_r", "blk_c", "blk"]))
        if ctx["catchments"]:
            ctx["borders"] = _border_lookup(
                [work.load("p1", k, keys=["keys"])["keys"] for k in range(1, n + 1)],
                [work.load("lv", k)["lv"] for k in range(1, n + 1)])
        return _pass2(boxes, None, None, ctx, work, say), graph

    # ---- pass 1: flood every tile from its border and keep only the crossings
    if work is not None and work.count("p1"):
        say(f"resuming: pass 1 has {work.count('p1')} of {n} tiles done earlier, "
            f"pass 2 has {work.count('p2')}", flush=True)
    tiles = []
    for k, box in enumerate(boxes, 1):
        if work is not None and work.has("p1", k):
            t = work.load("p1", k)
        else:
            t = _pass1_tile(box, ctx)
            if work is not None:
                work.save("p1", k, t)
            say(f"pass 1, tile {k}/{n}: {int(t['n_seeds'])} seeds, {len(t['ea'])} crossings", flush=True)
            _check_time(ctx, work, n)
        _mark_blocks(blocks, t)
        if work is None:                         # the checkpoints hold it otherwise
            ctx["invalid"][k] = {"inv_any": t["inv_any"], "inv_z": t["inv_z"]}
        tiles.append(t)

    # ---- the exact level of every border cell
    levels, graph = _solve_borders(tiles, ctx["width"], ctx["height"])
    if ctx["catchments"]:
        ctx["borders"] = _border_lookup([t["keys"] for t in tiles], levels)
    if work is not None:
        for k, lv in enumerate(levels, 1):
            work.save("lv", k, {"lv": lv})
        work.save_json("graph", graph)          # last: its presence means every level is saved
    return _pass2(boxes, tiles, levels, ctx, work, say), graph


def _pass2(boxes, tiles, levels, ctx, work, say):
    """Pass 2: flood each tile from the exact border levels, then summarise its depressions.

    `tiles` and `levels` are the pass-1 results in memory, or None to load each tile's from the
    checkpoints when it is needed.
    """
    n = len(boxes)
    pieces = []
    for k, box in enumerate(boxes, 1):
        if work is not None and work.has("p2", k):
            if tiles is not None:
                tiles[k - 1] = levels[k - 1] = None
            continue
        if tiles is None:
            t = work.load("p1", k, keys=["n_seeds"])
            lv = work.load("lv", k)["lv"]
        else:
            t, lv = tiles[k - 1], levels[k - 1]
            tiles[k - 1] = levels[k - 1] = None      # release this tile's pass-1 data once used
        piece = _pass2_tile(box, lv, ctx) if int(t["n_seeds"]) else None
        if work is not None:
            work.save("p2", k, _pack_piece(piece))
            _check_time(ctx, work, n)
        elif piece is not None:
            pieces.append(piece)
        n_pieces = 0 if piece is None or piece["agg"] is None else len(piece["agg"])
        say(f"pass 2, tile {k}/{n}: {n_pieces} pieces", flush=True)
    if work is not None:
        return (_unpack_piece(work.load("p2", k)) for k in range(1, n + 1))
    return pieces


def _invalid_reader(boxes, ctx, work):
    """invalid_tile(k) for `tile_opener`: tile k's invalid pixels as recorded in pass 1."""
    def invalid_tile(k):
        r0, r1, c0, c1 = boxes[k - 1]
        if work is not None:
            try:
                rec = work.load("p1", k, keys=["inv_any", "inv_z"])
            except KeyError:                    # checkpoints of an older version: read the DEM
                return ~_read_tile(ctx["src"], ctx["mask_src"], boxes[k - 1])[1]
        else:
            rec = ctx["invalid"][k]
        return unpack_invalid(rec["inv_any"], rec["inv_z"], (r1 - r0, c1 - c0))
    return invalid_tile


def _check_time(ctx, work, n):
    if ctx["deadline"] is not None and time.monotonic() > ctx["deadline"]:
        p1, p2 = work.count("p1"), work.count("p2")
        raise Paused(f"stopped at the time limit after a whole tile: pass 1 has {p1} of {n} tiles "
                     f"done, pass 2 has {p2} of {n}. Run the same command again to continue.",
                     p1 + p2, 2 * n)


def _mark_blocks(blocks, t):
    r, c, flags = int(t["blk_r"]), int(t["blk_c"]), t["blk"]
    blocks[r:r + flags.shape[0], c:c + flags.shape[1]] |= flags


def _pass1_tile(box, ctx):
    """Seeds, crossings and invalid-block flags of one tile (pass 1)."""
    r0, r1, c0, c1 = box
    height, width = ctx["height"], ctx["width"]
    work, valid = _read_tile(ctx["src"], ctx["mask_src"], box)
    br, bc, flags = invalid_block_flags(~valid, (r0, c0))
    rows, cols, pinned, border = tile_seeds(valid, (r0 == 0, r1 == height, c0 == 0, c1 == width))
    z = work[rows, cols]
    level, root = flood_from_seeds(work, valid, rows, cols, z)
    ea, eb, ew = region_edges(level, root)
    # The tile's border seeds are the graph's nodes. Every other seed is an outlet beside nodata
    # or water, whose level is its own elevation and never needs solving: they become a single
    # node, an outlet below everything, keeping for each region only its lowest crossing to any
    # of them. A crossing into an outlet is never below the outlet's elevation, so every border
    # level comes out the same, and the graph no longer grows with the length of shorelines.
    nb = int(border.sum())
    to_node = np.full(len(z), nb, dtype=np.int64)
    to_node[border] = np.arange(nb)
    a, b = to_node[ea], to_node[eb]
    keep = (a != nb) | (b != nb)
    a, b, ew = np.minimum(a, b)[keep], np.maximum(a, b)[keep], ew[keep]
    order = np.lexsort((ew, b, a))
    a, b, ew = a[order], b[order], ew[order]
    first = np.ones(len(a), dtype=bool)
    first[1:] = (a[1:] != a[:-1]) | (b[1:] != b[:-1])
    edge_mask = np.zeros(valid.shape, dtype=bool)
    edge_mask[0, :] = edge_mask[-1, :] = edge_mask[:, 0] = edge_mask[:, -1] = True
    bad_r, bad_c = np.nonzero(edge_mask & ~valid)
    br_rows, br_cols = rows[border], cols[border]
    return {"z": np.r_[z[border], -1e30], "pinned": np.r_[pinned[border], True],
            "border": np.r_[np.ones(nb, dtype=bool), False],
            "keys": np.r_[(br_rows + r0).astype(np.int64) * width + (br_cols + c0), -1],
            "ea": a[first], "eb": b[first], "ew": ew[first], "n_seeds": np.int64(len(z)),
            "invalid_border_keys": (bad_r + r0).astype(np.int64) * width + (bad_c + c0),
            "blk_r": np.int64(br), "blk_c": np.int64(bc), "blk": flags,
            **dict(zip(("inv_any", "inv_z"), pack_invalid(~valid)))}


def _pass2_tile(box, lv, ctx):
    """The depression pieces of one tile (pass 2), with piece numbers local to the tile.

    `lv` are the solved levels of the tile's border seeds (and, last, of its outlet node). The
    seeds are found again from the tile, exactly as in pass 1: border seeds start at their solved
    level, outlets beside nodata or water at their own elevation.
    """
    r0, r1, c0, c1 = box
    h, w = r1 - r0, c1 - c0
    src, width = ctx["src"], ctx["width"]
    if ctx["catchments"]:                         # the tile and a one-pixel rim of its neighbours
        win = (max(r0 - 1, 0), min(r1 + 1, ctx["height"]), max(c0 - 1, 0), min(c1 + 1, width))
        ework, evalid = _read_tile(src, ctx["mask_src"], win)
        oy, ox = r0 - win[0], c0 - win[2]
        work = np.ascontiguousarray(ework[oy:oy + h, ox:ox + w])
        valid = np.ascontiguousarray(evalid[oy:oy + h, ox:ox + w])
    else:
        work, valid = _read_tile(src, ctx["mask_src"], box)
    rows, cols, _, border = tile_seeds(valid, (r0 == 0, r1 == ctx["height"], c0 == 0,
                                               c1 == width))
    levels = work[rows, cols]
    if int(border.sum()) != len(lv) - 1:
        raise RuntimeError("pass 1 and pass 2 found different seeds in a tile")
    levels[border] = lv[:-1]
    filled, _ = flood_from_seeds(work, valid, rows, cols, levels)
    depth = np.where(valid, filled - work, 0.0)
    window_transform = src.window_transform(Window(c0, r0, w, h))
    bmask = None
    if ctx["buildings"] is not None:
        bmask = rasterize_buildings(
            _buildings_in(ctx["buildings"], _bounds(window_transform, (h, w))), window_transform,
            (h, w), min_area=0.0 if ctx["geographic"] else 20.0)
    drain_rc = ctx["drain_rc"]
    near = (near_drain_mask(drain_rc[0], drain_rc[1], (h, w), (r0, c0), ctx["radius"])
            if drain_rc is not None else None)
    labels, n_lab = ndimage.label(depth > 0, structure=EIGHT)
    out = _tile_records(depth, box, width, ctx["areas"][r0:r1], bmask, near, ctx["min_depth"],
                        labels, n_lab)
    lab2rec = np.full(n_lab + 1, -1, dtype=np.int64)
    if out is not None:
        lab2rec[out[0]["lab"].to_numpy()] = np.arange(len(out[0]))
    route = (_route_tile(ework, evalid, filled, labels, n_lab, lab2rec, box, win, ctx)
             if ctx["catchments"] else None)
    if out is None:
        return None if route is None else {"agg": None, "route": route}
    agg, pixels, labels = out
    # border pixels, to join pieces of one depression across tile borders
    ring = np.zeros((h, w), dtype=bool)
    ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
    pr, pc = np.nonzero(ring & (labels > 0))
    # pixels of kept pieces, as H3 cells
    lon, lat = lonlat(ctx["crs"], *pixel_centers(ctx["transform"], pixels["r"].to_numpy(),
                                                 pixels["c"].to_numpy()))
    cells = _cell_summary(lab2rec[pixels["lab"].to_numpy()],
                          h3_ids(lat, lon, ctx["res"], ctx["workers"]),
                          pixels["d"].to_numpy(), pixels["a"].to_numpy(), pixels["v"].to_numpy())
    piece = {"agg": agg, "ring_keys": (pr + r0).astype(np.int64) * width + (pc + c0),
             "ring_rec": lab2rec[labels[pr, pc]], "cells": cells}
    if route is not None:
        piece["route"] = route
    return piece


_CELL_PARTS = ("c_rec", "c_cell", "c_depth", "c_area", "c_volume")


def _pack_piece(piece):
    """A tile's pieces as named arrays for a checkpoint file (`empty` when it has none)."""
    if piece is None:
        return {"empty": np.bool_(True)}
    arrays = {"empty": np.bool_(False), "has_agg": np.bool_(piece["agg"] is not None)}
    if "route" in piece:
        arrays.update({f"rt__{k}": v for k, v in piece["route"].items()})
    if piece["agg"] is None:
        return arrays
    agg = piece["agg"]
    arrays.update({f"agg__{c}": agg[c].to_numpy() for c in agg.columns})
    arrays["agg_columns"] = np.array(list(agg.columns))
    arrays.update(dict(zip(_CELL_PARTS, piece["cells"])))
    return {**arrays, "ring_keys": piece["ring_keys"], "ring_rec": piece["ring_rec"]}


def _unpack_piece(arrays):
    if bool(arrays["empty"]):
        return None
    route = {k[4:]: v for k, v in arrays.items() if k.startswith("rt__")}
    piece = {"agg": None}
    if bool(arrays.get("has_agg", True)):
        agg = pd.DataFrame({c: arrays[f"agg__{c}"] for c in arrays["agg_columns"]})
        piece = {"agg": agg, "ring_keys": arrays["ring_keys"], "ring_rec": arrays["ring_rec"],
                 "cells": tuple(arrays[k] for k in _CELL_PARTS)}
    if route:
        piece["route"] = route
    return piece


def _add_piece(piece, records, rings, parts, routing=None, cparts=None):
    """Number a tile's pieces after those of the tiles before it and collect them."""
    if piece is None:
        return
    offset = sum(len(r) for r in records) if records else 0
    agg = piece["agg"]
    if agg is not None:
        records.append(agg.assign(rec=np.arange(offset, offset + len(agg))))
        rings.append((piece["ring_keys"], piece["ring_rec"] + offset))
        rec, cell, depth, area, volume = piece["cells"]
        parts.add(rec + offset, cell, depth, area, volume)
    if routing is not None and "route" in piece:
        routing.add(piece["route"], offset, cparts)


def _join_pieces(records, rings, width, height, transform, crs, res, min_depth, drains,
                 has_buildings, drain_core_frac):
    """Join the pieces of each depression across tile borders and apply min_depth to the whole.

    Returns the depression table without its H3 id columns, the integer id of each lowest
    point's cell, the depression (component) of every piece, and which components were kept.
    """
    recs = pd.concat(records, ignore_index=True)
    records.clear()                                  # the per-tile copies are no longer needed
    ring_keys = np.concatenate([r[0] for r in rings])
    ring_rec = np.concatenate([r[1] for r in rings])
    rings.clear()
    ci, cj = neighbour_pairs(ring_keys, width, height)
    graph_m = coo_matrix((np.ones(len(ci), dtype=np.int8), (ring_rec[ci], ring_rec[cj])),
                         shape=(len(recs), len(recs)))
    _, comp = connected_components(graph_m, directed=False)
    recs["comp"] = comp
    recs["first_key"] = recs["first_r"].astype(np.int64) * width + recs["first_c"]
    recs["low_key"] = recs["low_r"].astype(np.int64) * width + recs["low_c"]
    top = recs.groupby("comp")["maxd"].transform("max")
    recs["low_key"] = recs["low_key"].where(recs["maxd"] >= top - _TIE, np.iinfo(np.int64).max)
    spec = dict(npx=("npx", "sum"), area=("area", "sum"), volume=("volume", "sum"),
                maxd=("maxd", "max"), rmin=("rmin", "min"), rmax=("rmax", "max"),
                cmin=("cmin", "min"), cmax=("cmax", "max"), first_key=("first_key", "min"),
                low_key=("low_key", "min"))
    if has_buildings:
        spec["bsum"] = ("bsum", "sum")
    if drains is not None:
        spec["near_max"] = ("near_max", "max")
    dep = recs.groupby("comp").agg(**spec)
    dep = dep[dep["maxd"] >= min_depth].sort_values("first_key")
    dep["id"] = np.arange(1, len(dep) + 1)

    low_r, low_c = dep["low_key"].to_numpy() // width, dep["low_key"].to_numpy() % width
    xs, ys = pixel_centers(transform, low_r, low_c)
    lon, lat = lonlat(crs, xs, ys)
    out = pd.DataFrame({
        "id": dep["id"].to_numpy(), "max_depth_m": dep["maxd"].to_numpy(),
        "area_m2": dep["area"].to_numpy(), "volume_m3": dep["volume"].to_numpy(),
        "lat": lat, "lon": lon,
        "edge": ((dep["rmin"] <= 1) | (dep["cmin"] <= 1) | (dep["rmax"] + 1 >= height - 1)
                 | (dep["cmax"] + 1 >= width - 1)).to_numpy(),
        "deeper_than_wide": deeper_than_wide(dep["maxd"].to_numpy(), dep["area"].to_numpy())})
    if drains is not None:
        out["dist_to_drain_m"] = nearest_distance_m(drains, np.c_[xs, ys], crs)
        out["drain_at_core"] = (dep["near_max"] >= drain_core_frac * dep["maxd"]).to_numpy()
    if has_buildings:
        out["building_overlap"] = (dep["bsum"] / dep["npx"]).to_numpy()
        out["likely_building_artifact"] = out["building_overlap"] >= 0.5

    kept = np.zeros(int(comp.max()) + 1, dtype=bool)
    kept[dep.index.to_numpy()] = True
    return out, h3_ids(lat, lon, res), comp, kept, dep.index.to_numpy()


def _with_ids(out, lowpoints):
    """The depression table with its H3 id columns, from the lowest points' integer cell ids."""
    return out.assign(
        h3_lowpoint=[h3.int_to_str(int(c)) for c in lowpoints],
        h3_lowpoint_15=[h3.latlng_to_cell(la, lo, FINEST_H3)
                        for la, lo in zip(out["lat"].to_numpy(), out["lon"].to_numpy())])


def _write(out_dir, fmt, out, lowpoints, res, columns, cells, meta, transform, crs, footprint,
           catchment_chunks=None):
    """Write the tables piece by piece; return a `Result` that points at them."""
    remove_other_formats(out_dir, fmt)
    with TableWriter(out_dir / f"depressions.{fmt}", fmt) as w:
        for s in range(0, 0 if out is None else len(out), CHUNK_ROWS):
            w.write(_with_ids(out.iloc[s:s + CHUNK_ROWS], lowpoints[s:s + CHUNK_ROWS])[columns])
        w.close(empty=empty_depressions(columns))
    with TableWriter(out_dir / f"cells.{fmt}", fmt) as w:
        for chunk in cells:
            w.write(chunk)
        w.close(empty=empty_cells())
    n_cells = w.rows
    if catchment_chunks is not None:
        with TableWriter(out_dir / f"catchments.{fmt}", fmt) as w:
            for chunk in catchment_chunks:
                w.write(chunk)
            w.close(empty=empty_catchments())
    meta["output"] = {"format": fmt, "n_cells": n_cells}
    write_meta(out_dir, meta)
    return Result(None, None, meta, transform=transform, crs=crs, footprint=footprint, path=out_dir)


# ------------------------------------------------------------------ catchments (tiled)

def _border_lookup(keys, levels):
    """Sorted (key, exact level) of every tile-border cell, from each tile's pass-1 node keys
    (the last node of each tile is its outlet node, key -1) and solved levels."""
    k = np.concatenate([np.asarray(a)[:-1] for a in keys]) if keys else np.zeros(0, np.int64)
    v = np.concatenate([np.asarray(a)[:-1] for a in levels]) if levels else np.zeros(0)
    order = np.argsort(k)
    return k[order], v[order]


def _border_levels(borders, keys):
    sorted_keys, levels = borders
    pos = np.minimum(np.searchsorted(sorted_keys, keys), max(len(sorted_keys) - 1, 0))
    if len(keys) and not (sorted_keys[pos] == keys).all():
        raise RuntimeError("a valid pixel on a tile border has no solved level")
    return levels[pos]


def _route_tile(ework, evalid, filled, labels, n_lab, lab2rec, box, win, ctx):
    """Routing of one tile (see `hydro.tiled_routing`): the ends of its water paths and the
    pieces needed to join them with the other tiles'. Codes are local: sink ids are the tile's
    piece numbers and surface ids its own numbers, offset when the tiles are put together."""
    r0, r1, c0, c1 = box
    er0, er1, ec0, ec1 = win
    h, w = r1 - r0, c1 - c0
    oy, ox = r0 - er0, c0 - ec0
    width = ctx["width"]
    efilled = ework.copy()
    efilled[oy:oy + h, ox:ox + w] = filled
    rim = np.ones(ework.shape, dtype=bool)
    rim[oy:oy + h, ox:ox + w] = False
    rr, cc = np.nonzero(rim & evalid)
    efilled[rr, cc] = _border_levels(ctx["borders"],
                                     (rr + er0).astype(np.int64) * width + (cc + ec0))
    esink = evalid & (efilled > ework)
    elab = np.zeros(ework.shape, dtype=np.int64)
    elab[oy:oy + h, ox:ox + w] = labels
    dist = _distances(*(a[er0:er1] for a in ctx["spacing"]))
    (end_t, end_i, pcomp, has_down, outlet, unresolved, sink_fp, fp, fp_border, fp_touch,
     fp_exit, fp_dt, fp_di) = tile_route(ework, evalid, efilled, esink, elab, oy, ox, h, w, dist,
                                         width, er0, ec0, n_lab)
    if unresolved:
        raise RuntimeError(f"{unresolved} flat pixels have no way off: the fill is inconsistent")

    # where each sink overflows to, with the sinks that cannot be depressions passed over
    cand = lab2rec >= 0
    s = np.arange(1, n_lab + 1)
    f = sink_fp[s]
    touch = fp_touch[f]
    spill_t = np.full(n_lab + 1, E_OUT, dtype=np.int64)
    spill_i = np.zeros(n_lab + 1, dtype=np.int64)
    spill_t[s] = np.where(touch, E_FP, fp_dt[f])
    spill_i[s] = np.where(touch, f, fp_di[f])
    exit_key = np.full(n_lab + 1, -1, dtype=np.int64)
    exit_key[s] = np.where(touch, -1, fp_exit[f])
    if (~touch & (fp_dt[f] == E_NONE)).any():
        raise RuntimeError("a sink inside a tile has no way to overflow: the fill is inconsistent")
    level = np.zeros(n_lab + 1)
    lr, lc = np.nonzero(labels)
    level[labels[lr, lc]] = filled[lr, lc]
    order = s[np.argsort(level[1:], kind="stable")]
    spill_t, spill_i = collapse(order, cand, spill_t, spill_i)

    def code(t, i):
        t, i = np.array(t, dtype=np.int64), np.array(i, dtype=np.int64)
        t = np.where(t == E_NONE, E_OUT, t)
        sk = t == E_SINK
        nc = sk & ~cand[np.where(sk, i, 0)]
        j = i[nc]
        t[nc], i[nc] = spill_t[j], spill_i[j]
        sk = t == E_SINK
        i[sk] = lab2rec[i[sk]]
        return encode(t, i)

    own_valid = evalid[oy:oy + h, ox:ox + w].ravel()
    codes = np.full(h * w, OUT_CODE, dtype=np.int64)
    codes[own_valid] = code(end_t[own_valid], end_i[own_valid])
    o_rows, o_cols = np.divmod(np.arange(h * w), w)
    keys = (o_rows + r0).astype(np.int64) * width + (o_cols + c0)

    ring = np.zeros((h, w), dtype=bool)
    ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
    rb = np.flatnonzero(ring.ravel() & own_valid)
    pm = np.flatnonzero(pcomp >= 0)
    tf = np.flatnonzero(fp_touch)
    fb = np.flatnonzero(fp_border)
    cand_labels = np.flatnonzero(cand)
    lab_of_rec = np.empty(len(cand_labels), dtype=np.int64)
    lab_of_rec[lab2rec[cand_labels]] = cand_labels

    # the catchment parts: ground area per (end, H3 cell); water leaving the map is not kept
    sel = np.flatnonzero(own_valid & (codes != OUT_CODE))
    lon, lat = lonlat(ctx["crs"], *pixel_centers(ctx["transform"], o_rows[sel] + r0,
                                                  o_cols[sel] + c0))
    cell = h3_ids(lat, lon, ctx["res"], ctx["workers"])
    area = ctx["areas"][o_rows[sel] + r0]
    acc_code, acc_cell, acc_area = codes[sel], cell, area
    if len(sel):
        srt = np.lexsort((acc_cell, acc_code))
        start = _runs(acc_code[srt], acc_cell[srt])
        acc_code, acc_cell = acc_code[srt][start], acc_cell[srt][start]
        acc_area = np.add.reduceat(area[srt], start)
    return {"border_keys": keys[rb], "border_codes": codes[rb],
            "plat_keys": keys[pm], "plat_z": ework[oy:oy + h, ox:ox + w].ravel()[pm],
            "plat_exit": outlet[pm] | has_down[pm], "plat_codes": codes[pm],
            "fp_ids": tf.astype(np.int64), "fp_exit": fp_exit[tf],
            "fp_dest": code(fp_dt[tf], fp_di[tf]),
            "fpb_keys": keys[fb], "fpb_ids": fp[fb], "fpb_level": filled.ravel()[fb],
            "rec_spill": code(spill_t[lab_of_rec], spill_i[lab_of_rec]),
            "rec_exit": exit_key[lab_of_rec],
            "acc_code": acc_code, "acc_cell": acc_cell, "acc_area": acc_area,
            "n_fp": np.int64(len(fp_touch))}


def _offset_codes(codes, rec_offset, fp_offset):
    kind, ident = decode(codes)
    ident = ident + np.where(kind == E_SINK, rec_offset, 0) + np.where(kind == E_FP, fp_offset, 0)
    return encode(kind, ident)


class _RoutingParts:
    """The routing pieces of every tile, numbered like the depression pieces."""

    _LISTS = ("border_keys", "border_codes", "plat_keys", "plat_z", "plat_exit", "plat_codes",
              "fp_gid", "fp_exit", "fp_dest", "fpb_keys", "fpb_gid", "fpb_level", "rec_spill",
              "rec_exit", "code_codes", "code_area")

    def __init__(self):
        self.parts = {k: [] for k in self._LISTS}
        self.n_fp = 0

    def add(self, rt, rec_offset, cparts):
        fp_offset = self.n_fp

        def conv(c):
            return _offset_codes(c, rec_offset, fp_offset)

        p = self.parts
        p["border_keys"].append(rt["border_keys"])
        p["border_codes"].append(conv(rt["border_codes"]))
        p["plat_keys"].append(rt["plat_keys"])
        p["plat_z"].append(rt["plat_z"])
        p["plat_exit"].append(rt["plat_exit"])
        p["plat_codes"].append(conv(rt["plat_codes"]))
        p["fp_gid"].append(rt["fp_ids"] + fp_offset)
        p["fp_exit"].append(rt["fp_exit"])
        p["fp_dest"].append(conv(rt["fp_dest"]))
        p["fpb_keys"].append(rt["fpb_keys"])
        p["fpb_gid"].append(rt["fpb_ids"] + fp_offset)
        p["fpb_level"].append(rt["fpb_level"])
        p["rec_spill"].append(conv(rt["rec_spill"]))
        p["rec_exit"].append(rt["rec_exit"])
        acc = conv(rt["acc_code"])
        if len(acc):
            cparts.add(acc, rt["acc_cell"], np.zeros(len(acc)), rt["acc_area"], np.zeros(len(acc)))
            totals, inverse = np.unique(acc, return_inverse=True)
            p["code_codes"].append(totals)
            p["code_area"].append(np.bincount(inverse, weights=rt["acc_area"]))
        self.n_fp += int(rt["n_fp"])

    def get(self, name, dtype=np.int64):
        a = self.parts[name]
        return np.concatenate(a) if a else np.zeros(0, dtype=dtype)


def _settle_routing(routing, out, comp, kept, dep_comp, width, height, transform, crs):
    """Join the routing pieces of all tiles: the catchment and spill columns of the depression
    table, the settled ends, and the depression id of each joined sink (-1 if not kept)."""
    g = routing.get
    fpc = join_surfaces(g("fpb_keys"), g("fpb_gid"), g("fpb_level", float), routing.n_fp,
                        width, height)
    big = np.iinfo(np.int64).max
    fp_gid, fp_exit, fp_dest = g("fp_gid"), g("fp_exit"), g("fp_dest")
    n_fpc = int(fpc.max()) + 1 if len(fpc) else 0
    fpc_exit = np.full(n_fpc, -1, dtype=np.int64)
    fpc_dest = np.full(n_fpc, OUT_CODE, dtype=np.int64)
    if len(fp_gid):
        ex = np.where(fp_exit < 0, big, fp_exit)
        c = fpc[fp_gid]
        order = np.lexsort((ex, c))
        first = order[np.r_[True, c[order][1:] != c[order][:-1]]]
        fpc_exit[c[first]] = np.where(ex[first] == big, -1, ex[first])
        fpc_dest[c[first]] = fp_dest[first]
    flat_keys, flat_next = np.zeros(0, np.int64), np.zeros(0, np.int64)
    if len(routing.parts["plat_keys"]) and len(g("plat_keys")):
        flat_keys, flat_next = settle_flats(g("plat_keys"), g("plat_z", float),
                                            g("plat_exit", bool), g("plat_codes"), width, height)
    rec_spill, rec_exit = g("rec_spill"), g("rec_exit")
    n_comp = len(kept)
    comp_spill = np.full(n_comp, OUT_CODE, dtype=np.int64)
    comp_exit = np.full(n_comp, -1, dtype=np.int64)
    if len(comp):
        comps, first_rec = np.unique(comp, return_index=True)
        comp_spill[comps], comp_exit[comps] = rec_spill[first_rec], rec_exit[first_rec]
    recs = np.arange(len(comp))
    node = [encode(E_SINK, recs), encode(E_HALO, g("border_keys")), encode(E_FLAT, flat_keys),
            encode(E_FP, fp_gid)]
    nxt = [np.where(kept[comp], encode(E_KEPT, comp), comp_spill[comp]) if len(comp) else
           np.zeros(0, np.int64), g("border_codes"), flat_next,
           fpc_dest[fpc[fp_gid]] if len(fp_gid) else np.zeros(0, np.int64)]
    ends = Ends(np.concatenate(node), np.concatenate(nxt))
    id_of_comp = np.full(n_comp, -1, dtype=np.int64)
    if out is None:
        return None, ends, id_of_comp
    id_of_comp[dep_comp] = out["id"].to_numpy()

    spill = comp_spill[dep_comp]
    kind, ident = decode(ends.final(spill))
    spill_to = np.where(kind == E_KEPT, id_of_comp[np.where(kind == E_KEPT, ident, 0)], 0)
    sk, sid = decode(spill)
    via_surface = sk == E_FP
    key = comp_exit[dep_comp].copy()
    key[via_surface] = fpc_exit[fpc[sid[via_surface]]]
    if (key < 0).any():
        raise RuntimeError("a depression has no spill point: the fill is inconsistent")
    lon, lat = lonlat(crs, *pixel_centers(transform, key // width, key % width))
    codes, areas = g("code_codes"), g("code_area", float)
    ck, ci = decode(ends.final(codes))
    mine = ck == E_KEPT
    owner = id_of_comp[ci[mine]]
    catch = np.bincount(owner, weights=areas[mine], minlength=int(out["id"].max()) + 1)
    out = out.assign(catchment_area_m2=catch[out["id"].to_numpy()],
                     spill_to=spill_to.astype(np.int64), spill_lat=lat, spill_lon=lon)
    return out, ends, id_of_comp


def _catchment_chunks(cparts, ends, id_of_comp, rows=CHUNK_ROWS):
    """The catchment table in pieces, ordered by H3 cell and then depression id."""
    pending, n_pending = [], 0
    for key in cparts.keys():
        arr = cparts.load(key)
        kind, ident = decode(ends.final(arr["rec"]))
        keep = kind == E_KEPT
        ids, cell, area = id_of_comp[ident[keep]], arr["cell"][keep], arr["area"][keep]
        if len(ids) == 0:
            continue
        order = np.lexsort((ids, cell))
        ids, cell, area = ids[order], cell[order], area[order]
        start = _runs(cell, ids)
        pending.append(pd.DataFrame({"id": ids[start].astype(np.int64),
                                     "h3": [h3.int_to_str(int(c)) for c in cell[start]],
                                     "area_m2": np.add.reduceat(area, start)}))
        n_pending += len(start)
        if n_pending >= rows:
            yield pd.concat(pending, ignore_index=True)[CATCHMENT_TABLE]
            pending, n_pending = [], 0
    if pending:
        yield pd.concat(pending, ignore_index=True)[CATCHMENT_TABLE]
