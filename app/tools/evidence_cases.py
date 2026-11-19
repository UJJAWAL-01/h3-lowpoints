"""Cases for the evidence checks in a real browser (app/tools/evidence_test.mjs), from dataset 0.4.

    python -I app/tools/evidence_cases.py DATASET04 V03_DEPRESSIONS OUT_JSON

cases    70 depressions (seed 2): 10 each seen wet, never seen wet, too small or too seldom seen,
         flooded by radar (with their flood days), road dams, bridges, and without evidence, each
         with every evidence value of dataset 0.4; the app must show the same.
totals   national counts the overview must add up to.
"""
import json
import sys
from pathlib import Path

import h3
import numpy as np
import pandas as pd


def main(ds, v03, out):
    d = pd.read_parquet(v03, columns=["id", "h3_lowpoint", "lat", "lon"])
    w = pd.read_parquet(ds / "water_history.parquet")
    f = pd.read_parquet(ds / "flood_events.parquet", columns=["id", "scenes_observed", "scenes_flooded"])
    g = pd.read_parquet(ds / "confidence.parquet",
                        columns=["id", "grade", "base_grade", "road_dam", "bridge_crossing", "breached_depth_m"])
    s = pd.read_parquet(ds / "flood_seen.parquet", columns=["id", "datetime", "flooded"])
    s = s[s.flooded]
    days = (pd.to_datetime(s.datetime, utc=True).dt.strftime("%Y-%m-%d")).groupby(s.id).agg(lambda x: sorted(set(x)))
    t = d.merge(w, on="id", how="left").merge(f, on="id", how="left").merge(g, on="id", how="left")
    rng = np.random.default_rng(2)
    groups = {
        "seen_wet": t.water_class.isin(["some_years", "seasonal", "permanent"]),
        "never_seen_wet": t.water_class.eq("never_seen_wet"),
        "cannot_tell": t.water_class.isin(["too_small", "too_few_views"]),
        "flooded": t.scenes_flooded.fillna(0) > 0,
        "road_dam": t.road_dam.fillna(False).astype(bool),
        "bridge": t.bridge_crossing.fillna(False).astype(bool),
        "no_evidence": t.water_class.isna(),
    }
    nn = lambda x: None if pd.isna(x) else x
    cases = []
    for name, m in groups.items():
        for _, r in t[m].sample(10, random_state=int(rng.integers(1e9))).iterrows():
            c = {"group": name, "id": str(int(r.id)), "region": h3.cell_to_parent(r.h3_lowpoint, 4),
                 "lat": float(r.lat), "lon": float(r.lon), "evidence": not pd.isna(r.water_class)}
            if c["evidence"]:
                c.update({"water_class": r.water_class, "wet_years": int(r.wet_years),
                          "clear_years": nn(None if pd.isna(r.clear_years) else int(r.clear_years)),
                          "first": int(r.first_wet_year), "last": int(r.last_wet_year), "month": int(r.wettest_month),
                          "observed": int(r.scenes_observed), "flooded": int(r.scenes_flooded),
                          "days": days.get(r.id, []), "grade": r.grade, "base": r.base_grade,
                          "road_dam": bool(r.road_dam), "bridge": bool(r.bridge_crossing),
                          "breached": float(r.breached_depth_m)})
            cases.append(c)
    totals = {"has": int(t.water_class.notna().sum()),
              "wet": int(groups["seen_wet"].sum()), "flooded": int(groups["flooded"].sum()),
              "A": int((t.grade == "A").sum()), "B": int((t.grade == "B").sum())}
    Path(out).write_text(json.dumps({"cases": cases, "totals": totals}, indent=1))
    print(f"{len(cases)} cases; totals {totals}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
