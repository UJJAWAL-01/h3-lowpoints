import pandas as pd

from h3lowpoints.viz.pydeck_maps import make_map

cells = pd.read_csv("data/pilot_cells_classes.csv")
colors = {1: (200, 200, 200), 2: (60, 170, 90), 3: (150, 80, 190), 4: (230, 70, 40)}
for ch, i in (("r", 0), ("g", 1), ("b", 2)):
    cells[ch] = cells["code"].map(lambda c: colors[int(c)][i])
make_map(cells, "explained_map.html", hover_cols=("code",))
