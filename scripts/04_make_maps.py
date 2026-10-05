import pandas as pd

from h3lowpoints.viz.pydeck_maps import make_map, ramp

cells = pd.read_csv("data/pilot_cells.csv")

# 1) Terrain height: green (low) to brown (high), extruded by height above the lowest cell
t = cells.dropna(subset=["mean_elev"]).copy()
t["rel_elev"] = t["mean_elev"] - t["mean_elev"].min()
t = t.join(ramp(t["mean_elev"], t["mean_elev"].min(), t["mean_elev"].max(), (34, 139, 34), (139, 90, 43)))
make_map(t, "terrain.html", elev_col="rel_elev", elev_scale=3, hover_cols=("mean_elev",))

# 2) Depression depth: gray = none, light blue to dark blue = deeper
c = cells.copy()
c = c.join(ramp(c["max_depth"], 0.15, 1.5, (173, 216, 230), (0, 0, 139)))
none = c["max_depth"] == 0
for ch in ("r", "g", "b"):
    c.loc[none, ch] = 225
make_map(c, "lowpoints.html", elev_col="max_depth", elev_scale=30,
         hover_cols=("max_depth", "depress_area_m2", "depress_volume_m3"))

# 3) Flat cells: dark gray = flat (low relief), light = not flat
f = cells.dropna(subset=["mean_elev"]).copy()
f["flat_val"] = f["flat"].astype(float)
f = f.join(ramp(f["flat_val"], 0, 1, (235, 235, 235), (70, 70, 70)))
make_map(f, "flats.html", hover_cols=("relief", "mean_elev"))
