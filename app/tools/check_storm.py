"""Check the app's storm engine (app/src/storm.js) against the library on every step (spec 6.3, check 1).

    python -I app/tools/check_storm.py PACKS_WORK RARITY_WORK

For 3 test regions x 3 storms x 3 ground conditions, all 16 steps:
  reference  the library's functions (adjust_curve_number, runoff_mm, the cascade, stage_of, and for
             rarity storms log_interpolate between durations on the same Atlas 14 tables), with the
             pack's inflow from other regions at each depression's own rain; hexagons grouped and
             their water computed here independently of app/src/levels.js
  js         storm.js run by Node on the same pack and rarity block (app/tools/run_storm.mjs)
Pass: depression values within 1e-6 relative; hexagon water within 1e-6 absolute (m or share; the
app keeps steps in float32); fill and overflow steps equal. Writes PACKS_WORK/check_storm.json.
"""
import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_sim import CONDITIONS, REGIONS, read_pack, rel_err, volume_of  # noqa: E402

STEPS = 16
HOURS = (1.0, 2.0, 3.0, 6.0, 12.0, 24.0, 48.0)
YEARS = (1, 2, 5, 10, 25, 50, 100, 200, 500, 1000)
STORMS = ({"kind": "amount", "mm": 127.0, "hours": 6},          # 5 in in 6 h
          {"kind": "rarity", "years": 100, "hours": 24},
          {"kind": "rarity", "years": 25, "hours": 5})          # between published durations


def rarity_block(raw, n):
    raw = gzip.decompress(raw)
    cells = np.frombuffer(raw, "<u8", n)
    return cells, np.frombuffer(raw, "<f4", n * 70, offset=8 * n).reshape(n, 7, 10)


def reference_rain(p, storm, block, res):
    from h3.api import basic_int as h3i
    from h3lowpoints.sources.rainfall import log_interpolate

    n = len(p["id"])
    if storm["kind"] == "amount":
        return np.full(n, storm["mm"])
    cells, tables = block
    yi = YEARS.index(storm["years"])
    h = float(storm["hours"])
    hi = int(np.searchsorted(HOURS, h))
    rain = np.full(n, np.nan)
    for i, low in enumerate(p["low"]):
        c = h3i.cell_to_parent(int(low), res)
        k = int(np.searchsorted(cells, np.uint64(c)))
        if k >= len(cells) or cells[k] != np.uint64(c):
            continue
        t = tables[k].astype(float)
        if HOURS[hi] == h:
            rain[i] = t[hi, yi]
        else:
            w = (np.log(h) - np.log(HOURS[hi - 1])) / (np.log(HOURS[hi]) - np.log(HOURS[hi - 1]))
            rain[i] = float(log_interpolate(t[hi - 1, yi], t[hi, yi], w))
    return rain


def reference_steps(p, rain, condition):
    from h3lowpoints.hydro.cascade import _order, cascade
    from h3lowpoints.runoff import adjust_curve_number, runoff_mm
    from h3lowpoints.storms import stage_of

    cn = adjust_curve_number(p["cn"].astype(float), condition)
    c = CONDITIONS.index(condition)
    grid = p["ext_grid"].astype(float)
    target = p["target"].astype(np.int64)
    order = _order(target)
    vol = volume_of(p)
    out = {k: [] for k in ("store", "depth", "area", "inflow")}
    for s in range(STEPS):
        r = rain * ((s + 1) / STEPS)
        own = np.where(np.isnan(cn) | np.isnan(r), np.nan, runoff_mm(r, cn) * p["catch"].astype(float) / 1000.0)
        for k, i in enumerate(p["ext_dep"]):
            own[i] += np.interp(r[i], grid, p["ext_inflow"][k, c].astype(float))
        inflow, _ = cascade(order, target, own, vol)
        store = np.minimum(inflow, vol)
        depth, area = stage_of(store, p["vols"].astype(float), p["depths"].astype(float), p["areas"].astype(float),
                               p["count"].astype(np.int64))
        for k, v in (("store", store), ("depth", depth), ("area", area), ("inflow", inflow)):
            out[k].append(v)
    out = {k: np.array(v) for k, v in out.items()}
    with np.errstate(invalid="ignore"):
        filled = out["store"] >= vol * (1 - 1e-9)
        spilled = out["inflow"] > vol
    out["fillStep"] = np.where(filled.any(0), filled.argmax(0), -1)
    out["spillStep"] = np.where(spilled.any(0), spilled.argmax(0), -1)
    return out


def reference_levels(p, ref, level):
    """Hexagons in first-seen order; water at level 10 = deepest water, else share under water."""
    from h3.api import basic_int as h3i

    rows = p["cell"].astype(np.uint64)
    cells = rows if level == 10 else np.array([h3i.cell_to_parent(int(c), level) for c in rows], dtype=np.uint64)
    _, first, inv = np.unique(cells, return_index=True, return_inverse=True)
    rank = np.argsort(np.argsort(first))           # first-seen order
    k_of_row = rank[inv]
    K = len(first)
    dep = p["cell_dep"].astype(np.int64)
    n = len(p["id"])
    full_depth = p["depths"].astype(float)[np.arange(n), p["count"].astype(np.int64) - 1]
    unknown = np.zeros(K, bool)
    np.logical_or.at(unknown, k_of_row, np.isnan(ref["store"][-1, dep]))
    out = np.zeros((K, STEPS))
    if level == 10:
        floor = full_depth[dep] - p["cell_max_depth"].astype(float)
        w = ref["depth"][:, dep] - floor                 # steps x rows
        for s in range(STEPS):
            np.maximum.at(out[:, s], k_of_row, np.where(np.isnan(w[s]), 0, w[s]))
    else:
        wet = ref["area"][:, dep] * p["cell_share"].astype(float)
        for s in range(STEPS):
            np.add.at(out[:, s], k_of_row, np.where(np.isnan(wet[s]), 0, wet[s]))
        hexes = cells[first[np.argsort(rank)]]
        out /= np.array([h3i.cell_area(int(c), "m^2") for c in hexes])[:, None]
    out[unknown] = np.nan
    return out


def run_js(pack_file, rarity_file, n_cells, res, storm, condition, out_file):
    subprocess.run(["node", str(HERE / "run_storm.mjs"), str(pack_file), str(rarity_file), str(n_cells), str(res),
                    json.dumps({**storm, "condition": condition}), str(out_file)], check=True, cwd=HERE.parent)
    a = np.fromfile(out_file, "<f8")
    n, k8, k9, k10 = a[:4].astype(int)
    sizes = [n, STEPS * n, STEPS * n, STEPS * n, n, n, STEPS * k8, STEPS * k9, STEPS * k10, 1]
    parts = np.split(a[4:], np.cumsum(sizes)[:-1])
    keys = ("rain", "store", "depth", "area", "fillStep", "spillStep", "w8", "w9", "w10", "ms")
    js = dict(zip(keys, parts))
    for k in ("store", "depth", "area"):
        js[k] = js[k].reshape(STEPS, n)
    for lv, K in ((8, k8), (9, k9), (10, k10)):
        js[f"w{lv}"] = js[f"w{lv}"].reshape(K, STEPS)
    return js


def abs_err(a, b):
    na, nb = np.isnan(a), np.isnan(b)
    if not np.array_equal(na, nb):
        return float("inf")
    return float(np.max(np.abs(a[~na] - b[~nb]))) if (~na).any() else 0.0


def main(work, rwork):
    import h3

    index = json.loads((work / "index.json").read_text())
    rindex = json.loads((rwork / "rarity_index.json").read_text())
    res = rindex.get("res", 7)
    report = {}
    with open(work / "packs.bin", "rb") as f, open(rwork / "rarity.bin", "rb") as rf, \
            tempfile.TemporaryDirectory() as tmp:
        for name, (lat, lon) in REGIONS.items():
            region = h3.latlng_to_cell(lat, lon, 4)
            off, length = index["packs"][region][:2]
            f.seek(off)
            raw = f.read(length)
            pack_file = Path(tmp) / "p.gz"
            pack_file.write_bytes(raw)
            p = read_pack(raw)
            roff, rlen, rn = rindex["regions"][region]
            rf.seek(roff)
            rraw = rf.read(rlen)
            rarity_file = Path(tmp) / "r.gz"
            rarity_file.write_bytes(rraw)
            block = rarity_block(rraw, rn)
            for storm in STORMS:
                rain = reference_rain(p, storm, block, res)
                for cond in CONDITIONS:
                    ref = reference_steps(p, rain, cond)
                    js = run_js(pack_file, rarity_file, rn, res, storm, cond, Path(tmp) / "o.bin")
                    rec = {
                        "depressions": len(p["id"]), "rain_rel": rel_err(js["rain"], rain),
                        **{k: rel_err(js[k].ravel(), ref[k].ravel()) for k in ("store", "depth", "area")},
                        "fill_step_mismatch": int((js["fillStep"] != ref["fillStep"]).sum()),
                        "spill_step_mismatch": int((js["spillStep"] != ref["spillStep"]).sum()),
                        "filled_at_end": int((ref["fillStep"] >= 0).sum()),
                        "unknown": int(np.isnan(ref["store"][-1]).sum()),
                        "node_ms_16_steps": round(float(js["ms"][0]), 1),
                    }
                    for lv in (8, 9, 10):
                        r = reference_levels(p, ref, lv)
                        rec[f"hexagons_{lv}"] = r.shape[0]
                        rec[f"water_{lv}_abs"] = abs_err(js[f"w{lv}"], r) if js[f"w{lv}"].shape == r.shape else "shape"
                    key = f"{name}|{storm.get('mm', storm.get('years'))}{'mm' if storm['kind'] == 'amount' else 'yr'}" \
                          f"{storm['hours']}h|{cond}"
                    report[key] = rec
                    print(key, json.dumps(rec), flush=True)
    worst_rel = max(max(v[k] for k in ("rain_rel", "store", "depth", "area")) for v in report.values())
    worst_abs = max(max(v[f"water_{lv}_abs"] for lv in (8, 9, 10)) for v in report.values())
    steps_ok = all(v["fill_step_mismatch"] == 0 and v["spill_step_mismatch"] == 0 for v in report.values())
    out = {"worst_relative": worst_rel, "worst_hexagon_abs": worst_abs, "steps_equal": steps_ok,
           "pass": worst_rel <= 1e-6 and worst_abs <= 1e-6 and steps_ok, "cases": report}
    (work / "check_storm.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "cases"}))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
