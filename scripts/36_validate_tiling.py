import time
from pathlib import Path

import numpy as np
import psutil
import rasterio
from rasterio.windows import Window
from scipy.spatial import cKDTree

from h3lowpoints import analyze, analyze_tiled

SRC = "data/raw/USGS_one_meter_x62y336_TX_Central_B1_2017.tif"
N, TILE, BUFFER = 7000, 2500, 256
tmp = Path("data/bench/tilecheck.tif")
tmp.parent.mkdir(parents=True, exist_ok=True)
with rasterio.open(SRC) as src:
    win = Window(1500, 1500, N, N)
    prof = src.profile.copy()
    prof.update(width=N, height=N, transform=src.window_transform(win), compress="deflate")
    with rasterio.open(tmp, "w", **prof) as dst:
        dst.write(src.read(1, window=win), 1)

proc = psutil.Process()


def peak_gb():
    mi = proc.memory_info()
    return getattr(mi, "peak_wset", mi.rss) / 1e9


t0 = time.time()
tiled = analyze_tiled(str(tmp), tile=TILE, buffer=BUFFER, verbose=True)
t_tiled, m_tiled = time.time() - t0, peak_gb()
t0 = time.time()
whole = analyze(str(tmp))
t_whole, m_whole = time.time() - t0, peak_gb()
print(f"\ntiled: {t_tiled:.0f} s, peak {m_tiled:.1f} GB | whole: {t_whole:.0f} s, peak {m_whole:.1f} GB")
print("(the tiled peak includes about 0.3 GB from writing the test file)")

A, B = whole.depressions, tiled.depressions
print(f"\nwhole: {len(A)} depressions | tiled: {len(B)} ({int(B['edge'].sum())} flagged edge)")
lat0 = A["lat"].mean()


def metres(df):
    return np.c_[df["lon"].to_numpy() * 111320 * np.cos(np.radians(lat0)),
                 df["lat"].to_numpy() * 110574]


dist, j = cKDTree(metres(B)).query(metres(A))
Bj = B.iloc[j].reset_index(drop=True)
found = dist <= 15
da = np.abs(A["max_depth_m"].to_numpy() - Bj["max_depth_m"].to_numpy())
ra = np.abs(A["area_m2"].to_numpy() - Bj["area_m2"].to_numpy()) / A["area_m2"].to_numpy()
same = found & (da <= 0.02) & (ra <= 0.02)
a_ok = ~A["edge"].to_numpy()
b_edge = Bj["edge"].to_numpy()

for name, sel in (("all", a_ok), ("significant", a_ok & A["significant"].to_numpy())):
    n = int(sel.sum())
    print(f"\nwhole-raster depressions not at the raster edge, {name}: {n}")
    print(f"  identical in the tiled run (depth within 2 cm, area within 2%): {int((same & sel).sum())}")
    diff = found & ~same & sel
    print(f"  found but different: {int(diff.sum())} (tiled run had flagged edge: {int((diff & b_edge).sum())})")
    print(f"  missing from the tiled run: {int((~found & sel).sum())}")
    if diff.any():
        print(f"  largest depth difference among 'different': {da[diff].max():.2f} m")

extra = int((cKDTree(metres(A)).query(metres(B))[0] > 15).sum())
print(f"\ntiled depressions with no whole-raster counterpart within 15 m: {extra}")
print(f"volume, m3: whole (non-edge) {A.loc[a_ok, 'volume_m3'].sum():.0f} | "
      f"tiled (all) {B['volume_m3'].sum():.0f} | tiled (non-edge) {B.loc[~B['edge'], 'volume_m3'].sum():.0f}")
tmp.unlink()
