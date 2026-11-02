"""How the rain of a storm builds up over its hours, for the explorer's timeline: NOAA Atlas 14
temporal distributions, by region, as the h3-lowpoints library uses them (storms.storm_response).

    python -I app/tools/temporal.py PACKS_WORK TEMPORAL_CACHE OUT_JSON [CHECK_DATASET]

regions  for every H3 resolution-4 pack, the NOAA temporal region at the centre of each of its 7
         resolution-5 children (cell_to_children order), as an index into `names` (-1: none)
curves   per region, for the published durations 6, 12, 24 and 96 h, the median (50%) cumulative
         curve of all storms ("all") and of storms by the quarter with most rain ("first" ...
         "fourth"), at 49 equal times (share of the duration -> share of the rain), from
         storms.pattern_steps, as the library steps them
check    (with CHECK_DATASET) 20,000 random depressions: the region at the lowest point itself
         against the resolution-5 lookup, and the time to a given share of rain both ways
"""
import json
import sys
from pathlib import Path

import numpy as np

CASES = ("all", "first", "second", "third", "fourth")
HOURS = (6, 12, 24, 96)
N = 48


def main(work, cache, out, dataset=None):
    import h3
    from h3lowpoints.sources.rainfall import TemporalPatterns
    from h3lowpoints.storms import pattern_steps

    tp = TemporalPatterns(cache_dir=cache)
    packs = sorted(json.loads((Path(work) / "index.json").read_text())["packs"])
    children = {r: sorted(h3.cell_to_children(r, 5)) for r in packs}
    flat = [c for r in packs for c in children[r]]
    ll = np.array([h3.cell_to_latlng(c) for c in flat])
    found = tp.region(ll[:, 0], ll[:, 1])
    names = sorted({x for x in found if x is not None})
    idx = {n: i for i, n in enumerate(names)}
    at = {c: (idx[x] if x is not None else -1) for c, x in zip(flat, found)}
    regions = {r: [at[c] for c in children[r]] for r in packs}
    curves = {}
    for name in names:
        curves[name] = {}
        for hours in HOURS:
            curves[name][str(hours)] = {case: [round(float(x), 5) for x in pattern_steps(*tp.pattern(name, hours, case, 50), N)]
                                        for case in CASES}
    doc = {"source": "NOAA Atlas 14 temporal distributions (median curves), via h3-lowpoints TemporalPatterns",
           "steps": N, "hours": list(HOURS), "cases": list(CASES), "names": names, "regions": regions, "curves": curves}
    Path(out).write_text(json.dumps(doc, separators=(",", ":")))
    print(f"{len(names)} regions, {len(packs)} packs, {sum(v is None for v in found)} of {len(flat)} res-5 cells outside every region;"
          f" {Path(out).stat().st_size / 1e3:.0f} kB")
    if dataset:
        check(dataset, tp, doc)


def check(dataset, tp, doc):
    import h3
    import pyarrow.parquet as pq
    from h3lowpoints.storms import pattern_steps

    d = pq.read_table(Path(dataset) / "depressions.parquet", columns=["lat", "lon", "h3_lowpoint"]).to_pandas()
    s = d.sample(20000, random_state=1)
    exact = tp.region(s.lat.values, s.lon.values)
    looked = []
    for c in s.h3_lowpoint:
        r4, r5 = h3.cell_to_parent(c, 4), h3.cell_to_parent(c, 5)
        kids = sorted(h3.cell_to_children(r4, 5))
        i = doc["regions"].get(r4, [-1] * 7)[kids.index(r5)]
        looked.append(doc["names"][i] if i >= 0 else None)
    looked = np.array(looked, dtype=object)
    differ = np.array([a != b for a, b in zip(exact, looked)])
    # how much it matters: time to half the rain of a 24-hour storm, exact region vs the lookup
    def t_half(name):
        if name is None:
            return np.nan
        cum = np.asarray(doc["curves"][name]["24"]["all"])
        return float(np.interp(0.5, cum, np.linspace(0, 24, len(cum))))
    gaps = [abs(t_half(a) - t_half(b)) for a, b, dd in zip(exact, looked, differ) if dd and a and b]
    print(json.dumps({"sampled": len(s), "differ": int(differ.sum()), "differ_share": round(float(differ.mean()), 4),
                      "half_rain_hours_gap_when_differ": {"median": round(float(np.median(gaps)), 2) if gaps else 0,
                                                          "max": round(float(np.max(gaps)), 2) if gaps else 0}}))


if __name__ == "__main__":
    main(*sys.argv[1:4], *(sys.argv[4:5] or [None]))
