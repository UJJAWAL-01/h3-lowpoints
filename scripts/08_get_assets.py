from pathlib import Path

import geopandas as gpd
import requests

BBOX = (-97.76, 30.29, -97.72, 30.32)
SETS = {
    "combo_inlet": "gzzc-wbkt",
    "bridge_inlet": "8vin-b63u",
    "drainage_pipe": "d5s6-axa4",
    "open_channel": "5uu3-d6ij",
    "bar_ditch": "f36e-nadv",
    "misc_point": "w5zu-ckt5",
}
out = Path("data/assets")
out.mkdir(parents=True, exist_ok=True)

for name, ident in SETS.items():
    dest = out / f"{name}.geojson"
    if not dest.exists():
        url = f"https://data.austintexas.gov/resource/{ident}.geojson"
        r = requests.get(url, params={"$limit": 500000}, timeout=300)
        if r.status_code != 200:
            print(f"{name}: download failed ({r.status_code}). Export GeoJSON by hand from "
                  f"https://data.austintexas.gov/d/{ident} into {dest}")
            continue
        dest.write_bytes(r.content)
    gdf = gpd.read_file(dest).set_crs(4326, allow_override=True)
    inside = gdf.cx[BBOX[0]:BBOX[2], BBOX[1]:BBOX[3]]
    print(f"\n== {name}: {len(gdf)} features total, {len(inside)} in pilot box")
    print("   geometry:", gdf.geom_type.value_counts().to_dict())
    print("   columns:", list(gdf.columns))
