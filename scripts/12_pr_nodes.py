import pandas as pd
import rasterio

from h3lowpoints.sources.assets import load_clipped

BBOX = (-97.76, 30.29, -97.72, 30.32)
with rasterio.open("data/pilot_dem.tif") as s:
    crs = s.crs
pipes = load_clipped("drainage_pipe", BBOX, crs)

for c in ("up_node_type", "dn_node_type"):
    t = pipes[[c, "status", "year_built"]].copy()
    t["PR"] = t[c].fillna("").str.startswith("PR")
    t["yb"] = pd.to_numeric(t["year_built"], errors="coerce").astype("float64")
    print("\n", c)
    print(t.groupby("PR").agg(n=(c, "size"), median_year_built=("yb", "median")).to_string())
    print("  status, PR:    ", t[t.PR]["status"].value_counts().to_dict())
    print("  status, not PR:", t[~t.PR]["status"].value_counts().to_dict())
