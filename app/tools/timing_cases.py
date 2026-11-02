"""Expected answers for the timeline-in-hours checks (app/tools/storm_test.mjs, timing).

    python -I app/tools/timing_cases.py PACKS_WORK RARITY_WORK TEMPORAL_CACHE TEMPORAL_JSON DATASET OUT_JSON

curves   every curve in temporal.json against the library (TemporalPatterns + storms.pattern_steps)
lookup   200 random depressions: the region the explorer's resolution-5 lookup gives
fill     12 depressions in central Houston, the 10-year 24-hour storm, average ground: the exact
         share of the storm's rain at which each is full (bisection on the library's cascade, 40
         halvings), and the time to it on its region's curves (typical and by quarter)
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_sim import read_pack, volume_of  # noqa: E402
from check_storm import CONDITIONS, rarity_block, reference_rain  # noqa: E402

STORM = {"kind": "rarity", "years": 10, "hours": 24, "condition": "average"}


def time_at(curve, r):
    """First time share at which share r of the rain has fallen (as temporal.js timeAt)."""
    c = np.asarray(curve, float)
    n = len(c) - 1
    if r <= c[0]:
        return 0.0
    if r >= c[-1]:
        i = n
        while i > 0 and c[i - 1] >= c[-1]:
            i -= 1
        return i / n
    i = int(np.nonzero(c[1:] >= r)[0][0])
    d = c[i + 1] - c[i]
    return (i + ((r - c[i]) / d if d > 0 else 0.0)) / n


def stored(p, rain, condition):
    from h3lowpoints.hydro.cascade import _order, cascade
    from h3lowpoints.runoff import adjust_curve_number, runoff_mm

    cn = adjust_curve_number(p["cn"].astype(float), condition)
    c = CONDITIONS.index(condition)
    own = np.where(np.isnan(cn) | np.isnan(rain), np.nan, runoff_mm(rain, cn) * p["catch"].astype(float) / 1000.0)
    for k, i in enumerate(p["ext_dep"]):
        own[i] += np.interp(rain[i], p["ext_grid"].astype(float), p["ext_inflow"][k, c].astype(float))
    target = p["target"].astype(np.int64)
    inflow, _ = cascade(_order(target), target, own, volume_of(p))
    return np.minimum(inflow, volume_of(p))


def main(work, rwork, cache, tjson, dataset, out):
    import h3
    import pyarrow.parquet as pq
    from h3lowpoints.sources.rainfall import TemporalPatterns
    from h3lowpoints.storms import pattern_steps

    doc = json.loads(Path(tjson).read_text())
    tp = TemporalPatterns(cache_dir=cache)
    worst = 0.0
    for name in doc["names"]:
        for hours in doc["hours"]:
            for case in doc["cases"]:
                want = pattern_steps(*tp.pattern(name, hours, case, 50), doc["steps"])
                worst = max(worst, float(np.abs(np.asarray(doc["curves"][name][str(hours)][case]) - want).max()))
    d = pq.read_table(Path(dataset) / "depressions.parquet", columns=["lat", "lon", "h3_lowpoint"]).to_pandas().sample(200, random_state=7)
    lookup = []
    for lat, lon, c in zip(d.lat, d.lon, d.h3_lowpoint):
        la, lo = h3.cell_to_latlng(c)
        r5 = h3.latlng_to_cell(la, lo, 5); r4 = h3.cell_to_parent(r5, 4)
        kids = sorted(h3.cell_to_children(r4, 5))
        i = doc["regions"].get(r4, [-1] * 7)[kids.index(r5)]
        lookup.append({"lat": la, "lon": lo, "name": doc["names"][i] if i >= 0 else None})
    # exact fill shares in central Houston
    index = json.loads((Path(work) / "index.json").read_text())
    rindex = json.loads((Path(rwork) / "rarity_index.json").read_text())
    region = h3.latlng_to_cell(29.7604, -95.3698, 4)
    with open(Path(work) / "packs.bin", "rb") as f:
        off, length = index["packs"][region][:2]
        f.seek(off)
        p = read_pack(f.read(length))
    with open(Path(rwork) / "rarity.bin", "rb") as rf:
        roff, rlen, rn = rindex["regions"][region]
        rf.seek(roff)
        block = rarity_block(rf.read(rlen), rn)
    rain = reference_rain(p, STORM, block, rindex.get("res", 7))
    vol = volume_of(p)
    end = stored(p, rain, STORM["condition"])
    ll = np.array([h3.cell_to_latlng(format(int(c), "x")) for c in p["low"]])
    near = np.argsort((ll[:, 0] - 29.7604) ** 2 + (ll[:, 1] + 95.3698) ** 2)
    fills = []
    for i in near:
        if not (end[i] >= vol[i] * (1 - 1e-9)) or len(fills) >= 12:
            continue
        lo_, hi_ = 0.0, 1.0
        for _ in range(40):
            m = (lo_ + hi_) / 2
            if stored(p, rain * m, STORM["condition"])[i] >= vol[i] * (1 - 1e-9):
                hi_ = m
            else:
                lo_ = m
        la, lo = ll[i]
        r5 = h3.latlng_to_cell(la, lo, 5); r4 = h3.cell_to_parent(r5, 4)
        kids = sorted(h3.cell_to_children(r4, 5))
        k = doc["regions"][r4][kids.index(r5)]
        name = doc["names"][k] if k >= 0 else None
        times = {case: time_at(doc["curves"][name]["24"][case], hi_) * 24 for case in doc["cases"]} if name else None
        fills.append({"region": region, "id": str(int(p["id"][i])), "share": hi_, "name": name, "hours": times})
    Path(out).write_text(json.dumps({"curvesWorst": worst, "lookup": lookup, "storm": STORM, "fills": fills}, indent=1))
    print(json.dumps({"curvesWorst": worst, "lookup": len(lookup), "fills": [(x["id"], round(x["share"], 5), x["hours"] and round(x["hours"]["all"], 2)) for x in fills]}))


if __name__ == "__main__":
    main(*sys.argv[1:7])
