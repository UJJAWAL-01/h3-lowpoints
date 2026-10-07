from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window
from scipy.spatial import cKDTree

from h3lowpoints import analyze, analyze_tiled

SRC = "data/raw/USGS_one_meter_x62y336_TX_Central_B1_2017.tif"
N, TILE = 7000, 2500
tmp = Path("data/bench/tilecheck.tif")
tmp.parent.mkdir(parents=True, exist_ok=True)
with rasterio.open(SRC) as src:
    win = Window(1500, 1500, N, N)
    prof = src.profile.copy()
    prof.update(width=N, height=N, transform=src.window_transform(win), compress="deflate")
    with rasterio.open(tmp, "w", **prof) as dst:
        dst.write(src.read(1, window=win), 1)

whole = analyze(str(tmp)).depressions
with rasterio.open(tmp) as s:
    crs, inv = s.crs, ~s.transform
to_xy = Transformer.from_crs(4326, crs, always_xy=True)


def pixels(df):
    x, y = to_xy.transform(df["lon"].to_numpy(), df["lat"].to_numpy())
    c, r = inv @ (np.asarray(x), np.asarray(y))
    return np.c_[np.floor(r), np.floor(c)]


pa = pixels(whole)
print(f"whole raster: {len(whole)} depressions, {int(whole['edge'].sum())} flagged edge\n")
for buf in (16, 64, 256):
    tiled = analyze_tiled(str(tmp), tile=TILE, buffer=buf).depressions
    pb = pixels(tiled)
    d_ab, j = cKDTree(pb).query(pa)
    nearest = tiled.iloc[j].reset_index(drop=True)
    found = d_ab <= 15
    d_depth = np.abs(whole["max_depth_m"].to_numpy() - nearest["max_depth_m"].to_numpy())
    d_area = np.abs(whole["area_m2"].to_numpy() - nearest["area_m2"].to_numpy()) / whole["area_m2"].to_numpy()
    same = found & (d_depth <= 0.02) & (d_area <= 0.02)
    worst = f"{d_depth[found].max():.2f}" if found.any() else "n/a"
    print(f"buffer {buf:3d}: tiled {len(tiled)} | identical {int(same.sum())}/{len(whole)} | "
          f"different {int((found & ~same).sum())} | missing {int((~found).sum())} | "
          f"largest depth difference {worst} m | edge-flagged {int(tiled['edge'].sum())}")
    if buf == 256:
        for k in np.where(~found)[0]:
            w = whole.iloc[k]
            score = (np.abs(tiled["area_m2"].to_numpy() - w["area_m2"]) / w["area_m2"]
                     + np.abs(tiled["max_depth_m"].to_numpy() - w["max_depth_m"]))
            cand = int(np.argmin(score))
            t = tiled.iloc[cand]
            ra, ca = pa[k]
            rb, cb = pb[cand]
            print(f"\n  whole: depth {w['max_depth_m']:.2f} m, area {w['area_m2']:.0f} m2, low point "
                  f"row {int(ra)} col {int(ca)} (row%{TILE}={int(ra) % TILE}, col%{TILE}={int(ca) % TILE})")
            print(f"  tiled: depth {t['max_depth_m']:.2f} m, area {t['area_m2']:.0f} m2, low point "
                  f"row {int(rb)} col {int(cb)} (row%{TILE}={int(rb) % TILE}, col%{TILE}={int(cb) % TILE}); "
                  f"{np.hypot(ra - rb, ca - cb):.0f} m apart")
tmp.unlink()
