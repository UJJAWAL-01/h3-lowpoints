"""Data packs for the explorer app: one per H3 resolution-4 region of the national dataset.

    python -I app/tools/packs.py DATASET OUT prep external build

    prep      national arrays: depressions, their shape tables, spill targets, curve numbers
    external  inflow each depression receives from depressions of other regions, for a grid of
              uniform storm depths and the three ground conditions (and its interpolation error)
    build     the packs (one file, packs.bin, plus index.json) and a summary

A pack is gzip of: b"H3LP", uint32 format version (2 adds target_id and target_region), uint32 header length, a JSON header, then the
arrays it lists, each little-endian and starting on a multiple of 8 bytes. See app/src/pack.js.
"""
import gzip
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

FORMAT = 2
# format 2 adds target_id and target_region: where each depression overflows, across pack edges
CONDITIONS = ("dry", "average", "wet")
GRID_MM = np.r_[np.arange(0.0, 100.0, 2.0), np.arange(100.0, 300.0, 5.0), np.arange(300.0, 1000.0 + 1e-9, 10.0)]
# uniform depths (mm) for the external inflow: finer where small storms start upstream overflow
ROWS = 7                                                 # shape-table points (storms.LEVELS + full)


def log(out, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(out / "packs.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def h3_ints(strings):
    return np.array([int(s, 16) for s in strings], dtype=np.uint64)


def parent(cells, res):
    cells = np.asarray(cells, dtype=np.uint64)
    cells = (cells & ~np.uint64(0xF << 52)) | np.uint64(res << 52)
    return cells | np.uint64((1 << (3 * (15 - res))) - 1)


def prep(dataset, out):
    import pyarrow.parquet as pq
    from h3lowpoints.hydro.cascade import spill_order
    from h3lowpoints.storms import LEVEL_COLUMNS, LEVELS, shape_table

    t0 = time.time()
    cols = ["id", "h3_lowpoint", "spill_to", "catchment_area_m2", "volume_m3", "area_m2", "max_depth_m",
            "lat", "lon"] + LEVEL_COLUMNS
    d = pq.read_table(dataset / "depressions.parquet", columns=cols).to_pandas()
    s = pq.read_table(dataset / "susceptibility.parquet",
                      columns=["id", "curve_number", "rain_to_fill_mm", "fills_in_years", "drained",
                               "landlocked"]).to_pandas()
    if not np.array_equal(d["id"].to_numpy(), s["id"].to_numpy()):
        s = s.set_index("id").reindex(d["id"]).reset_index()
    ids = d["id"].to_numpy(np.int64)
    order, target = spill_order(ids, d["spill_to"].to_numpy(np.int64))
    low = h3_ints(d["h3_lowpoint"])
    levels = {name: (d[f"volume_{name}_down_m3"].to_numpy(float), d[f"area_{name}_down_m2"].to_numpy(float))
              for name, _ in LEVELS}
    vols, depths, areas, count = shape_table(d["volume_m3"].to_numpy(float), d["area_m2"].to_numpy(float),
                                             d["max_depth_m"].to_numpy(float), levels)
    np.savez(out / "national.npz", ids=ids, order=order, target=target, low=low, region=parent(low, 4),
             catch=d["catchment_area_m2"].to_numpy(float), volume=d["volume_m3"].to_numpy(float),
             cn=s["curve_number"].to_numpy(float), rain_to_fill=s["rain_to_fill_mm"].to_numpy(float),
             fills_in_years=s["fills_in_years"].to_numpy(float), drained=s["drained"].to_numpy(bool),
             landlocked=s["landlocked"].to_numpy(bool), lat=d["lat"].to_numpy(float),
             lon=d["lon"].to_numpy(float), vols=vols, depths=depths, areas=areas, count=count)
    log(out, f"prep: {len(ids):,} depressions, {int((target >= 0).sum()):,} spill links, "
             f"shape points {np.bincount(count).tolist()}, {time.time() - t0:.0f} s")


def national():
    return dict(np.load(OUT / "national.npz"))


def runoff_m3(depth_mm, cn, catch):
    """Own runoff, m3. Unknown where the curve number is unknown (runoff_mm alone would give 0)."""
    from h3lowpoints.runoff import runoff_mm
    q = runoff_mm(depth_mm, cn) * catch / 1000.0
    return np.where(np.isnan(cn) | np.isnan(catch), np.nan, q)


def external_inflow(n, depth_mm, cn, catch, order, target, volume, cross_from, cross_to):
    """Inflow from other regions at each receiving depression, one column per depth."""
    from h3lowpoints.hydro.cascade import cascade
    own = runoff_m3(np.asarray(depth_mm)[None, :], cn[:, None], catch[:, None])
    inflow, _ = cascade(order, target, own, volume)
    over = inflow[cross_from] - volume[cross_from, None]
    over = np.where(np.isnan(over), np.nan, np.maximum(over, 0.0))
    receivers = np.unique(cross_to)
    pos = np.searchsorted(receivers, cross_to)
    ext = np.zeros((len(receivers), over.shape[1]))
    for k in range(over.shape[1]):
        ext[:, k] = np.bincount(pos, weights=over[:, k], minlength=len(receivers))
    return receivers, ext


def external(out):
    from h3lowpoints.runoff import adjust_curve_number

    t0 = time.time()
    a = national()
    target, region = a["target"], a["region"]
    linked = target >= 0
    cross = linked.copy()
    cross[linked] = region[linked] != region[target[linked]]
    cross_from = np.nonzero(cross)[0]
    cross_to = target[cross_from]
    log(out, f"external: {len(cross_from):,} links cross regions, into {len(np.unique(cross_to)):,} depressions")
    res, errs = {}, {}
    rng = np.random.default_rng(0)

    def run(depths, cn):
        cols = []
        for k in range(0, len(depths), 17):
            rec, e = external_inflow(len(target), depths[k:k + 17], cn, a["catch"], a["order"], target,
                                     a["volume"], cross_from, cross_to)
            cols.append(e)
        return rec, np.concatenate(cols, axis=1)

    for cond in CONDITIONS:
        cn = adjust_curve_number(a["cn"], cond)
        rec, ext = run(GRID_MM, cn)
        res[cond] = ext
        # interpolation error at depths between grid points
        probe = np.sort(np.r_[(GRID_MM[:-1] + GRID_MM[1:]) / 2, rng.uniform(1, 999, 20)])
        _, exact = run(probe, cn)
        approx = np.stack([np.interp(probe, GRID_MM, row) for row in ext])
        with np.errstate(invalid="ignore", divide="ignore"):
            rel = np.abs(approx - exact) / exact
        big = np.isfinite(rel) & (exact > 1.0)
        errs[cond] = {"median": float(np.median(rel[big])), "p95": float(np.percentile(rel[big], 95)),
                      "p99": float(np.percentile(rel[big], 99)), "n": int(big.sum())}
        log(out, f"external: {cond} interpolation error {errs[cond]}")
    np.savez(out / "external.npz", receivers=rec, grid=GRID_MM, **res)
    (out / "external_error.json").write_text(json.dumps(errs, indent=1))
    log(out, f"external: done in {time.time() - t0:.0f} s")


def pack_bytes(header, arrays):
    """The uncompressed pack: magic, version, header length, JSON header, aligned arrays."""
    body, sections, offset = [], [], 0
    for name, arr in arrays:
        arr = np.ascontiguousarray(arr)
        pad = (-offset) % 8
        body.append(b"\0" * pad)
        offset += pad
        sections.append({"name": name, "dtype": arr.dtype.str, "shape": list(arr.shape), "offset": offset})
        b = arr.astype(arr.dtype.newbyteorder("<"), copy=False).tobytes()
        body.append(b)
        offset += len(b)
    head = json.dumps({**header, "sections": sections}, separators=(",", ":")).encode()
    head += b" " * ((-(12 + len(head))) % 8)
    return b"H3LP" + struct.pack("<II", FORMAT, len(head)) + head + b"".join(body)


def build(dataset, out):
    import pyarrow.parquet as pq

    t0 = time.time()
    a = national()
    e = dict(np.load(out / "external.npz"))
    ids, region, target = a["ids"], a["region"], a["target"]
    sort_ids = np.argsort(ids)
    dc = pq.read_table(dataset / "depression_cells.parquet", columns=["id", "h3", "area_m2", "max_depth_m"]).to_pandas()
    dpos = sort_ids[np.searchsorted(ids[sort_ids], dc["id"].to_numpy(np.int64))]
    darea = dc["area_m2"].to_numpy(float)
    share = darea / np.bincount(dpos, weights=darea, minlength=len(ids))[dpos]
    dcell = h3_ints(dc["h3"])
    dmax = dc["max_depth_m"].to_numpy(float)
    del dc
    log(out, f"build: {len(dpos):,} depression x cell rows read")
    regions = np.unique(region)
    by_region = np.argsort(region, kind="stable")
    starts = np.searchsorted(region[by_region], regions)
    ends = np.r_[starts[1:], len(by_region)]
    rows_by = np.argsort(region[dpos], kind="stable")
    rstarts = np.searchsorted(region[dpos][rows_by], regions)
    rends = np.r_[rstarts[1:], len(rows_by)]
    is_rec = np.zeros(len(ids), dtype=bool)
    is_rec[e["receivers"]] = True
    rec_pos = np.full(len(ids), -1)
    rec_pos[e["receivers"]] = np.arange(len(e["receivers"]))
    index, sizes, offset = {}, [], 0
    with open(out / "packs.bin", "wb") as f:
        for r, s0, s1, q0, q1 in zip(regions, starts, ends, rstarts, rends):
            g = by_region[s0:s1]                          # global indices of this pack's depressions
            local = np.full(len(ids), -1, dtype=np.int64)
            local[g] = np.arange(len(g))
            tg = target[g]
            t_local = np.where(tg >= 0, local[np.maximum(tg, 0)], -1)
            t_local = np.where((tg >= 0) & (region[np.maximum(tg, 0)] != r), -1, t_local)
            rows = rows_by[q0:q1]
            rows = rows[np.lexsort((dcell[rows],))]
            rec = g[is_rec[g]]
            ext = np.stack([e[c][rec_pos[rec]] for c in CONDITIONS], axis=1) if len(rec) else np.zeros((0, 3, len(e["grid"])))
            arrays = [
                ("id", ids[g].astype("<i8")), ("low", a["low"][g].astype("<u8")),
                ("catch", a["catch"][g].astype("<f4")), ("cn", a["cn"][g].astype("<f4")),
                ("target", t_local.astype("<i4")), ("count", a["count"][g].astype("u1")),
                ("vols", a["vols"][g].astype("<f4")), ("depths", a["depths"][g].astype("<f4")),
                ("areas", a["areas"][g].astype("<f4")), ("rain_to_fill", a["rain_to_fill"][g].astype("<f4")),
                ("fills_in_years", a["fills_in_years"][g].astype("<f4")),
                ("flags", (a["drained"][g] * 1 + a["landlocked"][g] * 2).astype("u1")),
                ("cell", dcell[rows].astype("<u8")), ("cell_dep", local[dpos[rows]].astype("<i4")),
                ("cell_share", share[rows].astype("<f4")), ("cell_max_depth", dmax[rows].astype("<f4")),
                ("ext_dep", local[rec].astype("<i4")), ("ext_inflow", ext.astype("<f4")),
                ("ext_grid", e["grid"].astype("<f4")),
                # format 2: the global overflow target (dataset id, 0 = leaves the data) and its region
                ("target_id", np.where(tg >= 0, ids[np.maximum(tg, 0)], 0).astype("<i8")),
                ("target_region", np.where(tg >= 0, region[np.maximum(tg, 0)], 0).astype("<u8")),
            ]
            raw = pack_bytes({"region": format(int(r), "x"), "conditions": list(CONDITIONS)}, arrays)
            z = gzip.compress(raw, compresslevel=9, mtime=0)
            f.write(z)
            index[format(int(r), "x")] = [offset, len(z), int(len(g)), int(len(rows))]
            sizes.append(len(z))
            offset += len(z)
    (out / "index.json").write_text(json.dumps({"format": FORMAT, "file": "packs.bin", "packs": index},
                                               separators=(",", ":")))
    sizes = np.array(sizes)
    summary = {"packs": len(sizes), "total_mb": round(sizes.sum() / 1e6, 1),
               "median_kb": round(float(np.median(sizes)) / 1e3, 1),
               "p95_kb": round(float(np.percentile(sizes, 95)) / 1e3, 1),
               "max_kb": round(float(sizes.max()) / 1e3, 1),
               "index_kb": round((out / "index.json").stat().st_size / 1e3, 1),
               "seconds": round(time.time() - t0)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    log(out, f"build: {summary}")


if __name__ == "__main__":
    DATASET, OUT = Path(sys.argv[1]), Path(sys.argv[2])
    OUT.mkdir(parents=True, exist_ok=True)
    for stage in sys.argv[3:]:
        log(OUT, f"--- stage {stage}")
        {"prep": lambda: prep(DATASET, OUT), "external": lambda: external(OUT),
         "build": lambda: build(DATASET, OUT)}[stage]()
