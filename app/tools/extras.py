"""Extras for the explorer app (6.3d2): road features and elevations, one small file beside the packs.

    python -I app/tools/extras.py DATASET PACKS_WORK ROADS_DIR roads build check

roads   For every depression, against Census TIGER 2024 primary and secondary roads (MTFCC S1100
        interstates and freeways, S1200 other major roads; ROADS_DIR/tl_2024_<state>_prisecroads.zip):
          d_low   distance from its lowest point to the nearest major road (m; NaN beyond 100 m)
          angle   between its long axis and that road's direction there (degrees, 0-90)
          road    0 none; 1 lowest point within 30 m of a major road; 2 = 1 and lying along it
                  (length >= 2.5 x width, long axis within 25 degrees of the road)
        Rules fixed before the national run (plans/PHASE6_3d2_SPEC.md). Writes PACKS_WORK/roads.parquet.
build   PACKS_WORK/extras.bin: per pack (same regions and depression order as packs.bin) gzip of
        float32 rim (m above sea level, NAVD 88: the dataset's water_level_m), float32 d_low, uint8 road;
        offsets in extras_index.json. Ground at the lowest point = rim - depth when full.
check   1: rim - depth when full (the pack) = the dataset's ground_m within 1 cm, every depression.
        2: road classes recomputed another way (geopandas, UTM, per box) on 3 boxes = roads.parquet.
"""
import gzip
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NEAR_M, MAX_M, ELONG, ANGLE = 30.0, 100.0, 2.5, 25.0
CLASSES = ("S1100", "S1200")
FORMAT = 1


def log(work, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(work / "extras.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_roads(roads_dir):
    import geopandas as gpd
    import pandas as pd
    parts = []
    for z in sorted(Path(roads_dir).glob("tl_2024_*_prisecroads.zip")):
        g = gpd.read_file(f"zip://{z}", columns=["MTFCC"])
        parts.append(g[g.MTFCC.isin(CLASSES)].to_crs(5070))
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=5070)


def roads(dataset, work, roads_dir):
    import pyarrow.parquet as pq
    import shapely
    from pyproj import Transformer

    r = load_roads(roads_dir)
    log(work, f"roads: {len(r):,} major road lines from {len(list(Path(roads_dir).glob('*.zip')))} state files")
    geoms = r.geometry.values._data if hasattr(r.geometry.values, "_data") else np.asarray(r.geometry.values)
    tree = shapely.STRtree(geoms)
    fwd = Transformer.from_crs(4326, 5070, always_xy=True)
    inv = Transformer.from_crs(5070, 4326, always_xy=True)
    t = pq.read_table(Path(dataset) / "depressions.parquet",
                      columns=["id", "lat", "lon", "length_m", "width_m", "orientation_deg"]).to_pandas()
    n = len(t)
    d_low = np.full(n, np.nan)
    angle = np.full(n, np.nan)
    step = 500_000
    for a in range(0, n, step):
        b = min(a + step, n)
        x, y = fwd.transform(t.lon.values[a:b], t.lat.values[a:b])
        pts = shapely.points(x, y)
        (pi, ri), dist = tree.query_nearest(pts, max_distance=MAX_M, return_distance=True, all_matches=False)
        d_low[a + pi] = dist
        near = dist <= NEAR_M
        pi, ri = pi[near], ri[near]
        lines, p = geoms[ri], pts[pi]
        s = shapely.line_locate_point(lines, p)
        L = shapely.length(lines)
        pa = shapely.line_interpolate_point(lines, np.maximum(s - 15, 0))
        pb = shapely.line_interpolate_point(lines, np.minimum(s + 15, L))
        lo1, la1 = inv.transform(shapely.get_x(pa), shapely.get_y(pa))
        lo2, la2 = inv.transform(shapely.get_x(pb), shapely.get_y(pb))
        bearing = np.degrees(np.arctan2((lo2 - lo1) * np.cos(np.radians((la1 + la2) / 2)), la2 - la1)) % 180
        diff = np.abs(bearing - t.orientation_deg.values[a + pi]) % 180
        angle[a + pi] = np.minimum(diff, 180 - diff)
        log(work, f"roads: {b:,}/{n:,} depressions")
    elong = t.length_m.values / np.maximum(t.width_m.values, 1)
    road = np.zeros(n, np.uint8)
    on = d_low <= NEAR_M
    road[on] = 1
    road[on & (elong >= ELONG) & (angle <= ANGLE)] = 2
    import pandas as pd
    out = pd.DataFrame({"id": t.id.values, "d_low": d_low.astype("float32"), "angle": angle.astype("float32"), "road": road})
    out.to_parquet(work / "roads.parquet", index=False)
    log(work, f"roads: class 1 {int((road == 1).sum()):,}, class 2 {int((road == 2).sum()):,} of {n:,}")


def build(dataset, work):
    import pandas as pd
    import pyarrow.parquet as pq
    from check_sim import read_pack

    dep = pq.read_table(Path(dataset) / "depressions.parquet", columns=["id", "water_level_m", "ground_m"]).to_pandas()
    rd = pd.read_parquet(work / "roads.parquet")
    dep = dep.merge(rd, on="id", how="left").sort_values("id")
    ids = dep.id.values
    index = json.loads((work / "index.json").read_text())
    regions, offset, worst, bad, total = {}, 0, 0.0, 0, 0
    with open(work / "packs.bin", "rb") as f, open(work / "extras.bin", "wb") as out:
        for region, entry in index["packs"].items():
            f.seek(entry[0])
            p = read_pack(f.read(entry[1]))
            k = np.searchsorted(ids, p["id"])
            assert np.array_equal(ids[k], p["id"]), region
            rim = dep.water_level_m.values[k].astype("<f4")
            full = p["depths"][np.arange(len(k)), p["count"].astype(np.int64) - 1].astype(float)
            err = np.abs(rim.astype(float) - full - dep.ground_m.values[k])     # check 1
            worst = max(worst, float(np.nanmax(err)) if len(err) else 0.0)
            bad += int((err > 0.01).sum())
            total += len(k)
            raw = rim.tobytes() + dep.d_low.values[k].astype("<f4").tobytes() + dep.road.values[k].astype("u1").tobytes()
            gz = gzip.compress(raw, 9)
            out.write(gz)
            regions[region] = [offset, len(gz), int(len(k))]
            offset += len(gz)
    (work / "extras_index.json").write_text(json.dumps({
        "file": "extras.bin", "format": FORMAT,
        "fields": ["rim_m float32 (NAVD 88, = dataset water_level_m)", "road_m float32 (NaN beyond 100 m)", "road uint8"],
        "roads": "US Census TIGER 2024 primary and secondary roads (MTFCC S1100, S1200)",
        "rules": {"near_m": NEAR_M, "elong": ELONG, "angle_deg": ANGLE}, "regions": regions}, separators=(",", ":")))
    rep = {"depressions": total, "check1_worst_m": worst, "check1_over_1cm": bad, "check1_pass": bad == 0,
           "extras_mb": round(offset / 1e6, 1)}
    (work / "extras_check1.json").write_text(json.dumps(rep, indent=1))
    log(work, f"build: {json.dumps(rep)}")


BOXES = {"austin": ((-97.85, 30.15, -97.65, 30.40), 32614), "houston": ((-95.50, 29.65, -95.25, 29.85), 32615),
         "minneapolis": ((-93.35, 44.90, -93.15, 45.05), 32615)}


def check(dataset, work, roads_dir):
    """Check 2: per box, with geopandas in UTM (as the prototype), classes and distances."""
    import geopandas as gpd
    import pandas as pd
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from shapely.geometry import Point

    r_all = load_roads(roads_dir).to_crs(4326)
    rd = pd.read_parquet(work / "roads.parquet")
    rep = {}
    for name, (box, epsg) in BOXES.items():
        w, s, e, n = box
        t = pq.read_table(Path(dataset) / "depressions.parquet", columns=["id", "lat", "lon", "length_m", "width_m", "orientation_deg"])
        m = pc.and_(pc.and_(pc.greater_equal(t["lat"], s), pc.less_equal(t["lat"], n)),
                    pc.and_(pc.greater_equal(t["lon"], w), pc.less_equal(t["lon"], e)))
        d = t.filter(m).to_pandas().merge(rd, on="id")
        roads = r_all.cx[w - 0.02:e + 0.02, s - 0.02:n + 0.02].to_crs(epsg)
        pts = gpd.GeoSeries([Point(x, y) for x, y in zip(d.lon, d.lat)], crs=4326).to_crs(epsg)
        idx, dist = roads.sindex.nearest(pts, return_distance=True, return_all=False)
        ang = []
        for p, j in zip(pts, idx[1]):
            line = roads.geometry.iloc[j]
            u = line.project(p)
            a, b = line.interpolate(max(u - 15, 0)), line.interpolate(min(u + 15, line.length))
            ang.append(np.degrees(np.arctan2(b.x - a.x, b.y - a.y)) % 180)
        diff = np.abs(np.array(ang) - d.orientation_deg.values) % 180
        angle = np.minimum(diff, 180 - diff)
        elong = d.length_m.values / np.maximum(d.width_m.values, 1)
        cls = np.where(dist <= NEAR_M, np.where((elong >= ELONG) & (angle <= ANGLE), 2, 1), 0)
        # cases within a hair of a threshold may differ between the two map projections
        edge = (np.abs(dist - NEAR_M) < 0.5) | ((dist <= NEAR_M) & (np.abs(angle - ANGLE) < 1.0))
        diffc = (cls != d.road.values) & ~edge
        near = dist <= 40                    # where a class is decided (projections differ ~1% in scale)
        rep[name] = {"depressions": len(d), "class_mismatch": int(diffc.sum()), "at_thresholds": int(edge.sum()),
                     "distance_worst_m": float(np.nanmax(np.abs(dist[near] - d.d_low.values[near]))) if near.any() else 0.0,
                     "classes": {str(c): int((d.road.values == c).sum()) for c in (0, 1, 2)}}
    rep_all = {"boxes": rep, "pass": all(v["class_mismatch"] == 0 and v["distance_worst_m"] < 0.5 for v in rep.values())}
    (work / "extras_check2.json").write_text(json.dumps(rep_all, indent=1))
    log(work, f"check: {json.dumps(rep_all)}")


if __name__ == "__main__":
    dataset, work, roads_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    for stage in sys.argv[4:]:
        log(work, f"--- stage {stage}")
        {"roads": lambda: roads(dataset, work, roads_dir), "build": lambda: build(dataset, work),
         "check": lambda: check(dataset, work, roads_dir)}[stage]()
