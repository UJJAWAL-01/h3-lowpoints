# Outputs

`Result.save(dir)` writes three files; `Result.depressions`, `Result.cells` and `Result.meta` hold
the same content in memory. Columns appear only when the data behind them was supplied.

## depressions (one row per depression)

| Column | Meaning |
|---|---|
| `id` | Label of the depression within this run |
| `max_depth_m` | Deepest point, metres |
| `area_m2`, `volume_m3` | Area and volume of the filled region |
| `lat`, `lon`, `h3_lowpoint` | Deepest point and the H3 cell containing it |
| `edge` | Cut off by the data border; size unknown (see [definition.md](definition.md)) |
| `level_uncertain` | Tiled runs only: water level depends on a tile border and could differ from a whole-raster run |
| `dist_to_drain_m`, `drain_at_core` | With `drains_xy`: distance to the nearest drain, and whether a drain lies near the deep core |
| `building_overlap`, `likely_building_artifact` | With `buildings`: share of the depression under footprints, and whether it is at least 50% |
| `significant`, `suspect_pit`, `cls` | With waterlogging on: see [waterlogging.md](waterlogging.md) |

## cells (one row per H3 cell that holds depression pixels)

`h3`, `max_depth_m`, `depress_area_m2`, `depress_volume_m3`, and `code` when waterlogging is on
(the highest class code among the cell's depressions; the mapping is in `meta["class_codes"]`).

## meta.json

Package and H3 versions, the DEM path and CRS, `cell_size_m`, `resolution_tier`, every
parameter used (including the waterlogging thresholds), counts of drains, buildings and
depressions, and for tiled runs the tile size and buffer. Keep it with the results: it is what
makes a run reproducible.
