import pandas as pd

from h3lowpoints.viz.pydeck_maps import make_map, ramp

cells = pd.read_csv("outputs/pilot/cells.csv")
cells = cells[cells["depress_area_m2"] >= 20].copy()
cells = cells.join(ramp(cells["max_depth_m"], 0.15, 1.5, (190, 220, 240), (10, 40, 140)))
make_map(cells, "docs/demo/pilot_depressions.html", elev_col="max_depth_m", elev_scale=30,
         hover_cols=("max_depth_m", "depress_area_m2", "depress_volume_m3"))
