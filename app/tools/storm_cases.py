"""Expected answers for the 6.3e storm-bar checks in a real browser (app/tools/storm_test.mjs).

    python -I app/tools/storm_cases.py PACKS_WORK RARITY_WORK OUT_JSON

views    spec check 2: 3 places x 2 storms, the depressions whose lowest point lies in a box (a view):
         full, overflowing, holding water, ground under water and water held after each of the 16
         steps, the five that fill earliest (earliest step, then largest), and the range of rain
         depths, from the h3-lowpoints library (check_storm.reference_steps)
rarity   spec check 3: 5 points x 3 storms, how rare (years) by the library's rule on the same tables
         (rarity.years_from_table)
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_sim import read_pack, volume_of  # noqa: E402
from check_storm import STEPS, rarity_block, reference_rain, reference_steps  # noqa: E402
from rarity import years_from_table  # noqa: E402

# view boxes [w, s, e, n] about the size of a laptop screen at street to city scale
VIEWS = {"houston": [-95.385, 29.752, -95.355, 29.770],
         "new_york": [-74.012, 40.700, -73.985, 40.718],
         "potholes": [-99.03, 47.48, -98.97, 47.52]}
STORMS = ({"kind": "rarity", "years": 10, "hours": 24, "condition": "average"},
          {"kind": "amount", "mm": 75.0, "hours": 3, "condition": "dry"})
POINTS = {"houston": (29.7604, -95.3698), "new_york": (40.7128, -74.006), "chicago": (41.8781, -87.6298),
          "denver": (39.7392, -104.9903), "miami": (25.7617, -80.1918)}
RSTORMS = ((50.0, 1), (127.0, 6), (250.0, 24))


def main(work, rwork, out_file):
    import h3

    index = json.loads((work / "index.json").read_text())
    rindex = json.loads((rwork / "rarity_index.json").read_text())
    res = rindex.get("res", 7)
    views = []
    with open(work / "packs.bin", "rb") as f, open(rwork / "rarity.bin", "rb") as rf:
        def pack_of(region):
            off, length = index["packs"][region][:2]
            f.seek(off)
            return read_pack(f.read(length))

        def block_of(region):
            roff, rlen, rn = rindex["regions"][region]
            rf.seek(roff)
            return rarity_block(rf.read(rlen), rn)

        for name, box in VIEWS.items():
            w, s, e, n = box
            # every res-4 region with a depression whose lowest point can lie in the box
            regions = sorted({h3.cell_to_parent(c, 4) for c in h3.polygon_to_cells(
                h3.LatLngPoly([(s, w), (s, e), (n, e), (n, w)]), 7)} | {h3.latlng_to_cell((s + n) / 2, (w + e) / 2, 4)})
            regions = [r for r in regions if r in index["packs"]]
            for storm in STORMS:
                steps = [dict(fill=0, overflow=0, holdWater=0, wetAreaM2=0.0, heldM3=0.0) for _ in range(STEPS)]
                tot = dict(depressions=0, unknown=0)
                firsts, rains = [], []
                for region in regions:
                    p = pack_of(region)
                    rain = reference_rain(p, storm, block_of(region), res)
                    ref = reference_steps(p, rain, storm["condition"])
                    vol = volume_of(p)
                    ll = np.array([h3.cell_to_latlng(format(int(c), "x")) for c in p["low"]])
                    inside = (ll[:, 0] >= s) & (ll[:, 0] <= n) & (ll[:, 1] >= w) & (ll[:, 1] <= e)
                    known = ~np.isnan(ref["store"][-1])
                    tot["depressions"] += int(inside.sum())
                    tot["unknown"] += int((inside & ~known).sum())
                    rains += [float(x) for x in rain[inside] if np.isfinite(x)]
                    k = inside & known
                    for t in range(STEPS):
                        st = steps[t]
                        st["fill"] += int((k & (ref["fillStep"] >= 0) & (ref["fillStep"] <= t)).sum())
                        st["overflow"] += int((k & (ref["spillStep"] >= 0) & (ref["spillStep"] <= t)).sum())
                        st["holdWater"] += int((k & (ref["store"][t] > 0)).sum())
                        st["wetAreaM2"] += float(ref["area"][t][k].sum())
                        st["heldM3"] += float(ref["store"][t][k].sum())
                    for i in np.nonzero(k & (ref["fillStep"] >= 0))[0]:
                        firsts.append((int(ref["fillStep"][i]), -float(vol[i]), str(int(p["id"][i]))))
                firsts.sort(key=lambda x: (x[0], x[1], x[2]))
                views.append({"name": name, "box": box, "regions": regions, "storm": storm, **tot, "byStep": steps,
                              "first": [{"step": a, "volume": -b, "id": c} for a, b, c in firsts[:5]],
                              "rainMin": min(rains) if rains else None, "rainMax": max(rains) if rains else None})
        rarity = []
        for name, (lat, lon) in POINTS.items():
            region = h3.latlng_to_cell(lat, lon, 4)
            if region not in rindex["regions"]:
                continue
            cells, tables = block_of(region)
            c = int(h3.latlng_to_cell(lat, lon, res), 16)
            kk = int(np.searchsorted(cells, np.uint64(c)))
            table = tables[kk].astype(float)
            for mm, hours in RSTORMS:
                rarity.append({"name": name, "lat": lat, "lon": lon, "mm": mm, "hours": hours,
                               "years": years_from_table(mm, hours, table)})
    Path(out_file).write_text(json.dumps({"views": views, "rarity": rarity}, indent=1))
    print(len(views), "views;", len(rarity), "rarity cases;",
          [(v["name"], v["storm"]["kind"], v["depressions"], v["byStep"][-1]["fill"]) for v in views])


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
