import heapq

import numpy as np
import rasterio
from numba import njit
from scipy import ndimage


@njit(cache=True)
def fill_depressions(dem, valid, drains):
    h, w = dem.shape
    filled = dem.copy()
    closed = np.zeros((h, w), np.uint8)
    dr = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
    dc = np.array([-1, 0, 1, -1, 1, -1, 0, 1])
    heap = [(-1e30, -1)]  # dummy entry so numba can infer the list type

    for r in range(h):
        for c in range(w):
            if not valid[r, c]:
                closed[r, c] = 1

    for r in range(h):
        for c in range(w):
            if closed[r, c] == 1:
                continue
            seed = r == 0 or c == 0 or r == h - 1 or c == w - 1 or drains[r, c]
            if not seed:
                for k in range(8):
                    if not valid[r + dr[k], c + dc[k]]:
                        seed = True
            if seed:
                closed[r, c] = 1
                heapq.heappush(heap, (dem[r, c], r * w + c))

    while len(heap) > 0:
        z, idx = heapq.heappop(heap)
        if idx < 0:
            continue
        r = idx // w
        c = idx % w
        for k in range(8):
            rr = r + dr[k]
            cc = c + dc[k]
            if rr < 0 or rr >= h or cc < 0 or cc >= w:
                continue
            if closed[rr, cc] == 1:
                continue
            closed[rr, cc] = 1
            zz = dem[rr, cc]
            if zz < z:
                zz = z
            filled[rr, cc] = zz
            heapq.heappush(heap, (zz, rr * w + cc))
    return filled


def depth_from_array(arr, nodata_mask=None, drains=None):
    """Depression depth raster. `drains` is an optional bool raster of cells that drain away."""
    arr = arr.astype(np.float64)
    invalid = np.isnan(arr) if nodata_mask is None else (nodata_mask | np.isnan(arr))
    work = np.where(invalid, 0.0, arr)
    d = np.zeros(arr.shape, dtype=np.bool_) if drains is None else drains.astype(np.bool_)
    filled = fill_depressions(work, ~invalid, d)
    depth = filled - work
    depth[invalid] = 0.0
    return depth


def label_depressions(depth, cell_area, min_depth=0.15):
    labels, n = ndimage.label(depth > 0)
    if n == 0:
        return labels, {"id": [], "max_depth_m": [], "area_m2": [], "volume_m3": []}
    idx = np.arange(1, n + 1)
    max_d = np.asarray(ndimage.maximum(depth, labels, idx))
    keep = idx[max_d >= min_depth]
    out = np.where(np.isin(labels, keep), labels, 0)
    return out, {
        "id": keep,
        "max_depth_m": max_d[keep - 1],
        "area_m2": np.asarray(ndimage.sum(depth > 0, labels, keep)) * cell_area,
        "volume_m3": np.asarray(ndimage.sum(depth, labels, keep)) * cell_area,
    }


def depth_from_file(path):
    with rasterio.open(path) as src:
        arr = src.read(1)
        nodata = src.nodata
        mask = np.isnan(arr) if nodata is None else (arr == nodata)
        return depth_from_array(arr, mask), src.transform, src.crs
