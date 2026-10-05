import numpy as np


def drains_to_raster(points, dem, transform, snap_cells=6, invalid=None):
    """Mark drain cells on the DEM grid, snapping each point to the lowest cell nearby."""
    h, w = dem.shape
    out = np.zeros((h, w), dtype=bool)
    inv = ~transform
    for g in points.geometry:
        col, row = inv @ (g.x, g.y)
        r, c = int(row), int(col)
        if r < 0 or c < 0 or r >= h or c >= w:
            continue
        r0, r1 = max(0, r - snap_cells), min(h, r + snap_cells + 1)
        c0, c1 = max(0, c - snap_cells), min(w, c + snap_cells + 1)
        win = dem[r0:r1, c0:c1].astype("float64")
        if invalid is not None:
            win = np.where(invalid[r0:r1, c0:c1], np.inf, win)
        k = int(np.argmin(win))
        if not np.isfinite(win.flat[k]):
            continue
        rr, cc = np.unravel_index(k, win.shape)
        out[r0 + rr, c0 + cc] = True
    return out
