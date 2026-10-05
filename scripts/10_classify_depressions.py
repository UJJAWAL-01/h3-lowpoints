import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from shapely.geometry import shape

from h3lowpoints.hydro.depressions import depth_from_file, label_depressions
from h3lowpoints.sources.assets import load_clipped

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
SNAP = 10.0  # metres; asset positions come partly from record drawings

depth, transform, crs = depth_from_file(DEM)
labels, st = label_depressions(depth, 1.0, min_depth=0.15)
H, W = depth.shape

polys = [
    (int(v), shape(g))
    for g, v in features.shapes(labels.astype("int32"), mask=labels > 0, transform=transform)
]
dep = gpd.GeoDataFrame(
    {"id": [p[0] for p in polys]}, geometry=[p[1] for p in polys], crs=crs
).dissolve(by="id").reset_index()
stats = pd.DataFrame({k: st[k] for k in ("id", "max_depth_m", "area_m2", "volume_m3")})
dep = dep.merge(stats, on="id")

combo = load_clipped("combo_inlet", BBOX, crs)
bridge = load_clipped("bridge_inlet", BBOX, crs)
pipes = load_clipped("drainage_pipe", BBOX, crs)
chans = load_clipped("open_channel", BBOX, crs)
inlets = gpd.GeoDataFrame(pd.concat([combo[["geometry"]], bridge[["geometry"]]], ignore_index=True), crs=crs)


def count_near(assets, label):
    buf = dep[["id", "geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(SNAP)
    j = gpd.sjoin(buf, assets[["geometry"]], predicate="intersects", how="inner")
    return j.groupby("id").size().rename(label)


for s in (count_near(inlets, "n_inlet"), count_near(pipes, "n_pipe"), count_near(chans, "n_channel")):
    dep = dep.merge(s, on="id", how="left")
for c in ("n_inlet", "n_pipe", "n_channel"):
    dep[c] = dep[c].fillna(0).astype(int)


def classify(r):
    if r.n_inlet > 0:
        return "inlet_nearby"
    if r.n_pipe > 0:
        return "pipe_nearby"
    if r.n_channel > 0:
        return "channel_nearby"
    return "no_recorded_asset"


dep["cls"] = dep.apply(classify, axis=1)

b = dep.geometry.bounds
left, top = transform.c, transform.f
right, bottom = left + W * transform.a, top + H * transform.e
dep["edge"] = (b.minx <= left + 2) | (b.maxx >= right - 2) | (b.miny <= bottom + 2) | (b.maxy >= top - 2)

ll = dep.geometry.centroid.to_crs(4326)
dep["lat"], dep["lon"] = ll.y, ll.x

print(f"{len(dep)} depressions (>= 0.15 m); {int(dep.edge.sum())} touch the raster edge\n")
print(dep.groupby("cls").agg(n=("id", "count"), volume_m3=("volume_m3", "sum"),
                             median_depth_m=("max_depth_m", "median")).round(1).to_string())

cand = dep[(dep.cls == "no_recorded_asset") & ~dep.edge].sort_values("max_depth_m", ascending=False).head(15)
print("\nDeepest depressions with NO recorded asset within", SNAP, "m (not on edge):")
for _, r in cand.iterrows():
    print(f"  {r.max_depth_m:5.2f} m | {r.area_m2:7.0f} m2 | {r.volume_m3:8.0f} m3 | "
          f"https://www.google.com/maps?q={r.lat:.6f},{r.lon:.6f}")

dep.drop(columns="geometry").to_csv("data/pilot_depressions_classified.csv", index=False)
dep.to_file("data/pilot_depressions.gpkg", driver="GPKG")
