import numpy as np
import rasterio
from scipy import ndimage

from h3lowpoints.hydro.depressions import depth_from_array, label_depressions
from h3lowpoints.sources.assets import drain_points, load_clipped

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
YEAR = 2017

with rasterio.open(DEM) as s:
    arr, nodata, transform, crs = s.read(1), s.nodata, s.transform, s.crs
invalid = np.isnan(arr) | (arr == nodata)
H, W = arr.shape
inv = ~transform

depth = depth_from_array(arr, invalid)
labels, st = label_depressions(depth, 1.0, 0.15)
ids = np.asarray(st["id"]).astype(int)
mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))
lut = np.zeros(int(labels.max()) + 1)
lut[ids] = mx

objs = ndimage.find_objects(labels)
edge = np.array([objs[i - 1][0].start == 0 or objs[i - 1][1].start == 0 or
                 objs[i - 1][0].stop == H or objs[i - 1][1].stop == W for i in ids])
subsets = {"all": np.ones(len(ids), bool), "significant": (mx >= 0.3) & (area >= 150) & ~edge}

pts = drain_points(load_clipped("combo_inlet", BBOX, crs), load_clipped("bridge_inlet", BBOX, crs),
                   load_clipped("drainage_pipe", BBOX, crs), YEAR)
xy = np.array([(g.x, g.y) for g in pts.geometry])
_, keep = np.unique(np.round(xy * 2) / 2, axis=0, return_index=True)
xy = xy[np.sort(keep)]
shifts = [(round(d * np.cos(a)), round(d * np.sin(a))) for d in (40, 60, 90)
          for a in np.arange(8) * np.pi / 4]


def explained(points, core, R):
    found = set()
    for x, y in points:
        c, r = inv * (x, y)
        r, c = int(r), int(c)
        if r < 0 or c < 0 or r >= H or c >= W:
            continue
        win = core[max(0, r - R):r + R + 1, max(0, c - R):c + R + 1]
        found.update(np.unique(win[win > 0]).tolist())
    return np.isin(ids, list(found))


def shares(m, sub):
    a, v = area[sub], vol[sub]
    return 100 * m[sub].mean(), 100 * a[m[sub]].sum() / a.sum(), 100 * v[m[sub]].sum() / v.sum()


print(f"{len(ids)} depressions, {len(xy)} drains, {len(shifts)} shifted copies as null\n")
print("core | radius | subset      | metric |  real | null mean | null range      | real/null")
for frac in (0.5, 0.8):
    core = np.where((labels > 0) & (depth >= frac * lut[labels]), labels, 0)
    for R in (5, 8):
        real = explained(xy, core, R)
        null = [explained(xy + np.array(sh), core, R) for sh in shifts]
        for name, sub in subsets.items():
            r = shares(real, sub)
            n = np.array([shares(m, sub) for m in null])
            for i, label in enumerate(("count", "area", "volume")):
                print(f"{int(frac * 100):3d}% | {R:4d} m | {name:11s} | {label:6s} | {r[i]:4.1f}% | "
                      f"{n[:, i].mean():8.1f}% | {n[:, i].min():4.1f}% - {n[:, i].max():4.1f}% | "
                      f"{r[i] / max(n[:, i].mean(), 0.1):6.1f}x")
