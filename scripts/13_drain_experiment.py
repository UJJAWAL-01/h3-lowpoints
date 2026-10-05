import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import cKDTree

from h3lowpoints.hydro.breach import drains_to_raster
from h3lowpoints.hydro.depressions import depth_from_array, label_depressions
from h3lowpoints.sources.assets import drain_points, existed_at, load_clipped

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
YEAR = 2017

with rasterio.open(DEM) as s:
    arr, nodata, transform, crs, prof = s.read(1), s.nodata, s.transform, s.crs, s.profile
invalid = np.isnan(arr) | (arr == nodata)

combo = load_clipped("combo_inlet", BBOX, crs)
bridge = load_clipped("bridge_inlet", BBOX, crs)
pipes = load_clipped("drainage_pipe", BBOX, crs)

# --- Check 1: does PR line up with ownership? ---
if "ownership" in pipes.columns:
    pr = pipes["up_node_type"].astype("object").fillna("").str.startswith("PR")
    print("ownership vs PR node type (pipes):")
    print(pd.crosstab(pipes["ownership"].astype("object").fillna("<null>"), pr).to_string())

# --- Check 2: is pipe geometry digitised in flow direction (start = upstream node)? ---
inlets = pd.concat([existed_at(combo, YEAR), existed_at(bridge, YEAR)])
tree = cKDTree(np.array([(g.x, g.y) for g in inlets.geometry]))
p = existed_at(pipes, YEAR)
m = p["up_node_type"].astype("object").fillna("").isin(["INLET", "GRATE"]).to_numpy()
starts, ends = [], []
for geom in p[m].geometry:
    lines = list(getattr(geom, "geoms", [geom]))
    starts.append(lines[0].coords[0][:2])
    ends.append(lines[-1].coords[-1][:2])
d_s, d_e = tree.query(np.array(starts))[0], tree.query(np.array(ends))[0]
print(f"\npipes whose UPSTREAM node is an inlet/grate: {len(d_s)}")
print("  median distance, pipe START to nearest inlet point (m):", round(float(np.median(d_s)), 1))
print("  median distance, pipe END   to nearest inlet point (m):", round(float(np.median(d_e)), 1))


def summarize(depth):
    _, st = label_depressions(depth, 1.0, 0.15)
    return len(st["id"]), float(np.sum(st["volume_m3"])), float(np.sum(st["area_m2"]))


# --- Experiment: how much do recorded inlets explain? ---
base = depth_from_array(arr, invalid)
n0, v0, a0 = summarize(base)
print(f"\nno drains      : {n0:5d} depressions | {v0:9.0f} m3 | {a0:9.0f} m2")

pts = drain_points(combo, bridge, pipes, YEAR)
print(f"drain points (inlets + grates existing in {YEAR}): {len(pts)}")
chosen = None
for snap in (3, 6, 10):
    dr = drains_to_raster(pts, arr, transform, snap_cells=snap, invalid=invalid)
    d = depth_from_array(arr, invalid, drains=dr)
    n, v, a = summarize(d)
    print(f"snap {snap:2d} m      : {n:5d} depressions | {v:9.0f} m3 | {a:9.0f} m2 "
          f"| volume removed {100 * (1 - v / v0):4.1f}%")
    if snap == 6:
        chosen = d

# --- Save the drained depth raster and list the largest remaining depressions ---
prof.update(dtype="float32", nodata=None, compress="deflate")
with rasterio.open("data/pilot_depth_drained.tif", "w", **prof) as dst:
    dst.write(chosen.astype("float32"), 1)

labels, st = label_depressions(chosen, 1.0, 0.15)
ids, vol = np.asarray(st["id"]), np.asarray(st["volume_m3"])
area, mx = np.asarray(st["area_m2"]), np.asarray(st["max_depth_m"])
objs = ndimage.find_objects(labels)
H, W = chosen.shape
to_ll = Transformer.from_crs(crs, 4326, always_xy=True)
print("\nLargest remaining depressions after draining (not on edge, area >= 150 m2):")
shown = 0
for k in np.argsort(-vol):
    sl = objs[int(ids[k]) - 1]
    if area[k] < 150 or sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == H or sl[1].stop == W:
        continue
    r, c = ndimage.maximum_position(chosen, labels, int(ids[k]))
    x, y = transform * (c + 0.5, r + 0.5)
    lon, lat = to_ll.transform(x, y)
    print(f"  {mx[k]:5.2f} m | {area[k]:7.0f} m2 | {vol[k]:8.0f} m3 | "
          f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}")
    shown += 1
    if shown == 15:
        break
