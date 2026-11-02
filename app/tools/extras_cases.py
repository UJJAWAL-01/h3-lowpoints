"""Cases for the extras checks in a real browser (app/tools/extras_test.mjs).

    python -I app/tools/extras_cases.py DATASET PACKS_WORK OUT_JSON

check3   30 depressions (10 of each road class): rim and ground from the dataset (water_level_m,
         ground_m), road class and distance from roads.parquet; the app must show the same.
gallery  20 random depressions of road class 2 and 20 of class 1 (seed 1), for the owner to look at.
counts   national counts by class, by state (lowest point), and by depth.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


def main(dataset, work, out):
    import h3
    dep = pq.read_table(Path(dataset) / "depressions.parquet",
                        columns=["id", "lat", "lon", "h3_lowpoint", "max_depth_m", "water_level_m", "ground_m"]).to_pandas()
    sus = pq.read_table(Path(dataset) / "susceptibility.parquet", columns=["id", "drained", "landlocked"]).to_pandas()
    d = dep.merge(pd.read_parquet(work / "roads.parquet"), on="id").merge(sus, on="id")
    rng = np.random.default_rng(1)
    region = lambda r: h3.cell_to_parent(r.h3_lowpoint, 4)     # packs go by the lowest point's res-10 cell, not lat/lon
    check3 = []
    for c in (2, 1, 0):
        for _, r in d[d.road == c].sample(10, random_state=int(rng.integers(1e9))).iterrows():
            check3.append({"id": str(int(r.id)), "region": region(r), "rim": float(r.water_level_m), "ground": float(r.ground_m),
                           "road": int(r.road), "roadM": None if np.isnan(r.d_low) else float(r.d_low)})
    gallery = []
    for c in (2, 1):
        for _, r in d[d.road == c].sample(20, random_state=int(rng.integers(1e9))).iterrows():
            gallery.append({"id": str(int(r.id)), "region": region(r), "lat": float(r.lat), "lon": float(r.lon), "road": int(r.road),
                            "depth": round(float(r.max_depth_m), 2), "roadM": round(float(r.d_low), 1)})
    depth_bins = pd.cut(d.max_depth_m, [0, 1, 2, 5, 1e9], right=False, labels=["0.5-1 m", "1-2 m", "2-5 m", "5 m+"])
    by_depth = {str(k): {"depressions": int(len(g)), "class1": int((g.road == 1).sum()), "class2": int((g.road == 2).sum())}
                for k, g in d.groupby(depth_bins, observed=True)}
    counts = {"depressions": int(len(d)), "class1": int((d.road == 1).sum()), "class2": int((d.road == 2).sum()),
              "landlocked_now_class1_or_2": int(((d.road > 0) & d.landlocked).sum()),
              "drained_after": round(float(((d.road > 0) | d.drained).mean()), 4), "drained_before": round(float(d.drained.mean()), 4),
              "by_depth": by_depth}
    Path(out).write_text(json.dumps({"check3": check3, "gallery": gallery, "counts": counts}, indent=1))
    print(json.dumps(counts, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
