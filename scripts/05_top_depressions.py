import numpy as np
from pyproj import Transformer

from h3lowpoints.hydro.depressions import depth_from_file, label_depressions

depth, transform, crs = depth_from_file("data/pilot_dem.tif")
labels, s = label_depressions(depth, 1.0)
ids = np.asarray(s["id"])
order = np.argsort(-np.asarray(s["max_depth_m"]))[:15]
to_ll = Transformer.from_crs(crs, 4326, always_xy=True)

print("rank | depth m | area m2 | volume m3 | location")
for rank, k in enumerate(order, 1):
    mask = labels == ids[k]
    r, c = np.unravel_index(np.argmax(np.where(mask, depth, -1)), depth.shape)
    x, y = transform * (c + 0.5, r + 0.5)
    lon, lat = to_ll.transform(x, y)
    print(f"{rank:4d} | {s['max_depth_m'][k]:7.2f} | {s['area_m2'][k]:7.0f} | "
          f"{s['volume_m3'][k]:9.0f} | https://www.google.com/maps?q={lat:.6f},{lon:.6f}")
