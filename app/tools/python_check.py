"""Run the explorer's "Do this in Python" snippets (app/src/ui/python.js) with the library and compare
their numbers with the map's (spec 6.4c).

    python -I app/tools/python_check.py NATIONAL_DIR STORM_CASES_JSON OUT_DIR

depressions  4 per test view (smallest, a third, two thirds and largest by volume, among those whose
             lowest point lies in the view): the snippet's depth, area, volume, catchment, curve
             number, rain from its own land and storm that fills it against dataset 0.3 (what the
             packs and the panel show), 1e-6 relative
storms       the 6 view storms of storm_cases.json (the app's end-of-storm counts, which storm_test
             checks against the app): depressions in view, full, overflowing (equal) and water held
             (1e-6 relative). Storms by rarity: the counts must be equal; the water held is reported,
             not required to match, because the map takes each depression's storm depth at its
             lowest point and the library averages it over the catchment (measured in 6.3b)
Each snippet is the text python.js produces, run as it is in OUT_DIR (downloads cached there);
a line is appended that prints its result as JSON. Writes OUT_DIR/python_check.json.
"""
import json
import subprocess
import sys
from pathlib import Path

import h3
import numpy as np
import pyarrow.dataset as ds

HERE = Path(__file__).resolve().parent
APP = HERE.parent
nat, cases_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
COLS = ["max_depth_m", "area_m2", "volume_m3", "catchment_area_m2", "curve_number", "rain_to_fill_mm",
        "fills_in_years"]
TAIL = {"depression": '\nimport json as _j\nprint("@@" + _j.dumps(here[["h3_lowpoint_15"] + %r].to_dict("records")))\n' % COLS,
        "storm": '\nimport json as _j\nprint("@@" + _j.dumps({"depressions": len(in_view), '
                 '"fill": int((in_view.peak_fill >= 1 - 1e-9).sum()), "overflow": int((in_view.overflow_m3 > 0).sum()), '
                 '"heldM3": float(in_view.water_left_m3.sum())}))\n'}


def snippet(kind, arg):
    js = (f'import("{(APP / "src" / "ui" / "python.js").as_uri()}").then((m) => '
          f'process.stdout.write(m.{kind}Snippet(...{json.dumps(arg)})))')
    return subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout


def run(name, text, kind):
    path = out / f"{name}.py"
    path.write_text(text + TAIL[kind], encoding="utf-8")
    p = subprocess.run([sys.executable, "-I", path.name], cwd=out, capture_output=True, text=True)
    (out / f"{name}.log").write_text(p.stdout + p.stderr, encoding="utf-8")
    line = [x for x in p.stdout.splitlines() if x.startswith("@@")]
    return json.loads(line[-1][2:]) if p.returncode == 0 and line else {"error": p.stderr[-800:]}


def close(a, b, tol=1e-6):
    if a is None or b is None:
        return a is None and b is None
    a, b = float(a), float(b)
    if np.isnan(a) or np.isnan(b):
        return np.isnan(a) and np.isnan(b)
    if np.isinf(a) or np.isinf(b):
        return a == b
    return abs(a - b) <= tol * max(abs(b), 1e-12)


cases = json.loads(cases_path.read_text(encoding="utf-8"))
views = {}
for v in cases["views"]:
    views.setdefault(v["name"], v["box"])
report = {"depressions": [], "storms": []}
dep_table = ds.dataset(nat / "depressions.parquet")
sus_table = ds.dataset(nat / "susceptibility.parquet")
for name, (w, s, e, n) in views.items():
    near = dep_table.to_table(filter=(ds.field("lon") >= w - 0.01) & (ds.field("lon") <= e + 0.01)
                              & (ds.field("lat") >= s - 0.01) & (ds.field("lat") <= n + 0.01)).to_pandas()
    cell = np.array([h3.cell_to_latlng(c) for c in near.h3_lowpoint])
    near = near[(cell[:, 1] >= w) & (cell[:, 1] <= e) & (cell[:, 0] >= s) & (cell[:, 0] <= n)]
    near = near.merge(sus_table.to_table(filter=ds.field("id").isin(near.id.tolist())).to_pandas(), on="id")
    near = near.sort_values("volume_m3").reset_index(drop=True)
    for j in sorted({0, len(near) // 3, 2 * len(near) // 3, len(near) - 1}):
        d = near.iloc[j]
        lat, lon = h3.cell_to_latlng(d.h3_lowpoint)
        got = run(f"dep_{name}_{j}", snippet("depression", [{"low": d.h3_lowpoint, "lat": lat, "lon": lon, "volume": float(d.volume_m3)}]), "depression")
        rec = {"view": name, "low": d.h3_lowpoint, "id": int(d.id)}
        if "error" in got:
            rec.update(error=got["error"], pass_=False)
        else:
            mine = [g for g in got if g["h3_lowpoint_15"] == d.h3_lowpoint_15]
            rec["rows_in_cell"] = len(got)
            rec["values"] = {c: [mine[0][c] if mine else None, None if d[c] != d[c] else float(d[c])] for c in COLS}
            rec["pass_"] = bool(mine) and all(close(*rec["values"][c]) for c in COLS)
        report["depressions"].append(rec)
        print(name, j, rec["pass_"], flush=True)
for k, v in enumerate(cases["views"]):
    storm = {**v["storm"]}
    got = run(f"storm_{v['name']}_{k}", snippet("storm", [v["box"], storm]), "storm")
    want = v["byStep"][-1] | {"depressions": v["depressions"]}
    rec = {"view": v["name"], "storm": storm, "got": got, "want": {x: want[x] for x in ("depressions", "fill", "overflow", "heldM3")}}
    counts = "error" not in got and all(got[x] == want[x] for x in ("depressions", "fill", "overflow"))
    rec["heldRelDiff"] = None if "error" in got else abs(got["heldM3"] - want["heldM3"]) / want["heldM3"]
    rec["pass_"] = counts and (storm["kind"] == "rarity" or close(got["heldM3"], want["heldM3"]))
    report["storms"].append(rec)
    print(v["name"], storm, rec["pass_"], flush=True)
report["pass"] = all(r["pass_"] for r in report["depressions"] + report["storms"])
(out / "python_check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps({"pass": report["pass"], "depressions": sum(r["pass_"] for r in report["depressions"]),
                  "of": len(report["depressions"]), "storms": sum(r["pass_"] for r in report["storms"]),
                  "of_storms": len(report["storms"])}))
