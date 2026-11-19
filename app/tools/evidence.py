"""Evidence for the explorer app (7.7c): observed water, radar floods and the confidence grade of
dataset 0.4, beside the packs, and per-hexagon counts for the national overview.

    python -I app/tools/evidence.py DATASET04 V03_DEPRESSIONS PACKS_WORK APP_DATA build check

build   PACKS_WORK/evidence.bin: per pack (same regions and depression order as packs.bin) a gzip of
          uint8 x 10 per depression: water class, wet years, clear years (255 unknown), first and
            last wet year (year - 1983; 0 none), wettest month, radar scenes observed and flooded
            (capped at 255), grade (0 none, 1-4 = A-D), flags (bit 0 road_dam, bit 1 bridge,
            bits 2-3 base grade 1-3)
          uint16 breached depth in cm (65535 none)
          uint32 m, then m x uint32 depression index and m x uint16 flood day (days since
            2015-01-01), one per depression and distinct day it was flooded
        offsets in evidence_index.json. APP_DATA/overview_evidence_<res>.bin (res 4, 5, 6): for the
        cells of overview_<res>.bin, in their order, uint32 per cell: depressions with evidence,
        seen wet, observed by radar, flooded, grades A, B, C, D, road dams.
check   Every depression decoded back equals dataset 0.4 (water class, years, months, radar counts,
        flood days, grade, flags, breached depth); the overview counts add up to the table totals.
Water classes: 0 no evidence, 1 too_small, 2 too_few_views, 3 never_seen_wet, 4 some_years,
5 seasonal, 6 permanent.
"""
import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FORMAT = 1
CLASSES = ["", "too_small", "too_few_views", "never_seen_wet", "some_years", "seasonal", "permanent"]
GRADES = ["", "A", "B", "C", "D"]
DAY0 = np.datetime64("2015-01-01")
RES = (4, 5, 6)
FIELDS = ["wclass", "wet", "clear", "first", "last", "month", "obs", "fl", "grade", "flags"]


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def per_depression(ds, v03):
    """One row per dataset 0.3 depression (by id), with the encoded evidence columns."""
    ids = pd.read_parquet(v03, columns=["id", "h3_lowpoint"]).sort_values("id").reset_index(drop=True)
    w = pd.read_parquet(ds / "water_history.parquet",
                        columns=["id", "water_class", "wet_years", "clear_years", "first_wet_year",
                                 "last_wet_year", "wettest_month"])
    f = pd.read_parquet(ds / "flood_events.parquet", columns=["id", "scenes_observed", "scenes_flooded"])
    g = pd.read_parquet(ds / "confidence.parquet",
                        columns=["id", "grade", "base_grade", "road_dam", "bridge_crossing", "breached_depth_m"])
    t = ids.merge(w, on="id", how="left").merge(f, on="id", how="left").merge(g, on="id", how="left")
    out = pd.DataFrame({"id": t.id, "h3_lowpoint": t.h3_lowpoint})
    out["wclass"] = t.water_class.map({c: k for k, c in enumerate(CLASSES)}).fillna(0).astype("u1")
    out["wet"] = t.wet_years.fillna(0).clip(0, 254).astype("u1")
    out["clear"] = np.where(t.clear_years.isna(), 255, t.clear_years.fillna(0).clip(0, 254)).astype("u1")
    yr = lambda s: np.where(s.fillna(-1) > 0, s.fillna(0) - 1983, 0).astype("u1")
    out["first"], out["last"] = yr(t.first_wet_year), yr(t.last_wet_year)
    out["month"] = t.wettest_month.fillna(0).astype("u1")
    out["obs"] = t.scenes_observed.fillna(0).clip(0, 255).astype("u1")
    out["fl"] = t.scenes_flooded.fillna(0).clip(0, 255).astype("u1")
    out["grade"] = t.grade.map({c: k for k, c in enumerate(GRADES)}).fillna(0).astype("u1")
    base = t.base_grade.map({"A": 1, "B": 2, "C": 3}).fillna(0).to_numpy(int)
    road = t.road_dam.fillna(False).to_numpy(bool).astype(int)
    bridge = t.bridge_crossing.fillna(False).to_numpy(bool).astype(int)
    out["flags"] = (road | (bridge << 1) | (base << 2)).astype("u1")
    out["breach_cm"] = np.where(t.breached_depth_m.isna(), 65535,
                                np.round(t.breached_depth_m.fillna(0) * 100).clip(0, 65534)).astype("<u2")
    return out


def flood_days(ds):
    s = pd.read_parquet(ds / "flood_seen.parquet", columns=["id", "datetime", "flooded"])
    s = s[s.flooded]
    day = (pd.to_datetime(s.datetime, utc=True).dt.tz_localize(None).values.astype("datetime64[D]") - DAY0
           ).astype(int)
    return pd.DataFrame({"id": s.id.values, "day": day}).drop_duplicates().sort_values(["id", "day"])


def build(ds, v03, work, app_data):
    from check_sim import read_pack

    t = per_depression(ds, v03)
    days = flood_days(ds)
    ids = t.id.to_numpy()
    index = json.loads((work / "index.json").read_text())
    regions, offset, total = {}, 0, 0
    with open(work / "packs.bin", "rb") as f, open(work / "evidence.bin", "wb") as out:
        for region, entry in index["packs"].items():
            f.seek(entry[0])
            p = read_pack(f.read(entry[1]))
            k = np.searchsorted(ids, p["id"])
            assert np.array_equal(ids[k], p["id"]), region
            raw = b"".join(t[c].to_numpy()[k].astype("u1").tobytes() for c in FIELDS)
            raw += t.breach_cm.to_numpy()[k].astype("<u2").tobytes()
            d = days[days.id.isin(p["id"])]
            pos = pd.Series(np.arange(len(k)), index=p["id"]).reindex(d.id).to_numpy()
            raw += np.uint32(len(d)).tobytes() + pos.astype("<u4").tobytes() + d.day.to_numpy().astype("<u2").tobytes()
            gz = gzip.compress(raw, 9, mtime=0)
            out.write(gz)
            regions[region] = [offset, len(gz), int(len(k))]
            offset += len(gz)
            total += len(k)
    (work / "evidence_index.json").write_text(json.dumps({
        "file": "evidence.bin", "format": FORMAT, "dataset": "0.4",
        "fields": FIELDS + ["breach_cm uint16"], "classes": CLASSES, "grades": GRADES,
        "day0": "2015-01-01", "regions": regions}, separators=(",", ":")))
    log(f"evidence.bin: {total:,} depressions in {len(regions)} packs, {offset / 1e6:.1f} MB")

    # overview counts, in the order of the app's overview files
    from overview import parent
    low = np.array([int(s, 16) for s in t.h3_lowpoint], dtype=np.uint64)
    flags = {"has": t.wclass > 0, "wet": t.wclass >= 4, "obs": t.obs > 0, "fl": t.fl > 0,
             "A": t.grade == 1, "B": t.grade == 2, "C": t.grade == 3, "D": t.grade == 4,
             "road": (t["flags"] & 1) > 0}
    rep = {}
    for res in RES:
        raw = gzip.decompress((app_data / f"overview_{res}.bin").read_bytes())
        n = len(raw) // 32
        cells = np.frombuffer(raw, dtype="<u8", count=n)
        pos = np.searchsorted(cells, parent(low, res))
        ok = (pos < n) & (cells[np.minimum(pos, n - 1)] == parent(low, res))
        cols = [np.bincount(pos[ok], weights=v.to_numpy()[ok].astype(float), minlength=n) for v in flags.values()]
        z = gzip.compress(np.stack(cols).astype("<u4").tobytes(), 9, mtime=0)
        (app_data / f"overview_evidence_{res}.bin").write_bytes(z)
        rep[res] = {"cells": n, "kb": round(len(z) / 1e3, 1), "placed": int(ok.sum())}
    log(f"overview evidence: {rep}")


def check(ds, v03, work, app_data):
    from check_sim import read_pack

    t = per_depression(ds, v03).set_index("id")
    days = flood_days(ds)
    ei = json.loads((work / "evidence_index.json").read_text())
    index = json.loads((work / "index.json").read_text())
    got, got_days, bad_regions = [], [], []
    with open(work / "packs.bin", "rb") as f, open(work / "evidence.bin", "rb") as e:
        for region, entry in index["packs"].items():
            f.seek(entry[0])
            p = read_pack(f.read(entry[1]))
            off, ln, n = ei["regions"][region]
            if n != len(p["id"]):
                bad_regions.append(region)
                continue
            e.seek(off)
            raw = gzip.decompress(e.read(ln))
            cols = {c: np.frombuffer(raw, "u1", n, i * n) for i, c in enumerate(FIELDS)}
            cols["breach_cm"] = np.frombuffer(raw, "<u2", n, 10 * n)
            o = 12 * n
            m = int(np.frombuffer(raw, "<u4", 1, o)[0])
            dep = np.frombuffer(raw, "<u4", m, o + 4)
            day = np.frombuffer(raw, "<u2", m, o + 4 + 4 * m)
            got.append(pd.DataFrame({"id": p["id"], **cols}))
            got_days.append(pd.DataFrame({"id": p["id"][dep], "day": day.astype(int)}))
    g = pd.concat(got).set_index("id").sort_index()
    ref = t.loc[g.index, FIELDS + ["breach_cm"]]
    differ = {c: int((g[c].to_numpy() != ref[c].to_numpy()).sum()) for c in ref.columns}
    gd = pd.concat(got_days).sort_values(["id", "day"]).reset_index(drop=True)
    rd = days[days.id.isin(g.index)].reset_index(drop=True)
    # the decoded values against the source tables directly (not only against the encoder)
    w = pd.read_parquet(ds / "water_history.parquet", columns=["id", "water_class", "wet_years", "first_wet_year"]).set_index("id")
    gr = pd.read_parquet(ds / "confidence.parquet", columns=["id", "grade", "road_dam"]).set_index("id")
    fe = pd.read_parquet(ds / "flood_events.parquet", columns=["id", "scenes_flooded"]).set_index("id")
    have = g.index.intersection(w.index)
    direct = {
        "water_class": bool((np.array(CLASSES, dtype=object)[g.loc[have, "wclass"]] == w.loc[have, "water_class"].to_numpy()).all()),
        "wet_years": bool((g.loc[have, "wet"].to_numpy() == np.minimum(w.loc[have, "wet_years"].to_numpy(), 254)).all()),
        "first_wet_year": bool((np.where(g.loc[have, "first"] > 0, g.loc[have, "first"].astype(int) + 1983, -1)
                                == w.loc[have, "first_wet_year"].to_numpy()).all()),
        "grade": bool((np.array(GRADES, dtype=object)[g.loc[have, "grade"]] == gr.loc[have, "grade"].to_numpy()).all()),
        "road_dam": bool((((g.loc[have, "flags"] & 1) > 0).to_numpy() == gr.loc[have, "road_dam"].to_numpy(bool)).all()),
        "scenes_flooded": bool((g.loc[have, "fl"].to_numpy() == np.minimum(fe.loc[have, "scenes_flooded"].to_numpy(), 255)).all()),
        "no_evidence_rows_empty": bool((g.loc[g.index.difference(w.index), ["wclass", "grade", "obs", "fl"]] == 0).all().all()),
    }
    totals = {"has": int((g.wclass > 0).sum()), "wet": int((g.wclass >= 4).sum()), "obs": int((g.obs > 0).sum()),
              "fl": int((g.fl > 0).sum()), "A": int((g.grade == 1).sum()), "B": int((g.grade == 2).sum()),
              "C": int((g.grade == 3).sum()), "D": int((g.grade == 4).sum()), "road": int(((g["flags"] & 1) > 0).sum())}
    over = {}
    for res in RES:
        raw = gzip.decompress((app_data / f"overview_evidence_{res}.bin").read_bytes())
        n = len(gzip.decompress((app_data / f"overview_{res}.bin").read_bytes())) // 32
        a = np.frombuffer(raw, "<u4").reshape(9, n)
        over[res] = {k: int(a[i].sum()) for i, k in enumerate(totals)}
    rep = {"depressions": len(g), "all_packs_matched": not bad_regions, "fields_differing": differ,
           "flood_days_equal": bool(gd.equals(rd)), "flood_day_pairs": len(gd), "direct": direct,
           "totals": totals, "overview_sums": over}
    rep["pass"] = (not bad_regions and not any(differ.values()) and rep["flood_days_equal"]
                   and all(direct.values()) and len(g) == len(t))
    (work / "evidence_check.json").write_text(json.dumps(rep, indent=1))
    log(json.dumps(rep))
    return rep["pass"]


if __name__ == "__main__":
    ds, v03, work, app_data = (Path(a) for a in sys.argv[1:5])
    stages = sys.argv[5:]
    if "build" in stages:
        build(ds, v03, work, app_data)
    if "check" in stages:
        sys.exit(0 if check(ds, v03, work, app_data) else 1)
