import time
from pathlib import Path

import psutil
import rasterio
from rasterio.windows import Window

from h3lowpoints import analyze

SRC = "data/raw/USGS_one_meter_x62y336_TX_Central_B1_2017.tif"
out = Path("data/bench")
out.mkdir(parents=True, exist_ok=True)
proc = psutil.Process()
print("size | cells | seconds | peak memory | depressions  (first row includes numba compile time)")
with rasterio.open(SRC) as src:
    for n in (500, 2000, 3500, 5000, 7000):
        win = Window(1500, 1500, n, n)
        prof = src.profile.copy()
        prof.update(width=n, height=n, transform=src.window_transform(win), compress="deflate")
        path = out / f"w{n}.tif"
        with rasterio.open(path, "w", **prof) as dst:
            dst.write(src.read(1, window=win), 1)
        t0 = time.time()
        res = analyze(str(path))
        dt = time.time() - t0
        mi = proc.memory_info()
        peak = getattr(mi, "peak_wset", mi.rss) / 1e9
        print(f"{n} x {n} | {n * n / 1e6:.1f} M | {dt:.1f} s | {peak:.1f} GB | {len(res.depressions)}", flush=True)
        path.unlink()
