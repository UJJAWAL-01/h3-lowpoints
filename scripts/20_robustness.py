import contextlib
import io
import runpy

import numpy as np
import pandas as pd
from rasterio import features
from scipy import ndimage

from h3lowpoints.sources.assets import existed_at, load_clipped

with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path("scripts/19_validate_311.py")

flood, ctrl = g["flood"], g["ctrl"]
labels, ids, mx, area = g["labels"], g["ids"], g["mx"], g["area"]
lut, maxlab, signif = g["lut"], g["maxlab"], g["signif"]
dist_all, ir, ic, inv, NEAR = g["dist_all"], g["ir"], g["ic"], g["inv"], g["NEAR"]
H, W = labels.shape
rng = np.random.default_rng(0)


def rc(df):
    c, r = inv * (df["x"].to_numpy(), df["y"].to_numpy())
    return r.astype(int), c.astype(int)


def dedupe(df):
    key = (np.round(df["x"] / 10).astype(int).astype(str) + "_"
           + np.round(df["y"] / 10).astype(int).astype(str))
    return df[~key.duplicated().to_numpy()]


sig = np.zeros(maxlab + 1, bool)
sig[ids[signif]] = True
d_sig = ndimage.distance_transform_edt(~sig[labels])
chans = existed_at(load_clipped("open_channel", g["BBOX"], g["crs"]), 2017)
near_chan = features.rasterize([(geom.buffer(30), 1) for geom in chans.geometry],
                               out_shape=(H, W), transform=g["transform"],
                               dtype="uint8").astype(bool)


def report(title, f_df, c_df):
    fr, fc = rc(f_df)
    cr, cc = rc(c_df)
    f, c = d_sig[fr, fc], d_sig[cr, cc]
    print(title)
    for d in (5, 10, 20):
        real, base = (f <= d).mean(), (c <= d).mean()
        boot = [(rng.choice(f, size=len(f)) <= d).mean() for _ in range(2000)]
        lo, hi = np.percentile(boot, [2.5, 97.5])
        print(f"  n_flood={len(f):4d} n_control={len(c):6d} within {d:2d} m: "
              f"flood {100 * real:4.1f}% (95% {100 * lo:.1f}-{100 * hi:.1f}) | "
              f"control {100 * base:4.1f}% | {real / max(base, 1e-6):.2f}x")


report("TEST 1 (significant depressions), all tickets:", flood, ctrl)
report("\nTEST 1b, one ticket per 10 m location:", dedupe(flood), dedupe(ctrl))
fr, fc = rc(flood)
cr, cc = rc(ctrl)
report("\nTEST 1c, only tickets > 30 m from a recorded open channel:",
       flood[~near_chan[fr, fc]], ctrl[~near_chan[cr, cc]])

area_l = np.zeros(maxlab + 1)
area_l[ids] = area


def dose(title, attr, edges, unit):
    print("\n" + title)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        def count(r, c):
            near = dist_all[r, c] <= NEAR
            v = attr[labels[ir[r, c], ic[r, c]]]
            return int((near & (v >= lo) & (v < hi)).sum())
        f, cn = count(fr, fc), count(cr, cc)
        n_dep = int(((attr[ids] >= lo) & (attr[ids] < hi)).sum())
        name = f"{lo:g}-{hi:g} {unit}" if hi < 1e8 else f"{lo:g}+ {unit}"
        rows.append((name, n_dep, f, cn, round(100 * f / max(f + cn, 1), 2)))
    print(pd.DataFrame(rows, columns=["bin", "n_depressions", "flood", "control",
                                      "flood_share_%"]).to_string(index=False))
    print(f"  baseline flood share of all tickets: {100 * len(flood) / (len(flood) + len(ctrl)):.2f}%")


dose("DOSE-RESPONSE by max depth (tickets within 15 m of a depression):", lut, [0.15, 0.3, 0.6, 1.0, 1e9], "m")
dose("DOSE-RESPONSE by area:", area_l, [0, 50, 150, 500, 1e9], "m2")
