import numpy as np
import pandas as pd
import rasterio

from h3lowpoints.h3agg.aggregate import pixels_to_h3

with rasterio.open("data/pilot_dem.tif") as s:
    z = s.read(1).astype("float64")
    z[z == s.nodata] = np.nan
    transform, crs = s.transform, s.crs

gy, gx = np.gradient(z, 1.0)          # 1 m cells
slope_pct = np.hypot(gx, gy) * 100
t = pixels_to_h3(slope_pct, transform, crs, res=11, step=4)
g = t.groupby("h3")["v"].agg(mean_slope_pct="mean", n_samples="count").reset_index()

cells = pd.read_csv("data/pilot_cells.csv")
cells = cells.drop(columns=[c for c in ("mean_slope_pct", "n_samples", "edge_cell") if c in cells.columns])
cells = cells.merge(g, on="h3", how="left")
full = g["n_samples"].quantile(0.9)
cells["edge_cell"] = cells["n_samples"] < 0.6 * full
cells["flat"] = (cells["mean_slope_pct"] < 2.0) & ~cells["edge_cell"]
cells.to_csv("data/pilot_cells.csv", index=False)
print("edge cells:", int(cells.edge_cell.sum()), "| flat cells (<2% slope):", int(cells.flat.sum()))
print(cells["mean_slope_pct"].describe().round(2).to_string())
