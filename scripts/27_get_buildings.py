from pathlib import Path

import geopandas as gpd
import requests

DS = "agk7-8sjm"
W, S, E, N = -97.765, 30.285, -97.675, 30.355  # covers pilot, east and north, with padding
out = Path("data/assets/buildings_2017.geojson")
out.parent.mkdir(parents=True, exist_ok=True)

if not out.exists():
    cols = requests.get(f"https://data.austintexas.gov/api/views/{DS}/columns.json", timeout=60).json()
    kinds = ("polygon", "multipolygon", "location", "point", "line", "multiline")
    geo = [c["fieldName"] for c in cols if c.get("dataTypeName") in kinds]
    print("geometry-like columns:", geo, "| all columns:", [c["fieldName"] for c in cols])
    if not geo:
        raise SystemExit("no geometry column found; send me the column list above")
    wkt = f"POLYGON(({W} {S}, {E} {S}, {E} {N}, {W} {N}, {W} {S}))"
    for where in (f"intersects({geo[0]}, '{wkt}')", f"within_box({geo[0]}, {N}, {W}, {S}, {E})"):
        r = requests.get(f"https://data.austintexas.gov/resource/{DS}.geojson",
                         params={"$where": where, "$limit": 1000000}, timeout=900)
        print(where[:30], "->", r.status_code, len(r.content) // 1_000_000, "MB")
        if r.status_code == 200 and len(r.content) > 1000:
            out.write_bytes(r.content)
            break
    else:
        raise SystemExit("download failed. Export GeoJSON from https://data.austintexas.gov/d/" + DS +
                         " and save it as data/assets/buildings_2017.geojson")

g = gpd.read_file(out)
print(f"\n{len(g)} features; geometry types: {g.geom_type.value_counts().to_dict()}")
print("columns:", list(g.columns))
for c in g.columns:
    if c != "geometry" and g[c].dtype == object and g[c].nunique() <= 25:
        print(f"\n{c}:", g[c].value_counts(dropna=False).to_dict())
for c in g.columns:
    if any(k in c.lower() for k in ("date", "year", "source")):
        print(f"\n{c} sample:", g[c].astype(str).head(3).tolist())
print("\nfootprint area, m2 (percentiles):")
print(g.to_crs(26914).area.describe(percentiles=[.1, .5, .9, .99]).round(0).to_string())
