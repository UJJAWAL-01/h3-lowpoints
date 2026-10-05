import rasterio

from h3lowpoints.sources.assets import load_clipped

BBOX = (-97.76, 30.29, -97.72, 30.32)
with rasterio.open("data/pilot_dem.tif") as s:
    crs = s.crs

FIELDS = {
    "drainage_pipe": ["subtype", "struct_shape", "material", "status", "enabled", "year_abandoned", "year_built"],
    "combo_inlet": ["status", "sump", "dem_calculated_sump", "depressed", "inlet_type", "year_built"],
    "bridge_inlet": ["status", "inlet_type"],
    "open_channel": ["status", "ditch_shape", "material"],
    "misc_point": ["status"],
}
for name, cols in FIELDS.items():
    g = load_clipped(name, BBOX, crs)
    print(f"\n== {name}: {len(g)} features near pilot")
    for c in cols:
        if c in g.columns:
            print(f"  {c}:", g[c].fillna("<null>").value_counts().head(8).to_dict())
