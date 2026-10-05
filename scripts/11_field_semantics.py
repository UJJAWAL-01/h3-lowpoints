import numpy as np
import pandas as pd
import rasterio
import requests

from h3lowpoints.sources.assets import load_clipped

BBOX = (-97.76, 30.29, -97.72, 30.32)
DEM = "data/pilot_dem.tif"
with rasterio.open(DEM) as s:
    crs = s.crs

WANT = {"subtype", "status", "struct_shape", "up_node_type", "dn_node_type", "sump",
        "dem_calculated_sump", "depressed", "depression_a", "inlet_type", "year_built",
        "surface_elevation", "top_elevation"}
for name, vid in {"drainage_pipe": "d5s6-axa4", "combo_inlet": "gzzc-wbkt"}.items():
    try:
        r = requests.get(f"https://data.austintexas.gov/api/views/{vid}/columns.json", timeout=60)
        for c in r.json():
            if c.get("fieldName") in WANT:
                print(f"[{name}] {c['fieldName']}: {c.get('description')}")
    except Exception as e:
        print(name, "column descriptions not available:", e)

pipes = load_clipped("drainage_pipe", BBOX, crs)
act = pipes[pipes["status"] == "ACTIVE"]
for col in ("subtype", "up_node_type", "dn_node_type"):
    print(f"\nactive pipes, {col}:", act[col].fillna("<null>").value_counts().head(10).to_dict())

combo = load_clipped("combo_inlet", BBOX, crs)
combo = combo[combo["status"] == "ACTIVE"]
sump_m = pd.to_numeric(combo["sump"], errors="coerce") * 0.3048
with rasterio.open(DEM) as s:
    z = np.array([v[0] for v in s.sample([(p.x, p.y) for p in combo.geometry])])
ok = (z > -9000) & sump_m.notna().to_numpy()
print("\nactive inlets with sump value and DEM:", int(ok.sum()))
print("DEM elevation minus sump (m, assuming sump is in feet):")
print(pd.Series(z[ok] - sump_m.to_numpy()[ok]).describe().round(2).to_string())
