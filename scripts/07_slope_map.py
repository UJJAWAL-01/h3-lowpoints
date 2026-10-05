import pandas as pd

from h3lowpoints.viz.pydeck_maps import make_map, ramp

cells = pd.read_csv("data/pilot_cells.csv")
f = cells.dropna(subset=["mean_slope_pct"]).copy()
f = f.join(ramp(f["mean_slope_pct"], 0, 8, (235, 245, 235), (160, 30, 30)))
make_map(f, "slope.html", hover_cols=("mean_slope_pct", "flat", "max_depth"))
