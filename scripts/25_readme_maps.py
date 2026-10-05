import pandas as pd

from h3lowpoints.viz.pydeck_maps import make_map, ramp

cells = pd.read_csv("data/pilot_cells.csv")

t = cells.dropna(subset=["mean_elev"]).copy()
t["rel_elev"] = t["mean_elev"] - t["mean_elev"].min()
t = t.join(ramp(t["mean_elev"], t["mean_elev"].min(), t["mean_elev"].max(), (34, 139, 34), (139, 90, 43)))
make_map(t, "docs/demo/terrain.html", elev_col="rel_elev", elev_scale=3, hover_cols=("mean_elev",))

s = cells.dropna(subset=["mean_slope_pct"]).copy()
if "edge_cell" in s.columns:
    s = s[~s["edge_cell"].astype(bool)]
s = s.join(ramp(s["mean_slope_pct"], 0, 15, (235, 245, 235), (160, 30, 30)))
make_map(s, "docs/demo/slope.html", hover_cols=("mean_slope_pct",))

d = pd.read_csv("outputs/pilot/cells.csv")
d = d[d["depress_area_m2"] >= 20].copy()
d = d.join(ramp(d["max_depth_m"], 0.15, 1.5, (190, 220, 240), (10, 40, 140)))
make_map(d, "docs/demo/pilot_depressions.html", elev_col="max_depth_m", elev_scale=30,
         hover_cols=("max_depth_m", "depress_area_m2", "depress_volume_m3"))
