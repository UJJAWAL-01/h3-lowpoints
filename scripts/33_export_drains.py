import numpy as np
import rasterio

from h3lowpoints.sources.assets import drain_points, load_clipped, points_to_xy

BBOX = (-97.76, 30.29, -97.72, 30.32)
with rasterio.open("data/pilot_dem.tif") as s:
    crs = s.crs
xy = points_to_xy(drain_points(load_clipped("combo_inlet", BBOX, crs, tag="pilot"),
                               load_clipped("bridge_inlet", BBOX, crs, tag="pilot"),
                               load_clipped("drainage_pipe", BBOX, crs, tag="pilot"), 2017))
np.savetxt("data/pilot_drains.csv", xy, delimiter=",", header="x,y", comments="", fmt="%.3f")
print(len(xy), "drain points written")
