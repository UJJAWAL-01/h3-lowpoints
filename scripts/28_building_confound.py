import time

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio import features
from scipy import ndimage

from h3lowpoints import analyze
from h3lowpoints.sources.assets import drain_points, load_clipped, points_to_xy

BBOX = {"pilot": (-97.76, 30.29, -97.72, 30.32),
        "east": (-97.72, 30.29, -97.68, 30.32),
        "north": (-97.76, 30.32, -97.72, 30.35)}
CASES = {185940: "#1", 71843: "#2", 138932: "#3", 153248: "#10"}  # pilot depressions you inspected
YEAR, NEAR_B, MIN_BLD, MAX_OVERLAP = 2017, 10, 20.0, 0.10

bld = gpd.read_file("data/assets/buildings_2017.geojson")
print(f"{len(bld)} footprints")
for col in ("feature", "origin_feature_class", "modify_type", "source"):
    if col in bld.columns:
        print(f"{col}:", bld[col].astype("string").value_counts(dropna=False).head(8).to_dict())
yrs = pd.to_datetime(bld["created_date"], utc=True, errors="coerce").dt.year
print("created year:", yrs.value_counts().sort_index().to_dict())

rng = np.random.default_rng(0)


def tickets(path, crs, inv, H, W):
    df = pd.read_csv(path, low_memory=False)
    lat = pd.to_numeric(df["sr_location_lat"], errors="coerce")
    lon = pd.to_numeric(df["sr_location_long"], errors="coerce")
    ok = (lat.notna() & lon.notna()).to_numpy()
    x, y = Transformer.from_crs(4326, crs, always_xy=True).transform(lon.to_numpy()[ok], lat.to_numpy()[ok])
    c, r = inv @ (np.asarray(x), np.asarray(y))
    r, c = np.floor(r).astype(int), np.floor(c).astype(int)
    inside = (r >= 0) & (c >= 0) & (r < H) & (c < W)
    return r[inside], c[inside]


def summarize(f, c, d=10):
    if len(f) == 0 or len(c) == 0:
        return None
    real, base = (f <= d).mean(), (c <= d).mean()
    boot = [(rng.choice(f, size=len(f)) <= d).mean() for _ in range(2000)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return real, lo, hi, base, real / max(base, 1e-9), len(f)


def line(tag, s):
    if s is None:
        return f"  {tag:58s} no tickets"
    real, lo, hi, base, ratio, nf = s
    return (f"  {tag:58s} flood {100 * real:5.1f}% ({100 * lo:.1f}-{100 * hi:.1f}) | "
            f"control {100 * base:5.1f}% | {ratio:4.2f}x | n_flood={nf}")


SCEN = {"A significant depressions, all tickets": ("sig", False),
        "B significant depressions, near-building tickets": ("sig", True),
        "C depressions under <10% building, all tickets": ("s0", False),
        "D depressions under <10% building, near-building (PRIMARY)": ("s0", True)}
pool = {k: ([], []) for k in SCEN}
passes = {}

for name, bbox in BBOX.items():
    dem = f"data/{name}_dem.tif"
    with rasterio.open(dem) as s:
        crs, transform, (H, W) = s.crs, s.transform, s.shape
    inv = ~transform
    xy = points_to_xy(drain_points(load_clipped("combo_inlet", bbox, crs, tag=name),
                                   load_clipped("bridge_inlet", bbox, crs, tag=name),
                                   load_clipped("drainage_pipe", bbox, crs, tag=name), YEAR))
    t0 = time.time()
    res = analyze(dem, drains_xy=xy)
    print(f"\n=== {name}: analyze() took {time.time() - t0:.0f} s for {H * W / 1e6:.1f} M cells")

    b = bld.to_crs(crs)
    b = b[b.area >= MIN_BLD]
    left, top = transform.c, transform.f
    right, bottom = left + W * transform.a, top + H * transform.e
    b = b.cx[left:right, bottom:top]
    bmask = features.rasterize(((g, 1) for g in b.geometry), out_shape=(H, W),
                               transform=transform, fill=0, dtype="uint8").astype(bool)

    dep, labels = res.depressions, res.labels
    ids = dep["id"].to_numpy()
    frac = np.asarray(ndimage.mean(bmask.astype("float32"), labels, ids))
    sig = dep["significant"].to_numpy()
    fs = frac[sig]
    print(f"significant depressions: {int(sig.sum())} | building overlap >=50%: {int((fs >= 0.5).sum())}, "
          f"10-50%: {int(((fs >= 0.1) & (fs < 0.5)).sum())}, <10%: {int((fs < 0.1).sum())}")
    if name == "pilot":
        lookup = dict(zip(ids, zip(frac, dep["area_m2"])))
        for i, tag in CASES.items():
            if i in lookup:
                print(f"  case {tag}: {100 * lookup[i][0]:.0f}% of the depression is under a building footprint "
                      f"({lookup[i][1]:.0f} m2)")

    m_sig = np.isin(labels, ids[sig])
    m_s0 = np.isin(labels, ids[sig & (frac < MAX_OVERLAP)])
    d_by = {"sig": ndimage.distance_transform_edt(~m_sig), "s0": ndimage.distance_transform_edt(~m_s0)}
    d_b = ndimage.distance_transform_edt(~bmask)

    fr, fc = tickets(f"data/311_flood_{name}.csv", crs, inv, H, W)
    cr, cc = tickets(f"data/311_control_{name}.csv", crs, inv, H, W)
    nb_f, nb_c = d_b[fr, fc] <= NEAR_B, d_b[cr, cc] <= NEAR_B
    print(f"tickets within {NEAR_B} m of a building: flood {100 * nb_f.mean():.0f}%, control {100 * nb_c.mean():.0f}%")

    for k, (which, nb_only) in SCEN.items():
        f, c = d_by[which][fr, fc], d_by[which][cr, cc]
        if nb_only:
            f, c = f[nb_f], c[nb_c]
        pool[k][0].append(f)
        pool[k][1].append(c)
        s = summarize(f, c)
        print(line(k, s))
        if "PRIMARY" in k:
            passes[name] = s is not None and s[1] > s[3]

print("\n=== pooled over the three areas (bootstrap ignores clustering, so intervals are optimistic)")
for k in SCEN:
    print(line(k, summarize(np.concatenate(pool[k][0]), np.concatenate(pool[k][1]))))
n_pass = sum(passes.values())
print(f"\nPRE-REGISTERED CRITERION (scenario D at 10 m, flood interval lower bound above control rate "
      f"in at least 2 of 3 areas): {n_pass}/3 -> {'PASS' if n_pass >= 2 else 'FAIL'}")
