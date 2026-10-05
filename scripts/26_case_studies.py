import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy import ndimage

from h3lowpoints import analyze
from h3lowpoints.sources.assets import drain_points, load_clipped, points_to_xy

NAME, BBOX, DEM = "pilot", (-97.76, 30.29, -97.72, 30.32), "data/pilot_dem.tif"
with rasterio.open(DEM) as s:
    crs, inv = s.crs, ~s.transform
    H, W = s.shape
xy = points_to_xy(drain_points(load_clipped("combo_inlet", BBOX, crs, tag=NAME),
                               load_clipped("bridge_inlet", BBOX, crs, tag=NAME),
                               load_clipped("drainage_pipe", BBOX, crs, tag=NAME), 2017))
res = analyze(DEM, drains_xy=xy)
dep, labels = res.depressions, res.labels

df = pd.read_csv("data/311_flood_pilot.csv", low_memory=False)
lat = pd.to_numeric(df["sr_location_lat"], errors="coerce")
lon = pd.to_numeric(df["sr_location_long"], errors="coerce")
ok = (lat.notna() & lon.notna()).to_numpy()
df = df[ok].copy()
x, y = Transformer.from_crs(4326, crs, always_xy=True).transform(lon.to_numpy()[ok], lat.to_numpy()[ok])
c, r = inv @ (np.asarray(x), np.asarray(y))
r, c = np.floor(r).astype(int), np.floor(c).astype(int)
inside = (r >= 0) & (c >= 0) & (r < H) & (c < W)
df, r, c = df[inside].copy(), r[inside], c[inside]
df["spot"] = (np.round(np.asarray(x)[inside] / 10).astype(int).astype(str) + "_"
              + np.round(np.asarray(y)[inside] / 10).astype(int).astype(str))

dist, (ir, ic) = ndimage.distance_transform_edt(labels == 0, return_indices=True)
df["dep_id"] = np.where(dist[r, c] <= 15, labels[ir[r, c], ic[r, c]], 0)

cnt = (df[df.dep_id > 0].groupby("dep_id")
       .agg(tickets=("sr_number", "size"), spots=("spot", "nunique"),
            first=("sr_created_date", "min"), last=("sr_created_date", "max"),
            types=("sr_type_desc", lambda s: "; ".join(s.value_counts().index[:2])))
       .reset_index().rename(columns={"dep_id": "id"}))
top = dep[dep.significant].merge(cnt, on="id").sort_values(["spots", "tickets"], ascending=False).head(10)
top["map"] = ["https://www.google.com/maps?q=%.6f,%.6f" % (a, b) for a, b in zip(top.lat, top.lon)]
for _, t in top.iterrows():
    print(f"{t.max_depth_m:4.2f} m | {t.area_m2:6.0f} m2 | drain at core: {str(t.drain_at_core):5s} | "
          f"{int(t.tickets)} tickets at {int(t.spots)} spots ({str(t['first'])[:4]}-{str(t['last'])[:4]}) | "
          f"{t.types}\n    {t.map}")
top.drop(columns=["first", "last"]).to_csv("outputs/case_study_candidates.csv", index=False)
