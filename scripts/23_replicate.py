import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from scipy import ndimage

from h3lowpoints import analyze
from h3lowpoints.sources.assets import drain_points, load_clipped, points_to_xy
from h3lowpoints.sources.dem import crop_mosaic

AREAS = {
    "pilot": (-97.76, 30.29, -97.72, 30.32),
    "east": (-97.72, 30.29, -97.68, 30.32),
    "north": (-97.76, 30.32, -97.72, 30.35),
}
name = sys.argv[1] if len(sys.argv) > 1 else "pilot"
BBOX = AREAS[name]
YEAR, NEAR = 2017, 15
URL = "https://data.austintexas.gov/resource/xwdj-i9he.json"
TARGET = "upper(sr_type_desc) like '%FLOOD%' OR upper(sr_type_desc) like '%STANDING WATER%'"
DRAINISH = (TARGET + " OR upper(sr_type_desc) like '%DRAIN%' OR upper(sr_type_desc) like '%STORM%' "
            "OR upper(sr_type_desc) like '%CHANNEL%' OR upper(sr_type_desc) like '%CREEK%' "
            "OR upper(sr_type_desc) like '%WATER%'")

dem = "data/pilot_dem.tif" if name == "pilot" else f"data/{name}_dem.tif"
if not Path(dem).exists():
    crop_mosaic(sorted(glob.glob("data/raw/*.tif")), dem, BBOX)
with rasterio.open(dem) as s:
    crs, inv = s.crs, ~s.transform
    H, W = s.shape


def fetch(extra_where, cache):
    p = Path(cache)
    if p.exists():
        return pd.read_csv(p, low_memory=False)
    since = "sr_created_date >= '2017-01-01T00:00:00'"
    boxes = [
        f"within_box(sr_location_lat_long, {BBOX[3]}, {BBOX[0]}, {BBOX[1]}, {BBOX[2]})",
        f"sr_location_lat between {BBOX[1]} and {BBOX[3]} AND sr_location_long between {BBOX[0]} and {BBOX[2]}",
    ]
    for box in boxes:
        r = requests.get(URL, params={"$where": f"({box}) AND {since} AND ({extra_where})",
                                      "$limit": 500000}, timeout=900)
        if r.status_code == 200:
            df = pd.DataFrame(r.json())
            df.to_csv(p, index=False)
            return df
        print("query failed:", r.status_code, r.text[:300])
    raise SystemExit("could not download 311 data")


to_xy = Transformer.from_crs(4326, crs, always_xy=True)


def tickets(extra, cache):
    df = fetch(extra, cache)
    lat = pd.to_numeric(df["sr_location_lat"], errors="coerce")
    lon = pd.to_numeric(df["sr_location_long"], errors="coerce")
    ok = (lat.notna() & lon.notna()).to_numpy()
    x, y = to_xy.transform(lon.to_numpy()[ok], lat.to_numpy()[ok])
    c, r = inv @ (np.asarray(x), np.asarray(y))
    r, c = np.floor(r).astype(int), np.floor(c).astype(int)
    inside = (r >= 0) & (c >= 0) & (r < H) & (c < W)
    return r[inside], c[inside]


combo = load_clipped("combo_inlet", BBOX, crs, tag=name)
bridge = load_clipped("bridge_inlet", BBOX, crs, tag=name)
pipes = load_clipped("drainage_pipe", BBOX, crs, tag=name)
xy = points_to_xy(drain_points(combo, bridge, pipes, YEAR))

res = analyze(dem, drains_xy=xy)  # frozen default thresholds
dep, labels = res.depressions, res.labels
print(f"\n=== {name}: {len(dep)} depressions, {int(dep.significant.sum())} significant, {len(xy)} drains")
print("classes:", res.meta["class_counts"])

fr, fc = tickets(TARGET, f"data/311_flood_{name}.csv")
cr, cc = tickets(f"NOT ({DRAINISH})", f"data/311_control_{name}.csv")
print(f"tickets since 2017: {len(fr)} flooding/standing-water, {len(cr)} control")

maxlab = int(labels.max())
sig_mask = np.isin(labels, dep.loc[dep.significant, "id"].to_numpy())
d_sig = ndimage.distance_transform_edt(~sig_mask)
d_any, (ir, ic) = ndimage.distance_transform_edt(labels == 0, return_indices=True)

rng = np.random.default_rng(0)
f, c = d_sig[fr, fc], d_sig[cr, cc]
crit_a = False
print("\nTEST 1: tickets within d m of a significant depression")
for d in (5, 10, 20):
    real, base = (f <= d).mean(), (c <= d).mean()
    boot = [(rng.choice(f, size=len(f)) <= d).mean() for _ in range(2000)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"  within {d:2d} m: flood {100 * real:4.1f}% (95% {100 * lo:.1f}-{100 * hi:.1f}) | "
          f"control {100 * base:4.1f}% | {real / max(base, 1e-6):.2f}x")
    if d == 10:
        crit_a = lo > base

area_l = np.zeros(maxlab + 1)
area_l[dep["id"].to_numpy()] = dep["area_m2"].to_numpy()
cls_l = np.full(maxlab + 1, "", dtype=object)
cls_l[dep["id"].to_numpy()] = dep["cls"].to_numpy()


def near_attr(r, c):
    ok = d_any[r, c] <= NEAR
    lab = labels[ir[r, c], ic[r, c]]
    return ok, area_l[lab], cls_l[lab]


okf, af, kf = near_attr(fr, fc)
okc, ac, kc = near_attr(cr, cc)
base_share = 100 * len(fr) / (len(fr) + len(cr))
print(f"\nDOSE-RESPONSE by area (tickets within {NEAR} m of a depression); baseline {base_share:.2f}%")
shares = []
edges = [0, 50, 150, 500, 1e9]
for lo, hi in zip(edges[:-1], edges[1:]):
    nf = int((okf & (af >= lo) & (af < hi)).sum())
    nc = int((okc & (ac >= lo) & (ac < hi)).sum())
    shares.append(100 * nf / max(nf + nc, 1))
    print(f"  {lo:g}-{hi:g} m2: flood {nf:4d} control {nc:6d} share {shares[-1]:.2f}%")
crit_b = shares[3] > max(shares[0], shares[1])

print(f"\nTEST 2: flood share of tickets within {NEAR} m, by depression class")
for k in ("drained", "unexplained", "minor", "suspect_pit"):
    nf, nc = int((okf & (kf == k)).sum()), int((okc & (kc == k)).sum())
    print(f"  {k:12s} flood {nf:4d} control {nc:6d} share {100 * nf / max(nf + nc, 1):.2f}%")

print(f"\nCRITERION A (10 m lower bound above control rate): {'PASS' if crit_a else 'FAIL'}")
print(f"CRITERION B (top area bin above the two smallest):  {'PASS' if crit_b else 'FAIL'}")
