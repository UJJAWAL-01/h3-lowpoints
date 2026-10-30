"""National overview for the explorer app: summaries per H3 cell at resolutions 4, 5 and 6.

    python -I app/tools/overview.py DATASET WORK

Per cell: land covered by the elevation data (m2), depressed area (m2) and volume (m3), from the
res-10 cells table (exact sums of children); and per depression whose lowest point lies in the
cell: the count, how many fill in the 2-year 24-hour storm (`fills_in_years` <= 2) and how many
are landlocked. One gzipped binary per resolution (overview_<res>.bin): uint64 cells (sorted),
then float32 covered, depressed, volume, then uint32 count, fills_2yr, landlocked.
Summaries only: at these levels a cell holds thousands of unrelated depressions, never a hotspot.
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np

RES = (4, 5, 6)


def parent(cells, res):
    cells = np.asarray(cells, dtype=np.uint64)
    cells = (cells & ~np.uint64(0xF << 52)) | np.uint64(res << 52)
    return cells | np.uint64((1 << (3 * (15 - res))) - 1)


def main(dataset, work):
    import pyarrow.parquet as pq

    c = pq.read_table(dataset / "cells.parquet", columns=["h3", "depress_area_m2", "depress_volume_m3",
                                                          "covered_area_m2"]).to_pandas()
    cells = np.array([int(s, 16) for s in c["h3"]], dtype=np.uint64)
    a = dict(np.load(work / "national.npz"))
    fills2 = (a["fills_in_years"] <= 2).astype(float)
    land = a["landlocked"].astype(float)
    summary = {}
    for res in RES:
        p = parent(cells, res)
        u, inv = np.unique(p, return_inverse=True)
        cov = np.bincount(inv, weights=c["covered_area_m2"].to_numpy(float))
        dep = np.bincount(inv, weights=c["depress_area_m2"].to_numpy(float))
        vol = np.bincount(inv, weights=c["depress_volume_m3"].to_numpy(float))
        pd_ = parent(a["low"], res)
        pos = np.searchsorted(u, pd_)
        ok = (pos < len(u)) & (u[np.minimum(pos, len(u) - 1)] == pd_)
        cnt = np.bincount(pos[ok], minlength=len(u))
        f2 = np.bincount(pos[ok], weights=fills2[ok], minlength=len(u))
        ll = np.bincount(pos[ok], weights=land[ok], minlength=len(u))
        raw = (u.astype("<u8").tobytes() + np.stack([cov, dep, vol]).astype("<f4").tobytes()
               + np.stack([cnt, f2, ll]).astype("<u4").tobytes())
        z = gzip.compress(raw, compresslevel=9, mtime=0)
        (work / f"overview_{res}.bin").write_bytes(z)
        summary[res] = {"cells": int(len(u)), "kb": round(len(z) / 1e3, 1),
                        "depressions_placed": int(cnt.sum()), "depressions_total": int(len(a["low"]))}
    (work / "overview.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
