import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import Point

from h3lowpoints.hydro.breach import drains_to_raster
from h3lowpoints.hydro.depressions import depth_from_array, label_depressions
from h3lowpoints.sources.assets import drain_points, existed_at, load_clipped

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
YEAR = 2017

with rasterio.open(DEM) as s:
    arr, nodata, transform, crs = s.read(1), s.nodata, s.transform, s.crs
invalid = np.isnan(arr) | (arr == nodata)
H, W = arr.shape
inv = ~transform

combo = load_clipped("combo_inlet", BBOX, crs)
bridge = load_clipped("bridge_inlet", BBOX, crs)
pipes = load_clipped("drainage_pipe", BBOX, crs)

# ---------- A. Is pipe geometry digitised in flow direction? ----------
p = existed_at(pipes, YEAR)
ui = pd.to_numeric(p["up_invert"], errors="coerce").astype("float64").to_numpy()
di = pd.to_numeric(p["dn_invert"], errors="coerce").astype("float64").to_numpy()
rows = []
for geom, a, b in zip(p.geometry, ui, di):
    lines = list(getattr(geom, "geoms", [geom]))
    if not lines or not (300 < a < 1200 and 300 < b < 1200):  # plausible elevations in feet
        continue
    (x0, y0), (x1, y1) = lines[0].coords[0][:2], lines[-1].coords[-1][:2]
    c0, r0 = inv * (x0, y0)
    c1, r1 = inv * (x1, y1)
    r0, c0, r1, c1 = int(r0), int(c0), int(r1), int(c1)
    if min(r0, c0, r1, c1) < 0 or max(r0, r1) >= H or max(c0, c1) >= W:
        continue
    if invalid[r0, c0] or invalid[r1, c1]:
        continue
    rows.append((arr[r0, c0] - arr[r1, c1], (a - b) * 0.3048))
t = np.array(rows)
big = (np.abs(t[:, 0]) > 0.5) & (np.abs(t[:, 1]) > 0.15)
agree = np.sign(t[big, 0]) == np.sign(t[big, 1])
print(f"A. pipes with usable inverts: {len(t)}; with a clear slope: {int(big.sum())}")
print(f"   share with up_invert > dn_invert: {(t[:, 1] > 0).mean():.1%}")
print(f"   DEM drop start->end agrees with invert drop up->dn: {agree.mean():.1%}")
print("   (near 100% = geometry starts at the upstream node; near 0% = reversed; ~50% = no signal)")

# ---------- B. Drain points, de-duplicated ----------
pts = drain_points(combo, bridge, pipes, YEAR)
xy = np.array([(g.x, g.y) for g in pts.geometry])
_, keep = np.unique(np.round(xy * 2) / 2, axis=0, return_index=True)
xy = xy[np.sort(keep)]
print(f"\nB. unique drain points: {len(xy)} (from {len(pts)} before de-duplication)")

# ---------- C. Baseline depressions and their low points ----------
base = depth_from_array(arr, invalid)
labels, st = label_depressions(base, 1.0, 0.15)
ids = [int(i) for i in st["id"]]
v0 = float(np.sum(st["volume_m3"]))
pos = ndimage.maximum_position(base, labels, ids)
low_xy = np.array([transform * (c + 0.5, r + 0.5) for r, c in pos])

SHIFTS = {"real": (0, 0), "shift N": (0, 60), "shift S": (0, -60), "shift E": (60, 0), "shift W": (-60, 0)}

print("\nC. distance from each depression's lowest point to the nearest drain:")
for name, (dx, dy) in SHIFTS.items():
    d = cKDTree(xy + np.array([dx, dy])).query(low_xy)[0]
    print(f"   {name:8s} median {np.median(d):6.1f} m | within 5 m {np.mean(d <= 5):5.1%} "
          f"| within 10 m {np.mean(d <= 10):5.1%} | within 20 m {np.mean(d <= 20):5.1%}")


# ---------- D. Volume removed: real vs shifted drains ----------
def removed(points_xy, snap):
    g = gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in points_xy], crs=crs)
    dr = drains_to_raster(g, arr, transform, snap_cells=snap, invalid=invalid)
    d = depth_from_array(arr, invalid, drains=dr)
    _, s = label_depressions(d, 1.0, 0.15)
    return 100 * (1 - float(np.sum(s["volume_m3"])) / v0)


print("\nD. % of depression volume removed (same snapping for real and shifted drains):")
print("   snap |   real | shifted N/S/E/W                    | shifted mean | excess (real - mean)")
for snap in (3, 6, 10):
    vals = {n: removed(xy + np.array(sh), snap) for n, sh in SHIFTS.items()}
    sh = [vals[n] for n in SHIFTS if n != "real"]
    print(f"   {snap:3d} m | {vals['real']:5.1f}% | "
          f"{' / '.join(f'{v:4.1f}' for v in sh):32s} | {np.mean(sh):9.1f}%  | {vals['real'] - np.mean(sh):+6.1f} pts")
