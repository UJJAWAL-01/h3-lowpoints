import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window

from .pipeline import Result, analyze_array, dep_columns, plan_analysis
from .resolution import metres_to_cells


def _tile_boxes(height, width, tile):
    for r0 in range(0, height, tile):
        for c0 in range(0, width, tile):
            yield r0, min(r0 + tile, height), c0, min(c0 + tile, width)


def _bounds(transform, shape):
    h, w = shape
    left, top = transform.c, transform.f
    right, bottom = left + w * transform.a, top + h * transform.e
    return left, min(bottom, top), right, max(bottom, top)


def _drains_in(xy, bounds, pad):
    if xy is None or len(xy) == 0:
        return None
    xy = np.asarray(xy)
    left, bottom, right, top = bounds
    m = ((xy[:, 0] >= left - pad) & (xy[:, 0] <= right + pad)
         & (xy[:, 1] >= bottom - pad) & (xy[:, 1] <= top + pad))
    return xy[m] if m.any() else None


def _buildings_in(buildings, bounds):
    if buildings is None or not hasattr(buildings, "cx"):
        return buildings
    left, bottom, right, top = bounds
    return buildings.cx[left:right, bottom:top]


def analyze_tiled(dem_path, *, drains_xy=None, buildings=None, tile=4096, buffer=256,
                  res=11, min_depth=0.15, waterlogging=None, verbose=False):
    """Analyze a large DEM in tiles with overlapping borders and combine the results.

    Each depression is kept by the tile whose core contains its lowest point. A tile treats its
    border as an outlet, so a lake that really spills beyond the buffer is seen at a lower level
    and may be split into pieces. Depressions whose water level depends on such a border are
    flagged `edge` (count in `meta["n_edge"]`); if many interior depressions are flagged,
    raise `buffer`.
    """
    if tile <= 0 or buffer < 0:
        raise ValueError("tile must be positive and buffer must not be negative")
    has_drains = drains_xy is not None and len(drains_xy) > 0
    parts, cell_parts, first_meta = [], [], None
    with rasterio.open(dem_path) as src:
        height, width, crs, nodata = src.height, src.width, src.crs, src.nodata
        plan = plan_analysis(crs, src.transform, res, min_depth, waterlogging, has_drains)
        pad = (metres_to_cells(plan[2].drain_radius_m if plan[2] else 5.0, plan[0]) + 1) * plan[0]
        boxes = list(_tile_boxes(height, width, tile))
        for k, (r0, r1, c0, c1) in enumerate(boxes, 1):
            br0, br1 = max(0, r0 - buffer), min(height, r1 + buffer)
            bc0, bc1 = max(0, c0 - buffer), min(width, c1 + buffer)
            win = Window(bc0, br0, bc1 - bc0, br1 - br0)
            arr = src.read(1, window=win)
            transform = src.window_transform(win)
            bounds = _bounds(transform, arr.shape)
            res_k = analyze_array(
                arr, transform, crs, nodata,
                drains_xy=_drains_in(drains_xy, bounds, pad),
                buildings=_buildings_in(buildings, bounds),
                res=res, min_depth=min_depth, waterlogging=waterlogging,
                core=(r0 - br0, r1 - br0, c0 - bc0, c1 - bc0),
                open_sides=(br0 > 0, br1 < height, bc0 > 0, bc1 < width),
                source=str(dem_path), _plan=plan)
            if verbose:
                print(f"tile {k}/{len(boxes)}: {len(res_k.depressions)} depressions", flush=True)
            if first_meta is None:
                first_meta = res_k.meta
            if len(res_k.depressions):
                parts.append(res_k.depressions)
            if len(res_k.cells):
                cell_parts.append(res_k.cells)

    meta = dict(first_meta)
    if parts:
        deps = pd.concat(parts, ignore_index=True)
        deps["id"] = np.arange(1, len(deps) + 1)
    else:
        deps = pd.DataFrame(columns=dep_columns(has_drains, buildings is not None, plan[2]))
    if cell_parts:
        stacked = pd.concat(cell_parts, ignore_index=True)
        spec = {"max_depth_m": ("max_depth_m", "max"),
                "depress_area_m2": ("depress_area_m2", "sum"),
                "depress_volume_m3": ("depress_volume_m3", "sum")}
        if "code" in stacked:
            spec["code"] = ("code", "max")
        cells = stacked.groupby("h3", as_index=False).agg(**spec)
    else:
        cells = pd.DataFrame()
    n_edge = int(deps["edge"].sum()) if len(deps) else 0
    meta.update({"shape": [height, width], "n_depressions": len(deps), "n_edge": n_edge,
                 "tiling": {"tile": tile, "buffer": buffer, "n_tiles": len(boxes)}})
    if "cls" in deps:
        meta["class_counts"] = deps["cls"].value_counts().to_dict()
    return Result(deps, cells, meta)
