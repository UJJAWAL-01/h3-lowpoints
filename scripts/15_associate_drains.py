import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import cKDTree

from h3lowpoints.h3agg.aggregate import pixels_to_h3
from h3lowpoints.hydro.depressions import depth_from_array, label_depressions
from h3lowpoints.sources.assets import drain_points, load_clipped

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
YEAR, R = 2017, 5  # lidar year; association radius in cells (1 m each)

with rasterio.open(DEM) as s:
    arr, nodata, transform, crs = s.read(1), s.nodata, s.transform, s.crs
invalid = np.isnan(arr) | (arr == nodata)
H, W = arr.shape
inv = ~transform

depth = depth_from_array(arr, invalid)
labels, st = label_depressions(depth, 1.0, 0.15)
ids = np.asarray(st["id"]).astype(int)
mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))

pts = drain_points(load_clipped("combo_inlet", BBOX, crs), load_clipped("bridge_inlet", BBOX, crs),
                   load_clipped("drainage_pipe", BBOX, crs), YEAR)
xy = np.array([(g.x, g.y) for g in pts.geometry])
_, keep = np.unique(np.round(xy * 2) / 2, axis=0, return_index=True)
xy = xy[np.sort(keep)]
print(f"{len(ids)} depressions (>= 0.15 m); {len(xy)} unique drain points")


def explained(points):
    found = set()
    for x, y in points:
        c, r = inv * (x, y)
        r, c = int(r), int(c)
        if r < 0 or c < 0 or r >= H or c >= W:
            continue
        win = labels[max(0, r - R):r + R + 1, max(0, c - R):c + R + 1]
        found.update(np.unique(win[win > 0]).tolist())
    return np.isin(ids, list(found))


def shares(m, subset):
    a, v = area[subset], vol[subset]
    return (100 * m[subset].mean(), 100 * a[m[subset]].sum() / a.sum(), 100 * v[m[subset]].sum() / v.sum())


objs = ndimage.find_objects(labels)
edge = np.array([objs[i - 1][0].start == 0 or objs[i - 1][1].start == 0 or
                 objs[i - 1][0].stop == H or objs[i - 1][1].stop == W for i in ids])
subsets = {
    "all depressions": np.ones(len(ids), bool),
    "depth >= 0.3 m, area >= 150 m2": (mx >= 0.3) & (area >= 150) & ~edge,
}

real = explained(xy)
shifts = [(round(d * np.cos(a)), round(d * np.sin(a))) for d in (40, 60, 90)
          for a in np.arange(8) * np.pi / 4]
null = [explained(xy + np.array(sh)) for sh in shifts]

print(f"\nshare explained by a drain within {R} m (real drains vs {len(shifts)} shifted copies)")
print("subset                          | metric |  real | null mean | null range      | real/null")
for name, sub in subsets.items():
    r = shares(real, sub)
    n = np.array([shares(m, sub) for m in null])
    for i, label in enumerate(("count", "area", "volume")):
        print(f"{name:31s} | {label:6s} | {r[i]:4.1f}% | {n[:, i].mean():8.1f}% | "
              f"{n[:, i].min():4.1f}% - {n[:, i].max():4.1f}% | {r[i] / max(n[:, i].mean(), 0.1):6.1f}x")

# ---------- classify every depression and export ----------
pos = ndimage.maximum_position(depth, labels, [int(i) for i in ids])
low_xy = np.array([transform * (c + 0.5, r + 0.5) for r, c in pos])
dist = cKDTree(xy).query(low_xy)[0]
pit = (mx >= 1.5) & (area < 150)
major = (mx >= 0.3) & (area >= 150)

code = np.ones(len(ids), int)                      # 1 = minor
code[real] = 2                                     # 2 = explained by a drain
code[~real & major & ~pit & ~edge] = 4             # 4 = candidate: no drain, significant
code[~real & pit & ~edge] = 3                      # 3 = suspect pit
names = {1: "minor", 2: "drain_nearby", 3: "suspect_pit", 4: "unexplained_candidate"}

to_ll = Transformer.from_crs(crs, 4326, always_xy=True)
lon, lat = to_ll.transform(low_xy[:, 0], low_xy[:, 1])
tab = pd.DataFrame({"id": ids, "max_depth_m": mx, "area_m2": area, "volume_m3": vol,
                    "dist_to_drain_m": dist, "edge": edge, "code": code,
                    "cls": [names[c] for c in code], "lat": lat, "lon": lon})
tab.to_csv("data/pilot_depression_table.csv", index=False)

print("\nclass summary")
print(tab.groupby("cls").agg(n=("id", "count"), volume_m3=("volume_m3", "sum"),
                             median_depth_m=("max_depth_m", "median")).round(1).to_string())

top = tab[tab.code == 4].sort_values("volume_m3", ascending=False).head(15)
print("\nlargest unexplained candidates (no recorded 2017 drain within", R, "m):")
for _, r in top.iterrows():
    print(f"  {r.max_depth_m:5.2f} m | {r.area_m2:7.0f} m2 | {r.volume_m3:8.0f} m3 | "
          f"{r.dist_to_drain_m:5.0f} m to nearest drain | https://www.google.com/maps?q={r.lat:.6f},{r.lon:.6f}")

# per-pixel class raster -> H3 cells (highest-priority class wins inside a cell)
lut = np.zeros(int(labels.max()) + 1)
lut[ids] = code
img = lut[labels]
img[labels == 0] = np.nan
cells = pixels_to_h3(img, transform, crs, res=11, step=1).groupby("h3")["v"].max().reset_index()
cells.columns = ["h3", "code"]
cells.to_csv("data/pilot_cells_classes.csv", index=False)
print("\nH3 cells with a depression:", len(cells))
