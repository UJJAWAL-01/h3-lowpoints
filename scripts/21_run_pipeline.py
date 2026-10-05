import rasterio

from h3lowpoints import analyze
from h3lowpoints.sources.assets import drain_points, load_clipped, points_to_xy

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
with rasterio.open(DEM) as s:
    crs = s.crs

pts = drain_points(load_clipped("combo_inlet", BBOX, crs), load_clipped("bridge_inlet", BBOX, crs),
                   load_clipped("drainage_pipe", BBOX, crs), 2017)
xy = points_to_xy(pts)
res = analyze(DEM, drains_xy=xy)
print(len(res.depressions), "depressions;", len(xy), "drains;", len(res.cells), "H3 cells")
print(res.depressions["cls"].value_counts().to_dict())
print("significant:", int(res.depressions.significant.sum()))
res.save("outputs/pilot")
print("saved to outputs/pilot (depressions.csv, cells.csv, meta.json)")
