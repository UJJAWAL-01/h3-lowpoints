"""Expected answers for the engine checks in a real browser (app/tools/check_engine_browser.mjs).

    python -I app/tools/engine_cases.py PACKS_WORK RARITY_WORK OUT_JSON

paths      40 depressions (25 whose overflow first goes into another res-4 area, 15 at random): the
           chain of dataset ids from the national arrays (spill_to), cut by the app's rules (more
           than 200 steps, or a 5th other area), and whether it ends by leaving the map
summaries  the 3 test regions, two storms, whole pack: counts and totals from the library
           (check_storm.reference_steps), with the dry and wet range
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_sim import REGIONS, read_pack, volume_of  # noqa: E402
from check_storm import rarity_block, reference_rain, reference_steps  # noqa: E402

MAX_PATH, MAX_PATH_PACKS = 200, 4
STORMS = ({"kind": "amount", "mm": 127.0, "hours": 6, "condition": "average"},
          {"kind": "rarity", "years": 100, "hours": 24, "condition": "wet"})


def expected_path(a, i):
    ids, target, region = a["ids"], a["target"], a["region"]
    out, others, j = [], set(), i
    while True:
        out.append(str(int(ids[j])))
        if len(out) > MAX_PATH:
            return out, False, True
        t = int(target[j])
        if t < 0:
            return out, True, False
        if region[t] != region[j]:
            r = int(region[t])
            if r not in others and len(others) >= MAX_PATH_PACKS:
                return out, False, True
            others.add(r)
        j = t


def main(work, rwork, out_file):
    import h3

    a = dict(np.load(work / "national.npz"))
    rng = np.random.default_rng(0)
    tgt = a["target"]
    cross = np.nonzero((tgt >= 0) & (a["region"][np.maximum(tgt, 0)] != a["region"]))[0]
    pick = np.r_[rng.choice(cross, 25, replace=False), rng.choice(len(tgt), 15, replace=False)]
    paths = []
    for i in pick:
        ids, leaves, cut = expected_path(a, int(i))
        paths.append({"region": format(int(a["region"][i]), "x"), "id": ids[0], "ids": ids, "leaves": leaves,
                      "cut": cut})
    del a

    index = json.loads((work / "index.json").read_text())
    rindex = json.loads((rwork / "rarity_index.json").read_text())
    summaries = []
    with open(work / "packs.bin", "rb") as f, open(rwork / "rarity.bin", "rb") as rf:
        for name, (lat, lon) in REGIONS.items():
            region = h3.latlng_to_cell(lat, lon, 4)
            off, length = index["packs"][region][:2]
            f.seek(off)
            p = read_pack(f.read(length))
            roff, rlen, rn = rindex["regions"][region]
            rf.seek(roff)
            block = rarity_block(rf.read(rlen), rn)
            vol = volume_of(p)
            for storm in STORMS:
                rain = reference_rain(p, storm, block, rindex.get("res", 7))
                rec = {"name": name, "region": region, "storm": storm, "range": {}}
                for cond in ("dry", "average", "wet"):
                    ref = reference_steps(p, rain, cond)
                    known = ~np.isnan(ref["store"][-1])
                    counts = {"fill": int((ref["fillStep"] >= 0).sum()), "overflow": int((ref["spillStep"] >= 0).sum())}
                    rec["range"][cond] = counts
                    if cond == storm["condition"]:
                        rec.update(depressions=len(vol), unknown=int((~known).sum()), **counts,
                                   holdWater=int((ref["store"][-1][known] > 0).sum()),
                                   heldM3=float(np.nansum(ref["store"][-1])),
                                   wetAreaM2=float(np.nansum(ref["area"][-1])))
                summaries.append(rec)
    Path(out_file).write_text(json.dumps({"paths": paths, "summaries": summaries}))
    print(len(paths), "paths;", sum(len(p["ids"]) for p in paths), "steps;",
          sum(p["cut"] for p in paths), "cut;", len(summaries), "summaries")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
