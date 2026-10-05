import numpy as np
from scipy import ndimage

CODES = {"minor": 1, "drained": 2, "suspect_pit": 3, "unexplained": 4}


def touches_edge(labels, ids):
    """True for depressions that touch the raster border (their true extent is cut off)."""
    h, w = labels.shape
    objs = ndimage.find_objects(labels)
    return np.array([objs[i - 1][0].start == 0 or objs[i - 1][1].start == 0 or
                     objs[i - 1][0].stop == h or objs[i - 1][1].stop == w for i in ids], dtype=bool)


def serving_drains(labels, depth, ids, max_depth, drains_xy, transform, core_frac=0.8, radius=5):
    """For each depression: is a drain within `radius` cells of its deep core?

    The core is the part of the depression deeper than core_frac * its maximum depth.
    `drains_xy` must be in the same CRS as the DEM.
    """
    h, w = labels.shape
    lut = np.zeros(int(labels.max()) + 1)
    lut[ids] = max_depth
    core = np.where((labels > 0) & (depth >= core_frac * lut[labels]), labels, 0)
    inv = ~transform
    found = set()
    for x, y in drains_xy:
        c, r = inv @ (x, y)
        r, c = int(np.floor(r)), int(np.floor(c))
        if r < 0 or c < 0 or r >= h or c >= w:
            continue
        win = core[max(0, r - radius):r + radius + 1, max(0, c - radius):c + radius + 1]
        found.update(np.unique(win[win > 0]).tolist())
    return np.isin(ids, list(found))


def classify(max_depth, area, served, edge, sig_depth=0.3, sig_area=150.0,
             pit_depth=1.5, pit_area=150.0):
    significant = (max_depth >= sig_depth) & (area >= sig_area) & ~edge
    pit = (max_depth >= pit_depth) & (area < pit_area) & ~edge
    cls = np.where(served, "drained", np.where(pit, "suspect_pit",
                   np.where(significant, "unexplained", "minor")))
    return cls, significant, pit
