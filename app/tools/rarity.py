"""How rare a storm is, for the explorer app: NOAA Atlas 14 depths per H3 resolution-7 cell.

    python -I app/tools/rarity.py WORK ATLAS_CACHE

For every res-7 cell of the dataset's res-4 regions, the depth (mm) of the storm of each published
duration from 1 to 48 hours and each return period (1 to 1,000 years), at the cell's centre, by
`Atlas14Storms` (the library). One gzipped block per region in rarity.bin (float32, cells x
durations x return periods, cells in sorted order), offsets in rarity_index.json.
Then the check (spec 6.1): the app's rule on these tables against the library at the exact point,
at 200 random points with random storms; and, separately, against the table at the cell's centre
(the JS arithmetic alone).
"""
import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np

HOURS = (1.0, 2.0, 3.0, 6.0, 12.0, 24.0, 48.0)
RES = 7                         # cells about 1.2 km across (res 6 left the 95th percentile at 17%)


def depth_table(storms, lat, lon):
    return np.stack([storms.depths(h, lat, lon) for h in HOURS], axis=1)       # (cells, 7, 10)


def years_from_table(depth_mm, hours, table):
    """The app's rule: log-log between the table's durations, then the library's years_of_depth."""
    from h3lowpoints.sources.rainfall import log_interpolate, years_of_depth
    h = np.asarray(HOURS)
    hi = int(np.searchsorted(h, hours))
    if h[hi] == hours:
        row = table[hi]
    else:
        w = (np.log(hours) - np.log(h[hi - 1])) / (np.log(h[hi]) - np.log(h[hi - 1]))
        row = log_interpolate(table[hi - 1], table[hi], w)
    return float(years_of_depth(np.array([depth_mm]), row[None, :])[0])


def main(work, cache):
    import h3
    from h3lowpoints.sources.rainfall import Atlas14Storms

    t0 = time.time()
    storms = Atlas14Storms(cache_dir=cache)
    regions = sorted(json.loads((work / "index.json").read_text())["packs"])
    index, offset = {}, 0
    with open(work / "rarity.bin", "wb") as f:
        for k, r in enumerate(regions):
            cells = sorted(h3.cell_to_children(r, RES))
            lat, lon = np.array([h3.cell_to_latlng(c) for c in cells]).T
            table = depth_table(storms, lat, lon).astype("<f4")
            ids = np.array([int(c, 16) for c in cells], dtype="<u8")
            z = gzip.compress(ids.tobytes() + table.tobytes(), compresslevel=9, mtime=0)
            f.write(z)
            index[r] = [offset, len(z), len(cells)]
            offset += len(z)
            if k % 500 == 0:
                print(f"rarity: {k}/{len(regions)} regions, {time.time() - t0:.0f} s", flush=True)
    (work / "rarity_index.json").write_text(json.dumps(
        {"file": "rarity.bin", "res": RES, "hours": HOURS, "years": [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000],
         "regions": index}, separators=(",", ":")))
    check(work, storms)
    print(f"rarity: done, {offset / 1e6:.1f} MB, {time.time() - t0:.0f} s")


def check(work, storms):
    """Spec 6.1 check 1 (rarity): table rule vs library at the exact point, 200 random points."""
    import h3
    rng = np.random.default_rng(0)
    out, n = [], 0
    while n < 200:
        lat, lon = rng.uniform(25, 49), rng.uniform(-124, -67)
        exact_tab = storms.depths(24.0, lat, lon)
        if np.isnan(exact_tab).any():
            continue                                    # outside Atlas 14 (sea, WA, OR, abroad)
        hours = float(np.exp(rng.uniform(np.log(1.0), np.log(48.0))))
        years = float(np.exp(rng.uniform(np.log(1.0), np.log(1000.0))))
        depth = float(np.exp(np.interp(np.log(years), np.log([1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]),
                                       np.log(storms.depths(hours, lat, lon)[0]))))
        lib = float(storms.return_period(depth, hours, lat, lon)[0])
        cell = h3.latlng_to_cell(lat, lon, RES)
        clat, clon = h3.cell_to_latlng(cell)
        table = depth_table(storms, np.array([clat]), np.array([clon]))[0]
        app = years_from_table(depth, hours, table)
        lib_centre = float(storms.return_period(depth, hours, clat, clon)[0])
        out.append({"lat": lat, "lon": lon, "hours": hours, "depth_mm": depth, "library": lib,
                    "app": app, "library_at_centre": lib_centre})
        n += 1
    a = np.array([[o["library"], o["app"], o["library_at_centre"]] for o in out])
    fin = np.isfinite(a).all(axis=1) & (a > 0).all(axis=1)
    math = np.abs(a[fin, 1] - a[fin, 2]) / a[fin, 2]
    place = np.abs(a[fin, 1] - a[fin, 0]) / a[fin, 0]
    res = {"points": len(out), "finite": int(fin.sum()),
           "app_rule_vs_library_same_point": {"max": float(math.max()), "median": float(np.median(math))},
           "cell_centre_vs_exact_point": {"median": float(np.median(place)), "p95": float(np.percentile(place, 95)),
                                          "max": float(place.max())},
           "pass_1pct_rule": bool(math.max() <= 0.01)}
    (work / "rarity_check.json").write_text(json.dumps({**res, "points_detail": out}, indent=1))
    print("rarity check:", json.dumps(res))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
