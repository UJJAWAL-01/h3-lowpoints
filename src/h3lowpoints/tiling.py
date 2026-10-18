"""Analyze a DEM tile by tile and get exactly the result of analyzing it whole.

`analyze_tiled` fills the DEM in two passes over the tiles (see `hydro/tiled.py` for the method),
labels the depressions inside each tile, and joins the pieces of depressions that cross tile
borders. Peak memory is one tile plus a graph over the tile-border cells, so a raster far larger
than memory can be analyzed. The result equals `analyze` on the same DEM, to the last digit of
every depth and to rounding in area and volume sums; `tests/test_tiling.py` checks that on random
terrain, nodata, flats, mazes and geographic grids.
"""
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
from .coverage import Footprint, file_opener, mark_invalid_blocks
from .geometry import crs_info, nearest_distance_m, pixel_areas_m2
from .h3agg.aggregate import CELL_COLUMNS, h3_ids, lonlat, pixel_centers
from .hydro.depressions import EIGHT
from .hydro.tiled import (flood_from_seeds, neighbour_pairs, region_edges, solve_levels,
                          tile_seeds)
from .pipeline import CELL_TABLE, Result, base_meta, dep_columns, finish_cells, plan_analysis
from .resolution import FINEST_H3
from .sources._common import gdal_env
from .resolution import metres_to_cells

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


def _tile_records(depth, box, width, areas, bmask, near, min_depth):
    """Per-tile summary of its depression pieces, before they are joined across tile borders."""
    r0, r1, c0, c1 = box
    h, w = depth.shape
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
                  verbose=False):
    """Analyze a large DEM in tiles. The result equals `analyze` on the same DEM.

    tile: tile edge in cells. It only trades memory for speed; it never changes the result.
    mask_path: optional raster on the same grid as the DEM; non-zero cells are ignored.
    Other arguments are as in `analyze`. `Result.labels` and `Result.depth` are not returned,
    and `id` numbers the depressions 1..n in raster-scan order of their first cell.
    """
    if tile <= 0:
        raise ValueError("tile must be positive")
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
                        drain_radius_m, drain_core_frac, verbose, requested)
        finally:
            if mask_src is not None:
                mask_src.close()


def _run(src, mask_src, dem_path, mask_path, height, width, crs, transform, size, tier,
         geographic, areas, drains, buildings, tile, res, min_depth, drain_radius_m,
         drain_core_frac, verbose, requested):
    boxes = list(_tile_boxes(height, width, tile))
    radius = metres_to_cells(drain_radius_m, size)
    drain_rc = drain_pixels(drains, transform, (height, width)) if drains is not None else None

    # ---- pass 1: flood every tile from its border and keep only the crossings
    tiles = []
    blocks = np.zeros((-(-height // 16), -(-width // 16)), dtype=bool)   # blocks holding invalid pixels
    for k, box in enumerate(boxes, 1):
        r0, r1, c0, c1 = box
        work, valid = _read_tile(src, mask_src, box)
        mark_invalid_blocks(blocks, ~valid, (r0, c0))
        rows, cols, pinned, border = tile_seeds(valid, (r0 == 0, r1 == height, c0 == 0, c1 == width))
        z = work[rows, cols]
        level, root = flood_from_seeds(work, valid, rows, cols, z)
        ea, eb, ew = region_edges(level, root)
        edge_mask = np.zeros(valid.shape, dtype=bool)
        edge_mask[0, :] = edge_mask[-1, :] = edge_mask[:, 0] = edge_mask[:, -1] = True
        bad_r, bad_c = np.nonzero(edge_mask & ~valid)
        tiles.append({
            "rows": rows, "cols": cols, "z": z, "pinned": pinned, "border": border,
            "keys": (rows + r0).astype(np.int64) * width + (cols + c0),
            "ea": ea, "eb": eb, "ew": ew,
            "invalid_border_keys": (bad_r + r0).astype(np.int64) * width + (bad_c + c0)})
        if verbose:
            print(f"pass 1, tile {k}/{len(boxes)}: {len(z)} seeds, {len(ea)} crossings", flush=True)

    # ---- the exact level of every border cell
    levels, graph = _solve_borders(tiles, width, height)

    # ---- pass 2: flood each tile from the exact border levels, then summarise its depressions
    records, rings, cell_parts = [], [], []
    n_rec = 0
    for k, (box, t, lv) in enumerate(zip(boxes, tiles, levels), 1):
        if len(t["z"]) == 0:        # nothing valid in this tile
            continue
        r0, r1, c0, c1 = box
        h, w = r1 - r0, c1 - c0
        work, valid = _read_tile(src, mask_src, box)
        filled, _ = flood_from_seeds(work, valid, t["rows"], t["cols"], lv)
        depth = np.where(valid, filled - work, 0.0)
        window_transform = src.window_transform(Window(c0, r0, w, h))
        bmask = None
        if buildings is not None:
            bmask = rasterize_buildings(
                _buildings_in(buildings, _bounds(window_transform, (h, w))), window_transform,
                (h, w), min_area=0.0 if geographic else 20.0)
        near = (near_drain_mask(drain_rc[0], drain_rc[1], (h, w), (r0, c0), radius)
                if drain_rc is not None else None)
        out = _tile_records(depth, box, width, areas[r0:r1], bmask, near, min_depth)
        if out is None:
            continue
        agg, pixels, labels = out
        agg = agg.assign(rec=np.arange(n_rec, n_rec + len(agg)))
        lab2rec = np.full(int(labels.max()) + 1, -1, dtype=np.int64)
        lab2rec[agg["lab"].to_numpy()] = agg["rec"].to_numpy()
        # border pixels, to join pieces of one depression across tile borders
        ring = np.zeros((h, w), dtype=bool)
        ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
        pr, pc = np.nonzero(ring & (labels > 0))
        rings.append(((pr + r0).astype(np.int64) * width + (pc + c0), lab2rec[labels[pr, pc]]))
        # pixels of kept pieces, as H3 cells
        lon, lat = lonlat(crs, *pixel_centers(transform, pixels["r"].to_numpy(),
                                              pixels["c"].to_numpy()))
        pixels = pixels.assign(cell=h3_ids(lat, lon, res), rec=lab2rec[pixels["lab"].to_numpy()])
        cell_parts.append(pixels.groupby(["rec", "cell"]).agg(
            max_depth_m=("d", "max"), depress_area_m2=("a", "sum"),
            depress_volume_m3=("v", "sum")).reset_index())
        records.append(agg)
        n_rec += len(agg)
        if verbose:
            print(f"pass 2, tile {k}/{len(boxes)}: {len(agg)} pieces", flush=True)

    meta = base_meta(str(dem_path), crs, (height, width), size, tier, geographic, res, min_depth,
                     drain_radius_m, drain_core_frac, drains, buildings, res_requested=requested)
    meta["tiling"] = {"tile": tile, "n_tiles": len(boxes), **graph}
    has_buildings = buildings is not None
    columns = dep_columns(drains is not None, has_buildings)
    footprint = Footprint((height, width), transform, crs, blocks, file_opener(dem_path, mask_path))
    if not records:
        meta.update({"n_depressions": 0, "n_edge": 0})
        return Result(pd.DataFrame(columns=columns), pd.DataFrame(columns=CELL_TABLE), meta,
                      transform=transform, crs=crs, footprint=footprint)

    # ---- join the pieces of each depression and apply min_depth to the whole depression
    recs = pd.concat(records, ignore_index=True)
    ring_keys = np.concatenate([r[0] for r in rings])
    ring_rec = np.concatenate([r[1] for r in rings])
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
        "h3_lowpoint": [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)],
        "h3_lowpoint_15": [h3.latlng_to_cell(la, lo, FINEST_H3) for la, lo in zip(lat, lon)],
        "edge": ((dep["rmin"] <= 1) | (dep["cmin"] <= 1) | (dep["rmax"] + 1 >= height - 1)
                 | (dep["cmax"] + 1 >= width - 1)).to_numpy()})
    if drains is not None:
        out["dist_to_drain_m"] = nearest_distance_m(drains, np.c_[xs, ys], crs)
        out["drain_at_core"] = (dep["near_max"] >= drain_core_frac * dep["maxd"]).to_numpy()
    if has_buildings:
        out["building_overlap"] = (dep["bsum"] / dep["npx"]).to_numpy()
        out["likely_building_artifact"] = out["building_overlap"] >= 0.5

    cells = pd.concat(cell_parts, ignore_index=True)
    cells = cells[cells["rec"].map(recs["comp"]).isin(dep.index)]
    cells = cells.groupby("cell").agg(
        max_depth_m=("max_depth_m", "max"), depress_area_m2=("depress_area_m2", "sum"),
        depress_volume_m3=("depress_volume_m3", "sum")).reset_index()
    cells["h3"] = [h3.int_to_str(int(c)) for c in cells["cell"]]
    meta.update({"n_depressions": len(out), "n_edge": int(out["edge"].sum())})
    return Result(out[columns], finish_cells(cells[CELL_COLUMNS], out["h3_lowpoint"], footprint),
                  meta, transform=transform, crs=crs, footprint=footprint)
