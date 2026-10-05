import numpy as np
import rasterio

from h3lowpoints.hydro.depressions import depth_from_array

with rasterio.open("data/pilot_dem.tif") as s:
    arr = s.read(1)
    nodata = s.nodata
    print("shape", arr.shape, "| dtype", arr.dtype, "| nodata", nodata, "| crs", s.crs, "| res", s.res)

mask = np.isnan(arr)
if nodata is not None and not np.isnan(nodata):
    mask |= arr == nodata
print("nodata fraction:", round(float(mask.mean()), 4))

valid = arr[~mask]
print("elevation min / median / max:", float(valid.min()), float(np.median(valid)), float(valid.max()))

d = depth_from_array(arr, mask)
print("cells with depth > 0:", int((d > 0).sum()))
for t in (0.01, 0.05, 0.10, 0.15, 0.30):
    print(f"cells with depth >= {t} m:", int((d >= t).sum()))
print("max depth (m):", float(d.max()))
