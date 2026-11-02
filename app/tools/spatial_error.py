"""How much the app's method loses when a storm varies over the land (spec 6.3, section 4).

    python -I app/tools/spatial_error.py WORK ATLAS_CACHE

For NOAA Atlas 14 storms (each depression its own depth at its lowest point), average ground:
  exact  the national cascade, every depression with its own depth
  app    what the explorer does: links between regions cut; the water a depression receives from
         other regions read from its uniform-storm curve at its own depth (external.npz)
Error of water held per depression, for depressions holding at least 1 m3 in the exact run.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

STORMS = [(100, 24.0), (2, 24.0), (10, 1.0)]


def main(work, cache):
    from h3lowpoints.hydro.cascade import _order, cascade
    from h3lowpoints.runoff import adjust_curve_number, runoff_mm
    from h3lowpoints.sources.rainfall import Atlas14Storms

    t0 = time.time()
    a = dict(np.load(work / "national.npz"))
    e = np.load(work / "external.npz")
    grid, ext, rec = e["grid"], e["average"], e["receivers"]
    target, region, volume = a["target"], a["region"], a["volume"]
    linked = target >= 0
    cross = np.zeros(len(target), dtype=bool)
    cross[linked] = region[linked] != region[target[linked]]
    cut = np.where(cross, -1, target)
    order_cut = _order(cut)
    cn = adjust_curve_number(a["cn"], "average")
    storms = Atlas14Storms(cache_dir=cache)
    out = {}
    for years, hours in STORMS:
        p = storms.depth(years, hours, a["lat"], a["lon"])
        own = np.where(np.isnan(cn) | np.isnan(p), np.nan, runoff_mm(p, cn) * a["catch"] / 1000.0)
        inflow, _ = cascade(a["order"], target, own, volume)
        exact = np.minimum(inflow, volume)
        pr = p[rec]
        j = np.clip(np.searchsorted(grid, pr, "right") - 1, 0, len(grid) - 2)
        t = np.clip((pr - grid[j]) / (grid[j + 1] - grid[j]), 0, 1)
        rows = np.arange(len(rec))
        add = ext[rows, j] * (1 - t) + ext[rows, j + 1] * t
        own2 = own.copy()
        own2[rec] = own2[rec] + add
        inflow2, _ = cascade(order_cut, cut, own2, volume)
        app = np.minimum(inflow2, volume)
        ok = np.isfinite(exact) & np.isfinite(app) & (exact >= 1.0)
        rel = np.abs(app[ok] - exact[ok]) / exact[ok]
        key = f"{years}-year {hours:g}-hour"
        out[key] = {"depressions_compared": int(ok.sum()), "without_atlas14": int(np.isnan(p).sum()),
                    "median": float(np.median(rel)), "p95": float(np.percentile(rel, 95)),
                    "p99": float(np.percentile(rel, 99)), "share_over_1pct": float((rel > 0.01).mean()),
                    "total_water_rel": float(abs(np.nansum(app[ok]) - np.nansum(exact[ok])) / np.nansum(exact[ok])),
                    "rain_mm_median": float(np.nanmedian(p))}
        print(key, json.dumps(out[key]), flush=True)
    out["seconds"] = round(time.time() - t0)
    (work / "spatial_error.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
