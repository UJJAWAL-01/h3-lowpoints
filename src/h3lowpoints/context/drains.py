import numpy as np
from scipy import ndimage


def drain_pixels(drains_xy, transform, shape):
    """Row and column of each drain that falls inside the raster."""
    xy = np.asarray(drains_xy, dtype="float64").reshape(-1, 2)
    cols, rows = ~transform @ (xy[:, 0], xy[:, 1])
    rows, cols = np.floor(rows).astype(int), np.floor(cols).astype(int)
    inside = (rows >= 0) & (cols >= 0) & (rows < shape[0]) & (cols < shape[1])
    return rows[inside], cols[inside]


def drains_at_core(labels, depth, ids, max_depth, drains_xy, transform, core_frac=0.8,
                   radius_cells=5):
    """For each depression: is a drain within `radius_cells` cells of its deep core?

    The core is the part of the depression deeper than core_frac * its maximum depth.
    `drains_xy` must be in the same CRS as the DEM.
    """
    lut = np.zeros(int(labels.max()) + 1)
    lut[ids] = max_depth
    core = np.where((labels > 0) & (depth >= core_frac * lut[labels]), labels, 0)
    rows, cols = drain_pixels(drains_xy, transform, labels.shape)
    mask = np.zeros(labels.shape, dtype=bool)
    mask[rows, cols] = True
    near = ndimage.maximum_filter(mask, size=2 * radius_cells + 1, mode="constant")
    return np.isin(ids, np.unique(core[near & (core > 0)]))


def near_drain_mask(rows, cols, shape, origin, radius_cells):
    """Pixels of a window within `radius_cells` (square neighbourhood) of any drain.

    `rows`, `cols` are drain pixels in the coordinates of the whole raster, `shape` is the
    window's size and `origin` its (row, col) offset. Drains outside the window count when they
    are within reach of it.
    """
    r0, c0 = origin
    h, w = shape
    mask = np.zeros((h + 2 * radius_cells, w + 2 * radius_cells), dtype=bool)
    rr, cc = rows - r0 + radius_cells, cols - c0 + radius_cells
    inside = (rr >= 0) & (cc >= 0) & (rr < mask.shape[0]) & (cc < mask.shape[1])
    mask[rr[inside], cc[inside]] = True
    near = ndimage.maximum_filter(mask, size=2 * radius_cells + 1, mode="constant")
    return near[radius_cells:radius_cells + h, radius_cells:radius_cells + w]
