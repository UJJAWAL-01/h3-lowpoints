import numpy as np
import rasterio

from h3lowpoints.sources.assets import drain_points, load_clipped, points_to_xy

BBOX = (-97.76, 30.29, -97.72, 30.32)
with rasterio.open("data/pilot_dem.tif") as s:
    transform, crs, (H, W) = s.transform, s.crs, s.shape
xy = points_to_xy(drain_points(load_clipped("combo_inlet", BBOX, crs), load_clipped("bridge_inlet", BBOX, crs),
                               load_clipped("drainage_pipe", BBOX, crs), 2017))
c, r = (~transform) @ (xy[:, 0], xy[:, 1])
sliver = ((r > -1) & (c > -1) & (r < H) & (c < W)) & ((r < 0) | (c < 0))
print("drains within one cell outside the raster edge (truncation vs floor):", int(sliver.sum()))
