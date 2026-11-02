"""Check pack format 2 against format 1 and the dataset (spec 6.3, check 2).

    python -I app/tools/check_packs_v2.py DATASET WORK_V1 WORK_V2

1. Every array of format 1 is byte-identical in format 2, in every pack.
2. target_id is the dataset's spill_to for every depression (0 where it spills off the data or to a
   depression outside the dataset), and target_region is the res-4 region of that target's lowest point.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_sim import read_pack  # noqa: E402


def main(dataset, v1, v2):
    import pyarrow.parquet as pq

    i1 = json.loads((v1 / "index.json").read_text())["packs"]
    i2 = json.loads((v2 / "index.json").read_text())["packs"]
    assert set(i1) == set(i2), "different regions"
    d = pq.read_table(dataset / "depressions.parquet", columns=["id", "spill_to"]).to_pandas()
    ids = d["id"].to_numpy(np.int64)
    order = np.argsort(ids)
    sids, spill = ids[order], d["spill_to"].to_numpy(np.int64)[order]
    nat = np.load(v2 / "national.npz")
    nat_ids, nat_region = nat["ids"], nat["region"]
    norder = np.argsort(nat_ids)
    report = {"packs": 0, "fields_identical": 0, "depressions": 0, "target_ok": 0, "region_ok": 0,
              "spill_off_data": 0, "spill_outside_dataset": 0, "problems": []}
    with open(v1 / "packs.bin", "rb") as f1, open(v2 / "packs.bin", "rb") as f2:
        for k, region in enumerate(sorted(i1)):
            f1.seek(i1[region][0]); a = read_pack(f1.read(i1[region][1]))
            f2.seek(i2[region][0]); b = read_pack(f2.read(i2[region][1]))
            report["packs"] += 1
            same = all(name in b and a[name].tobytes() == b[name].tobytes() for name in a if name != "header")
            same = same and a["header"]["region"] == b["header"]["region"] == region
            if same:
                report["fields_identical"] += 1
            elif len(report["problems"]) < 20:
                report["problems"].append(f"{region}: format-1 fields differ")
            pid = b["id"].astype(np.int64)
            st = spill[np.searchsorted(sids, pid)]
            in_data = np.isin(st, sids)
            want = np.where((st != 0) & in_data, st, 0)
            ok = b["target_id"] == want
            report["depressions"] += len(pid)
            report["target_ok"] += int(ok.sum())
            report["spill_off_data"] += int((st == 0).sum())
            report["spill_outside_dataset"] += int(((st != 0) & ~in_data).sum())
            has = want != 0
            reg_want = np.zeros(len(pid), dtype=np.uint64)
            reg_want[has] = nat_region[norder[np.searchsorted(nat_ids[norder], want[has])]]
            report["region_ok"] += int((b["target_region"] == reg_want).sum())
            if not ok.all() and len(report["problems"]) < 20:
                report["problems"].append(f"{region}: {int((~ok).sum())} targets differ")
            if k % 500 == 0:
                print(f"{k}/{len(i1)} packs", flush=True)
    report["pass"] = (report["fields_identical"] == report["packs"] and report["target_ok"] == report["depressions"]
                      and report["region_ok"] == report["depressions"])
    (v2 / "check_packs_v2.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
