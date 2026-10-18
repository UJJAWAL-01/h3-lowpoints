import heapq

import numpy as np
import rasterio
from numba import njit
from scipy import ndimage


@njit(cache=True)
def fill_depressions(dem, valid, drains, open_sides):
    """Priority-flood fill. Returns the filled surface and a per-cell `provisional` flag.

    The raster border, cells next to invalid cells, and `drains` are outlets. `open_sides` marks
    which of the four borders (top, bottom, left, right) are artificial window cuts rather than
    real edges of the data. A cell is provisional when its water level was set by a path to an
    artificial cut: with more data beyond the cut the level could be higher. At equal height a
    real outlet wins over an artificial cut (the heap orders ties by the provisional flag), so only
    genuinely ambiguous cells are marked.
    """
    h, w = dem.shape
    filled = dem.copy()
    closed = np.zeros((h, w), np.uint8)
    provisional = np.zeros((h, w), np.uint8)
    dr = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
    dc = np.array([-1, 0, 1, -1, 1, -1, 0, 1])
    heap = [(-1e30, 0, -1)]  # dummy entry so numba can infer the list type

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
                if ((r == 0 and open_sides[0]) or (r == h - 1 and open_sides[1])
                        or (c == 0 and open_sides[2]) or (c == w - 1 and open_sides[3])):
                    provisional[r, c] = 1
                heapq.heappush(heap, (dem[r, c], np.int64(provisional[r, c]), r * w + c))

    while len(heap) > 0:
        z, _, idx = heapq.heappop(heap)
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
            provisional[rr, cc] = provisional[r, c]
            zz = dem[rr, cc]
            if zz < z:
                zz = z
            filled[rr, cc] = zz
            heapq.heappush(heap, (zz, np.int64(provisional[rr, cc]), rr * w + cc))
    return filled, provisional


def depth_and_provisional(arr, nodata_mask=None, drains=None, open_sides=(False,) * 4):
    """Depression depth raster plus a bool raster of cells whose depth is only a lower bound."""
    arr = arr.astype(np.float64)
    invalid = np.isnan(arr) if nodata_mask is None else (nodata_mask | np.isnan(arr))
    work = np.where(invalid, 0.0, arr)
    d = np.zeros(arr.shape, dtype=np.bool_) if drains is None else drains.astype(np.bool_)
    filled, provisional = fill_depressions(work, ~invalid, d, np.asarray(open_sides, dtype=np.bool_))
    depth = filled - work
    depth[invalid] = 0.0
    return depth, provisional.astype(bool)


def depth_from_array(arr, nodata_mask=None, drains=None):
    """Depression depth raster. `drains` is an optional bool raster of cells that drain away."""
    return depth_and_provisional(arr, nodata_mask, drains)[0]


# The fill spreads through all 8 neighbours, so a depression is an 8-connected region of
# positive depth. Labelling with the default 4-connectivity would split pits that touch at a corner.
EIGHT = np.ones((3, 3), dtype=int)


def label_depressions(depth, cell_area, min_depth=0.15):
    """Label 8-connected regions of positive depth and keep those at least `min_depth` deep.

    Returns (labels, stats). Label values are not renumbered after filtering, so `stats["id"]`
    lists the surviving label values.
    """
    labels, n = ndimage.label(depth > 0, structure=EIGHT)
    if n == 0:
        return labels, {"id": [], "max_depth_m": [], "area_m2": [], "volume_m3": []}
    # Summaries run over the labelled pixels only: scipy's labelled reductions sort every pixel
    # of the raster, which dominates the run time on large DEMs.
    inside = labels > 0
    lab, d = labels[inside], depth[inside]
    idx = np.arange(1, n + 1)
    max_d = np.asarray(ndimage.maximum(d, lab, idx))
    keep = idx[max_d >= min_depth]
    out = np.where(np.isin(labels, keep), labels, 0)
    counts = np.bincount(lab, minlength=n + 1)
    sums = np.bincount(lab, weights=d, minlength=n + 1)
    return out, {
        "id": keep,
        "max_depth_m": max_d[keep - 1],
        "area_m2": counts[keep] * cell_area,
        "volume_m3": sums[keep] * cell_area,
    }


def depth_from_file(path):
    with rasterio.open(path) as src:
        arr = src.read(1)
        nodata = src.nodata
        mask = np.isnan(arr) if nodata is None else (arr == nodata)
        return depth_from_array(arr, mask), src.transform, src.crs


def lowest_points(depth, labels, ids):
    """Row and column of each depression's deepest cell.

    Ties go to the first cell in row-major order, so the answer is the same whichever window of
    a larger raster the depression is analyzed in. scipy's maximum_position makes no such
    promise, which made tiled runs drop depressions on some platforms.
    """
    ids = np.asarray(ids)
    lut = np.zeros(int(labels.max()) + 1)
    flat_labels = labels.ravel()
    idx = np.flatnonzero(flat_labels)
    lab = flat_labels[idx]
    lut[ids] = ndimage.maximum(depth.ravel()[idx], lab, ids)
    top = depth.ravel()[idx] >= lut[lab] - 1e-9
    idx, lab = idx[top], lab[top]
    first_labels, first = np.unique(lab, return_index=True)
    at = np.searchsorted(first_labels, ids)
    rows, cols = np.unravel_index(idx[first][at], labels.shape)
    return rows, cols


def touches_edge(labels, ids, margin=1):
    """True for depressions within `margin` cells of the raster border.

    The fill treats the border as an outlet, so border cells never hold depth: a depression
    whose true extent is cut off by the border shows up one cell inside it.
    """
    h, w = labels.shape
    objs = ndimage.find_objects(labels)
    out = []
    for i in ids:
        rows, cols = objs[i - 1]
        out.append(rows.start <= margin or cols.start <= margin
                   or rows.stop >= h - margin or cols.stop >= w - margin)
    return np.array(out, dtype=bool)
