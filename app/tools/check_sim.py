"""Check the app's JavaScript simulator against the library, number for number (spec 6.1, check 1).

    python -I app/tools/check_sim.py WORK

For each test region, storm depth and ground condition:
  reference  the library's own functions (runoff_mm, adjust_curve_number, the cascade, stage_of)
             on the pack's stored values, with the pack's inflow from other regions
  js         app/src/sim.js run by Node on the same pack
  national   the national cascade in full precision (dataset values, exact inflow from upstream):
             what the pack's float32 storage and interpolated outside inflow cost
"""
import gzip
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
APP = HERE.parent
REGIONS = {"houston": (29.76, -95.37), "potholes": (47.5, -99.0), "new_york": (40.71, -74.0)}
DEPTHS = (25.0, 75.0, 150.0, 300.0)
CONDITIONS = ("dry", "average", "wet")
DTYPES = {"<i8": "<i8", "<u8": "<u8", "<f4": "<f4", "<f8": "<f8", "<i4": "<i4", "<u4": "<u4", "|u1": "u1"}


def read_pack(raw):
    raw = gzip.decompress(raw)
    assert raw[:4] == b"H3LP"
    _, head_len = struct.unpack("<II", raw[4:12])
    header = json.loads(raw[12:12 + head_len])
    body = 12 + head_len
    out = {"header": header}
    for s in header["sections"]:
        dt = np.dtype(DTYPES[s["dtype"]])
        n = int(np.prod(s["shape"])) if s["shape"] else 1
        out[s["name"]] = np.frombuffer(raw, dtype=dt, count=n, offset=body + s["offset"]).reshape(s["shape"])
    return out


def reference(p, depth_mm, condition):
    from h3lowpoints.hydro.cascade import _order, cascade
    from h3lowpoints.runoff import adjust_curve_number, runoff_mm
    from h3lowpoints.storms import stage_of

    cn = adjust_curve_number(p["cn"].astype(float), condition)
    own = runoff_mm(depth_mm, cn) * p["catch"].astype(float) / 1000.0
    own = np.where(np.isnan(cn), np.nan, own)
    c = CONDITIONS.index(condition)
    grid = p["ext_grid"].astype(float)
    for k, i in enumerate(p["ext_dep"]):
        own[i] += np.interp(depth_mm, grid, p["ext_inflow"][k, c].astype(float))
    target = p["target"].astype(np.int64)
    inflow, _ = cascade(_order(target), target, own, volume_of(p))
    store = np.minimum(inflow, volume_of(p))
    depth, area = stage_of(store, p["vols"].astype(float), p["depths"].astype(float), p["areas"].astype(float),
                           p["count"].astype(np.int64))
    cell_wet = area[p["cell_dep"]] * p["cell_share"].astype(float)
    return {"inflow": inflow, "store": store, "depth": depth, "area": area, "cellWet": cell_wet}


def volume_of(p):
    n = len(p["id"])
    return p["vols"].astype(float)[np.arange(n), p["count"].astype(np.int64) - 1]


def run_js(pack_file, depth_mm, condition, out_file):
    subprocess.run(["node", str(HERE / "run_sim.mjs"), str(pack_file), str(depth_mm), condition, str(out_file)],
                   check=True)
    raw = np.fromfile(out_file, dtype="<f8")
    head = raw[:2].astype(int)
    n, m = head
    parts = np.split(raw[2:], np.cumsum([n, n, n, n]))
    return dict(zip(("inflow", "store", "depth", "area", "cellWet"), parts))


def rel_err(a, b):
    """Largest relative difference, NaN pattern required to match."""
    na, nb = np.isnan(a), np.isnan(b)
    if not np.array_equal(na, nb):
        return float("inf")
    a, b = a[~na], b[~nb]
    if not len(a):
        return 0.0
    return float(np.max(np.abs(a - b) / np.maximum(np.abs(b), 1e-9)))


def national_store(work, region_hex, depth_mm, condition, cache={}):
    """Water held by the pack's depressions in a full-precision national run."""
    from h3lowpoints.hydro.cascade import cascade
    from h3lowpoints.runoff import adjust_curve_number, runoff_mm

    if "a" not in cache:
        cache["a"] = dict(np.load(work / "national.npz"))
    a = cache["a"]
    cn = adjust_curve_number(a["cn"], condition)
    own = np.where(np.isnan(cn), np.nan, runoff_mm(depth_mm, cn) * a["catch"] / 1000.0)
    inflow, _ = cascade(a["order"], a["target"], own, a["volume"])
    g = np.nonzero(a["region"] == np.uint64(int(region_hex, 16)))[0]
    return np.minimum(inflow[g], a["volume"][g]), a["ids"][g]


def main(work):
    import h3

    index = json.loads((work / "index.json").read_text())
    report, cases = {}, []
    refdir = work / "browser_ref"
    refdir.mkdir(exist_ok=True)
    with open(work / "packs.bin", "rb") as f, tempfile.TemporaryDirectory() as tmp:
        for name, (lat, lon) in REGIONS.items():
            region = h3.latlng_to_cell(lat, lon, 4)
            offset, length = index["packs"][region][:2]
            f.seek(offset)
            raw = f.read(length)
            pack_file = Path(tmp) / f"{name}.pack"
            pack_file.write_bytes(raw)
            p = read_pack(raw)
            for depth_mm in DEPTHS:
                for cond in CONDITIONS:
                    ref = reference(p, depth_mm, cond)
                    file = f"browser_ref/{name}_{depth_mm:g}_{cond}.bin"
                    np.r_[ref["store"], ref["cellWet"]].astype("<f8").tofile(work / file)
                    cases.append({"name": name, "region": region, "depth_mm": depth_mm, "condition": cond,
                                  "file": file})
                    js = run_js(pack_file, depth_mm, cond, Path(tmp) / "out.bin")
                    nat, nat_ids = national_store(work, region, depth_mm, cond)
                    order = np.argsort(nat_ids)
                    nat = nat[order][np.searchsorted(nat_ids[order], p["id"])]
                    ok = ~np.isnan(nat) & ~np.isnan(ref["store"]) & (nat > 1.0)
                    err_nat = np.abs(ref["store"][ok] - nat[ok]) / nat[ok]
                    report[f"{name}|{depth_mm:g}|{cond}"] = {
                        "depressions": len(p["id"]), "cells": len(p["cell"]),
                        "js_vs_reference": {k: rel_err(js[k], ref[k]) for k in ref},
                        "pack_vs_national_store": {
                            "median": float(np.median(err_nat)) if ok.any() else None,
                            "p99": float(np.percentile(err_nat, 99)) if ok.any() else None,
                            "max": float(err_nat.max()) if ok.any() else None},
                        "unknown": int(np.isnan(ref["store"]).sum()),
                        "filled": int(np.nansum(ref["store"] >= volume_of(p) * (1 - 1e-9))),
                    }
    rarity = []
    rc = work / "rarity_check.json"
    if rc.exists():
        rindex = json.loads((work / "rarity_index.json").read_text())
        ri = rindex["regions"]
        for o in json.loads(rc.read_text())["points_detail"]:
            cell6 = h3.latlng_to_cell(o["lat"], o["lon"], rindex.get("res", 6))
            reg = h3.cell_to_parent(cell6, 4)
            if reg in ri and np.isfinite(o["app"]) and o["app"] > 0:
                rarity.append({"region": reg, "cell6": cell6, "depth_mm": o["depth_mm"], "hours": o["hours"],
                               "app_python": o["app"]})
    (work / "browser_cases.json").write_text(json.dumps({"cases": cases, "rarity": rarity}))
    worst = max(max(v["js_vs_reference"].values()) for v in report.values())
    out = {"worst_js_vs_reference": worst, "pass_1e-6": worst <= 1e-6, "cases": report}
    (work / "check_sim.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"worst_js_vs_reference": worst, "pass": worst <= 1e-6}))
    for k, v in report.items():
        print(k, v["depressions"], v["filled"], v["unknown"], v["pack_vs_national_store"])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
