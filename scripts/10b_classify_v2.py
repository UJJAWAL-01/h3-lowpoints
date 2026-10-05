import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio import features
from shapely.geometry import Point, shape

from h3lowpoints.hydro.depressions import depth_from_file, label_depressions
from h3lowpoints.sources.assets import load_clipped

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
LIDAR_YEAR = 2017
SNAP_INLET, SNAP_CHANNEL, SNAP_PIPE_END = 15.0, 10.0, 15.0


def col(g, name):
    return g[name] if name in g.columns else pd.Series(pd.NA, index=g.index, dtype="string")


def existed(g):
    """Keep assets that plausibly existed and worked when the lidar was flown."""
    yb = pd.to_numeric(col(g, "year_built"), errors="coerce").astype("float64")
    ya = pd.to_numeric(col(g, "year_abandoned"), errors="coerce").astype("float64")
    st = col(g, "status").astype("object").fillna("")
    keep = ~st.isin(["NEVERCON", "UNDERCON"])
    keep &= yb.isna() | (yb <= LIDAR_YEAR)
    gone = st.isin(["REMOVED", "INACTIVE"])
    keep &= ~gone | (ya > LIDAR_YEAR)
    return g[np.asarray(keep.fillna(False), dtype=bool)]


def pipe_ends(pipes):
    pts = []
    for geom in pipes.geometry:
        for line in getattr(geom, "geoms", [geom]):
            c = list(line.coords)
            pts += [Point(c[0]), Point(c[-1])]
    return gpd.GeoDataFrame(geometry=pts, crs=pipes.crs)


depth, transform, crs = depth_from_file(DEM)
labels, st = label_depressions(depth, 1.0, min_depth=0.15)
H, W = depth.shape

polys = [(int(v), shape(g)) for g, v in
         features.shapes(labels.astype("int32"), mask=labels > 0, transform=transform)]
dep = gpd.GeoDataFrame({"id": [p[0] for p in polys]},
                       geometry=[p[1] for p in polys], crs=crs).dissolve(by="id").reset_index()
dep = dep.merge(pd.DataFrame({k: st[k] for k in ("id", "max_depth_m", "area_m2", "volume_m3")}), on="id")

combo = load_clipped("combo_inlet", BBOX, crs)
bridge = load_clipped("bridge_inlet", BBOX, crs)
inlets = existed(gpd.GeoDataFrame(pd.concat([combo, bridge], ignore_index=True), crs=crs))
chans = existed(load_clipped("open_channel", BBOX, crs))
ends = pipe_ends(existed(load_clipped("drainage_pipe", BBOX, crs)))
print("assets kept (existed in", LIDAR_YEAR, "):", len(inlets), "inlets,", len(chans), "channels,",
      len(ends), "pipe end points")


def near(assets, snap, label):
    buf = dep[["id", "geometry"]].copy()
    buf["geometry"] = buf.geometry.buffer(snap)
    j = gpd.sjoin(buf, assets[["geometry"]], predicate="intersects", how="inner")
    return j.groupby("id").size().rename(label)


for s in (near(inlets, SNAP_INLET, "n_inlet"), near(chans, SNAP_CHANNEL, "n_channel"),
          near(ends, SNAP_PIPE_END, "n_pipe_end")):
    dep = dep.merge(s, on="id", how="left")
for c in ("n_inlet", "n_channel", "n_pipe_end"):
    dep[c] = dep[c].fillna(0).astype(int)


def classify(r):
    if r.n_inlet > 0:
        return "inlet"
    if r.n_channel > 0:
        return "channel"
    if r.n_pipe_end > 0:
        return "pipe_end_only"
    return "none"


dep["cls"] = dep.apply(classify, axis=1)
dep["pit_suspect"] = (dep.max_depth_m >= 1.5) & (dep.area_m2 < 150)

b = dep.geometry.bounds
left, top = transform.c, transform.f
right, bottom = left + W * transform.a, top + H * transform.e
dep["edge"] = (b.minx <= left + 2) | (b.maxx >= right - 2) | (b.miny <= bottom + 2) | (b.maxy >= top - 2)
ll = dep.geometry.centroid.to_crs(4326)
dep["lat"], dep["lon"] = ll.y, ll.x

print(f"\n{len(dep)} depressions; {int(dep.edge.sum())} on raster edge; "
      f"{int(dep.pit_suspect.sum())} suspect pits (>=1.5 m deep, <150 m2)\n")
print(dep.groupby("cls").agg(n=("id", "count"), volume_m3=("volume_m3", "sum"),
                             median_depth_m=("max_depth_m", "median")).round(1).to_string())

cand = dep[(dep.cls == "none") & ~dep.edge & ~dep.pit_suspect & (dep.area_m2 >= 150)]
cand = cand.sort_values("volume_m3", ascending=False).head(15)
print("\nLargest-volume depressions with no recorded inlet, channel or pipe end nearby:")
for _, r in cand.iterrows():
    print(f"  {r.max_depth_m:5.2f} m | {r.area_m2:7.0f} m2 | {r.volume_m3:8.0f} m3 | "
          f"https://www.google.com/maps?q={r.lat:.6f},{r.lon:.6f}")

dep.drop(columns="geometry").to_csv("data/pilot_depressions_classified.csv", index=False)
dep.to_file("data/pilot_depressions.gpkg", driver="GPKG", layer="depressions")
