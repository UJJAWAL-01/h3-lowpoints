import glob

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

BBOX = (-97.76, 30.29, -97.72, 30.32)
for p in sorted(glob.glob("data/raw/*.tif")):
    with rasterio.open(p) as s:
        b = transform_bounds("EPSG:4326", s.crs, *BBOX)
        print("\n", p)
        print(" shape", s.shape, "| crs", s.crs, "| nodata", s.nodata, "| dtype", s.dtypes[0])
        print(" tile bounds :", tuple(round(x) for x in s.bounds))
        print(" pilot bounds:", tuple(round(x) for x in b))
        small = s.read(1, out_shape=(s.height // 50, s.width // 50)).astype("float64")
        ok = small[small > -1e30]
        print(" coarse sample of the whole tile -> min / median / max:",
              round(float(ok.min()), 1), round(float(np.median(ok)), 1), round(float(ok.max()), 1))
        print(" fraction of exact zeros:", round(float((ok == 0).mean()), 3))
