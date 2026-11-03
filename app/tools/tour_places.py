"""Places for the tours and the flood-report files, by the rules fixed in plans/PHASE6_4e_SPEC.md.

    python -I app/tools/tour_places.py NATIONAL_DIR ROADS_PARQUET CITY_LIMITS_GPKG REPORTS_DIR OUT_JSON APP_DATA_DIR

For each tour city, inside its 2024 city limits (Census places), dataset 0.3:
  depression  landlocked, not on a major road (road class 0), at least 1 m deep, fills in the 2-year 24-hour
              storm (fills_in_years <= 2, counting upstream water), overflows into at least 2 more
              depressions; of those, the most reports of the city's storm day within 300 m of its lowest
              point; ties to the largest volume. If none qualifies the rules are relaxed in this order
              (and recorded): reports within 300 m, then 2 overflow steps, then 1 m deep.
  road        the class 2 depression (road cut or underpass) nearest the chosen one.
  view        the 2 km box centred on the chosen depression's lowest point.
Reports: the city's 311 requests in the street-flooding categories of the studies on the storm day,
repeats at one place on one day counted once (coordinates rounded to 5 decimals), inside the limits;
written to APP_DATA_DIR/reports_<city>.json with their source, version and changes.
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import shapely

nat, roads_path, limits_path, reports_dir, out_json, app_data = (Path(x) for x in sys.argv[1:7])
CITIES = {
    "nyc": {"limits": "nyc", "reports": "nyc.csv", "day": "2023-09-29", "name": "New York City",
            "source": "NYC Open Data, 311 Service Requests from 2020 to Present (erm2-nwe9)",
            "credit": "NYC Open Data (data.cityofnewyork.us), 311 Service Requests (erm2-nwe9)"},
    "austin": {"limits": "aus", "reports": "austin.csv", "day": "2015-10-30", "name": "Austin",
               "source": "City of Austin, Austin 311 Public Data (xwdj-i9he)",
               "credit": "City of Austin, Texas - data.austintexas.gov"},
}
DOWNLOADED = "2026-10-28"           # phase5/data/fetch_reports.log
R_EARTH = 6371008.8


def metres(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(a))


limits = gpd.read_file(limits_path).to_crs(4326)
roads = ds.dataset(roads_path)
out = {"rules": __doc__.split("Reports:")[0].strip(), "cities": {}}
for key, c in CITIES.items():
    poly = limits[limits.city == c["limits"]].geometry.union_all()
    w, s, e, n = poly.bounds
    dep = ds.dataset(nat / "depressions.parquet").to_table(
        filter=(ds.field("lon") >= w) & (ds.field("lon") <= e) & (ds.field("lat") >= s) & (ds.field("lat") <= n),
        columns=["id", "lat", "lon", "h3_lowpoint", "max_depth_m", "area_m2", "volume_m3", "spill_to"]).to_pandas()
    dep = dep[shapely.contains_xy(poly, dep.lon.to_numpy(), dep.lat.to_numpy())]
    sus = ds.dataset(nat / "susceptibility.parquet").to_table(
        filter=ds.field("id").isin(dep.id.tolist()), columns=["id", "fills_in_years", "landlocked", "drained"]).to_pandas()
    rd = roads.to_table(filter=ds.field("id").isin(dep.id.tolist()), columns=["id", "road"]).to_pandas()
    dep = dep.merge(sus, on="id").merge(rd, on="id")
    # overflow steps into other depressions: follow spill_to through the national table
    spill = dict(zip(dep.id, dep.spill_to))
    missing = set(int(x) for x in dep.spill_to if x > 0) - set(spill)
    while missing:
        more = ds.dataset(nat / "depressions.parquet").to_table(filter=ds.field("id").isin(sorted(missing)),
                                                                 columns=["id", "spill_to"]).to_pandas()
        spill.update(zip(more.id, more.spill_to))
        missing = set(int(x) for x in more.spill_to if x > 0) - set(spill)

    def steps(i, cap=50):
        k, j = 0, spill.get(i, 0)             # spill_to 0: the water leaves the mapped land
        while j is not None and j > 0 and k < cap:
            k += 1
            j = spill.get(int(j), 0)
        return k
    dep["steps"] = [steps(i) for i in dep.id]
    # reports of the storm day
    r = pd.read_csv(reports_dir / c["reports"])
    r = r[r.created.str[:10] == c["day"]].dropna(subset=["lat", "lon"])
    r = r.assign(lat=r.lat.round(5), lon=r.lon.round(5)).drop_duplicates(["lat", "lon"])
    r = r[shapely.contains_xy(poly, r.lon.to_numpy(), r.lat.to_numpy())]
    near = np.array([(metres(a, b, r.lat.to_numpy(), r.lon.to_numpy()) <= 300).sum() for a, b in zip(dep.lat, dep.lon)])
    dep["reports_300m"] = near
    relaxed, rule = [], lambda d: (d.landlocked == True) & (d.road == 0) & (d.max_depth_m >= 1) & (d.fills_in_years <= 2) & (d.steps >= 2)  # noqa: E712
    pick = dep[rule(dep) & (dep.reports_300m > 0)]
    if pick.empty:
        relaxed.append("reports within 300 m"); pick = dep[rule(dep)]
    if pick.empty:
        relaxed.append("2 overflow steps"); pick = dep[(dep.landlocked == True) & (dep.road == 0) & (dep.max_depth_m >= 1) & (dep.fills_in_years <= 2)]  # noqa: E712
    if pick.empty:
        relaxed.append("1 m deep"); pick = dep[(dep.landlocked == True) & (dep.road == 0) & (dep.fills_in_years <= 2)]  # noqa: E712
    best = pick.sort_values(["reports_300m", "volume_m3"], ascending=False).iloc[0]
    cuts = dep[dep.road == 2]
    road = cuts.iloc[int(np.argmin(metres(best.lat, best.lon, cuts.lat.to_numpy(), cuts.lon.to_numpy())))]
    half = 1000 / 111320
    view = [best.lon - half / np.cos(np.radians(best.lat)), best.lat - half, best.lon + half / np.cos(np.radians(best.lat)), best.lat + half]
    region = lambda row: h3.cell_to_parent(row.h3_lowpoint, 4)  # noqa: E731
    out["cities"][key] = {
        "name": c["name"], "stormDay": c["day"], "candidates": int(rule(dep).sum()), "qualifying": int(len(pick)),
        "relaxed": relaxed, "depressionsInLimits": int(len(dep)), "reports": int(len(r)),
        "depression": {"id": int(best.id), "region": region(best), "low": best.h3_lowpoint, "lat": float(best.lat), "lon": float(best.lon),
                       "depth_m": float(best.max_depth_m), "volume_m3": float(best.volume_m3), "fills_in_years": float(best.fills_in_years),
                       "steps": int(best.steps), "reports_300m": int(best.reports_300m)},
        "road": {"id": int(road.id), "region": region(road), "lat": float(road.lat), "lon": float(road.lon),
                 "metres_from_depression": float(metres(best.lat, best.lon, road.lat, road.lon))},
        "view": [float(x) for x in view],
    }
    (app_data / f"reports_{key}.json").write_text(json.dumps({
        "city": c["name"], "day": c["day"], "source": c["source"], "credit": c["credit"], "downloaded": DOWNLOADED,
        "changes": "street-flooding categories only (as in the studies); the storm day only; repeats at one place on one day counted once; coordinates rounded to 5 decimals; inside the city limits",
        "categories": sorted(r.category.unique().tolist()),
        "points": [[float(x), float(y)] for x, y in zip(r.lon, r.lat)]}, separators=(",", ":")), encoding="utf-8")
    print(key, json.dumps(out["cities"][key]))
Path(out_json).write_text(json.dumps(out, indent=1), encoding="utf-8")
