from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from scipy import ndimage

from h3lowpoints.hydro.depressions import depth_from_array, label_depressions
from h3lowpoints.sources.assets import drain_points, load_clipped

DEM = "data/pilot_dem.tif"
BBOX = (-97.76, 30.29, -97.72, 30.32)
YEAR, FRAC, R, NEAR = 2017, 0.8, 5, 15
URL = "https://data.austintexas.gov/resource/xwdj-i9he.json"
TARGET = "upper(sr_type_desc) like '%FLOOD%' OR upper(sr_type_desc) like '%STANDING WATER%'"
DRAINISH = (TARGET + " OR upper(sr_type_desc) like '%DRAIN%' OR upper(sr_type_desc) like '%STORM%' "
            "OR upper(sr_type_desc) like '%CHANNEL%' OR upper(sr_type_desc) like '%CREEK%' "
            "OR upper(sr_type_desc) like '%WATER%'")


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
        where = f"({box}) AND {since} AND ({extra_where})"
        r = requests.get(URL, params={"$where": where, "$limit": 500000}, timeout=900)
        if r.status_code == 200:
            df = pd.DataFrame(r.json())
            df.to_csv(p, index=False)
            return df
        print("query failed:", r.status_code, r.text[:300])
    raise SystemExit("could not download 311 data; send me the messages above")


with rasterio.open(DEM) as s:
    arr, nodata, transform, crs = s.read(1), s.nodata, s.transform, s.crs
invalid = np.isnan(arr) | (arr == nodata)
H, W = arr.shape
inv = ~transform
to_xy_tf = Transformer.from_crs(4326, crs, always_xy=True)

depth = depth_from_array(arr, invalid)
labels, st = label_depressions(depth, 1.0, 0.15)
ids = np.asarray(st["id"]).astype(int)
mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))
maxlab = int(labels.max())
print(f"{len(ids)} depressions; the 10 largest hold {100 * np.sort(vol)[-10:].sum() / vol.sum():.0f}% of total volume")

# --- classify depressions: a drain within R m of the deep core means 'drained' ---
lut = np.zeros(maxlab + 1)
lut[ids] = mx
core = np.where((labels > 0) & (depth >= FRAC * lut[labels]), labels, 0)
pts = drain_points(load_clipped("combo_inlet", BBOX, crs), load_clipped("bridge_inlet", BBOX, crs),
                   load_clipped("drainage_pipe", BBOX, crs), YEAR)
xy = np.array([(g.x, g.y) for g in pts.geometry])
_, keep = np.unique(np.round(xy * 2) / 2, axis=0, return_index=True)
xy = xy[np.sort(keep)]
found = set()
for x, y in xy:
    c, r = inv * (x, y)
    r, c = int(r), int(c)
    if 0 <= r < H and 0 <= c < W:
        win = core[max(0, r - R):r + R + 1, max(0, c - R):c + R + 1]
        found.update(np.unique(win[win > 0]).tolist())
served = np.isin(ids, list(found))

objs = ndimage.find_objects(labels)
edge = np.array([objs[i - 1][0].start == 0 or objs[i - 1][1].start == 0 or
                 objs[i - 1][0].stop == H or objs[i - 1][1].stop == W for i in ids])
pit = (mx >= 1.5) & (area < 150)
signif = (mx >= 0.3) & (area >= 150) & ~edge
cls = np.where(served, "drained",
      np.where(pit & ~edge, "suspect_pit",
      np.where(signif, "unexplained", "minor")))
cls_by_label = np.full(maxlab + 1, "", dtype=object)
cls_by_label[ids] = cls
print("depression classes:", pd.Series(cls).value_counts().to_dict())

# --- 311 tickets: flooding / standing water vs other (control) tickets ---
def load(extra, cache):
    df = fetch(extra, cache)
    lat = pd.to_numeric(df["sr_location_lat"], errors="coerce")
    lon = pd.to_numeric(df["sr_location_long"], errors="coerce")
    ok = lat.notna() & lon.notna()
    df = df[ok].copy()
    df["x"], df["y"] = to_xy_tf.transform(lon[ok].to_numpy(), lat[ok].to_numpy())
    return df


flood = load(TARGET, "data/311_flood.csv")
ctrl = load(f"NOT ({DRAINISH})", "data/311_control.csv")
print(f"\ntickets since 2017 in box: {len(flood)} flooding/standing-water, {len(ctrl)} control")

dist_all, (ir, ic) = ndimage.distance_transform_edt(~(labels > 0), return_indices=True)


def assign(df):
    c, r = inv * (df["x"].to_numpy(), df["y"].to_numpy())
    r, c = r.astype(int), c.astype(int)
    ok = (r >= 0) & (c >= 0) & (r < H) & (c < W)
    df, r, c = df[ok].copy(), r[ok], c[ok]
    df["dist"] = dist_all[r, c]
    df["cls"] = np.where(df["dist"] <= NEAR, cls_by_label[labels[ir[r, c], ic[r, c]]], "none within 15 m")
    return df


flood, ctrl = assign(flood), assign(ctrl)

# Test 1: are flood tickets closer to depressions than control tickets?
print("\nTEST 1: share of tickets within d metres of a depression (control = random draws of other 311 tickets)")
rng = np.random.default_rng(0)
dc = ctrl["dist"].to_numpy()
for name, mask in (("all depressions", labels > 0),
                   ("significant depressions", np.zeros(maxlab + 1, bool))):
    if name.startswith("significant"):
        flag = np.zeros(maxlab + 1, bool)
        flag[ids[signif]] = True
        mask = flag[labels]
        d_f = ndimage.distance_transform_edt(~mask)
        cf = inv * (flood["x"].to_numpy(), flood["y"].to_numpy())
        cc = inv * (ctrl["x"].to_numpy(), ctrl["y"].to_numpy())
        df_dist = d_f[cf[1].astype(int), cf[0].astype(int)]
        dc_dist = d_f[cc[1].astype(int), cc[0].astype(int)]
    else:
        df_dist, dc_dist = flood["dist"].to_numpy(), dc
    for d in (5, 10, 20):
        real = (df_dist <= d).mean()
        boot = [(rng.choice(dc_dist, size=len(df_dist), replace=len(dc_dist) < len(df_dist)) <= d).mean()
                for _ in range(1000)]
        lo, hi = np.percentile(boot, [2.5, 97.5])
        print(f"  {name:24s} within {d:2d} m: flood {100 * real:5.1f}% | control {100 * np.mean(boot):5.1f}% "
              f"(95%: {100 * lo:.1f}-{100 * hi:.1f}%) | {real / max(np.mean(boot), 1e-6):.2f}x")

# Test 2: flood share of nearby tickets, by depression class
print(f"\nTEST 2: flood/standing-water share of all tickets within {NEAR} m of each depression class")
rows = []
for k in ("drained", "unexplained", "minor", "suspect_pit", "none within 15 m"):
    f, c = int((flood["cls"] == k).sum()), int((ctrl["cls"] == k).sum())
    rows.append((k, int((cls == k).sum()), f, c, 100 * f / max(f + c, 1)))
rows.append(("ALL TICKETS", len(ids), len(flood), len(ctrl), 100 * len(flood) / (len(flood) + len(ctrl))))
print(pd.DataFrame(rows, columns=["class", "n_depressions", "flood_tickets", "control_tickets",
                                  "flood_share_%"]).round(1).to_string(index=False))
