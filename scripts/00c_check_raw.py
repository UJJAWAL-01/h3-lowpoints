import glob

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window

BBOX = (-97.76, 30.29, -97.72, 30.32)


def stats(name, a, nodata):
    a = a.astype("float64")
    nan = np.isnan(a)
    nd = (a < -1e30) if nodata is None else ((a == nodata) | (a < -1e30))
    valid = ~nan & ~nd
    line = (f"  {name:24s} valid={valid.mean():.3f} nan={nan.mean():.3f} "
            f"nodata={nd.mean():.3f} zeros={(a == 0).mean():.3f}")
    if valid.any():
        v = a[valid]
        line += f" | min/median/max = {v.min():.1f} / {np.median(v):.1f} / {v.max():.1f}"
    else:
        line += " | NO VALID PIXELS"
    print(line)


for p in sorted(glob.glob("data/raw/*.tif")):
    with rasterio.open(p) as s:
        print("\n", p)
        print("  size", s.width, s.height, "| nodata", s.nodata, "| compression",
              s.compression, "| block", s.block_shapes[0], "| overviews", s.overviews(1))

        r, c = s.height // 2, s.width // 2
        stats("tile center 500x500", s.read(1, window=Window(c - 250, r - 250, 500, 500)), s.nodata)

        left, bottom, right, top = transform_bounds("EPSG:4326", s.crs, *BBOX)
        x0, x1 = max(left, s.bounds.left), min(right, s.bounds.right)
        y0, y1 = max(bottom, s.bounds.bottom), min(top, s.bounds.top)
        if x0 < x1 and y0 < y1:
            r0, c0 = s.index(x0, y1)
            r1, c1 = s.index(x1, y0)
            r0, c0 = max(r0, 0), max(c0, 0)
            r1, c1 = min(r1, s.height), min(c1, s.width)
            win = Window(c0, r0, c1 - c0, r1 - r0)
            print("  pilot overlap window:", int(win.width), "x", int(win.height), "pixels")
            stats("pilot overlap", s.read(1, window=win), s.nodata)
        else:
            print("  pilot box does not overlap this tile")
