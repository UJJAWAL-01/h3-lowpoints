from h3lowpoints.hydro.depressions import depth_from_file
from h3lowpoints.h3agg.aggregate import build_cell_table

DEM = "data/pilot_dem.tif"
depth, transform, crs = depth_from_file(DEM)
cells = build_cell_table(DEM, depth, transform, crs, res=11)
cells.to_csv("data/pilot_cells.csv", index=False)
print(len(cells), "cells;", int((cells.max_depth > 0).sum()), "with a depression;",
      int(cells.flat.sum()), "flat")
print(cells.describe().round(2).to_string())
