import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window

from .pipeline import DEP_COLS, Result, analyze_array


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


def analyze_tiled(dem_path, drains_xy=None, buildings=None, tile=4096, buffer=256,
                  verbose=False, **params):
    """Analyze a large DEM in tiles with overlapping borders and combine the results.

    Each depression is kept by the tile whose core contains its lowest point. Depressions that
    touch the border of a processed window are flagged `edge` (their true extent may be larger).
    """
    if tile <= 0 or buffer < 0:
        raise ValueError("tile must be positive and buffer must not be negative")
    radius = params.get("radius", 5)
    parts, cell_parts, first_meta = [], [], None
    with rasterio.open(dem_path) as src:
        height, width, crs, nodata = src.height, src.width, src.crs, src.nodata
        boxes = list(_tile_boxes(height, width, tile))
        for k, (r0, r1, c0, c1) in enumerate(boxes, 1):
            br0, br1 = max(0, r0 - buffer), min(height, r1 + buffer)
            bc0, bc1 = max(0, c0 - buffer), min(width, c1 + buffer)
            win = Window(bc0, br0, bc1 - bc0, br1 - br0)
            arr = src.read(1, window=win)
            transform = src.window_transform(win)
            bounds = _bounds(transform, arr.shape)
            pad = (radius + 1) * abs(transform.a)
            res = analyze_array(
                arr, transform, crs, nodata,
                drains_xy=_drains_in(drains_xy, bounds, pad),
                buildings=_buildings_in(buildings, bounds),
                core=(r0 - br0, r1 - br0, c0 - bc0, c1 - bc0),
                source=str(dem_path), **params)
            if verbose:
                print(f"tile {k}/{len(boxes)}: {len(res.depressions)} depressions", flush=True)
            if first_meta is None:
                first_meta = res.meta
            if len(res.depressions):
                parts.append(res.depressions)
            if len(res.cells):
                cell_parts.append(res.cells)

    if parts:
        deps = pd.concat(parts, ignore_index=True)
        deps["id"] = np.arange(1, len(deps) + 1)
    else:
        deps = pd.DataFrame(columns=DEP_COLS)
    if cell_parts:
        cells = (pd.concat(cell_parts, ignore_index=True).groupby("h3", as_index=False)
                 .agg(max_depth_m=("max_depth_m", "max"), code=("code", "max"),
                      depress_area_m2=("depress_area_m2", "sum"),
                      depress_volume_m3=("depress_volume_m3", "sum")))
    else:
        cells = pd.DataFrame()
    meta = dict(first_meta)
    meta.update({"shape": [height, width], "n_depressions": len(deps),
                 "class_counts": deps["cls"].value_counts().to_dict(),
                 "tiling": {"tile": tile, "buffer": buffer, "n_tiles": len(boxes)}})
    return Result(deps, cells, meta)
