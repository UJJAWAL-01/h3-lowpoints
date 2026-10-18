# Outputs

`Result.save(dir)` writes three files; `Result.depressions`, `Result.cells` and `Result.meta` hold
the same content in memory. Columns appear only when the data behind them was supplied.

## depressions (one row per depression)

| Column | Meaning |
|---|---|
| `id` | Label of the depression: the raster label for `analyze`, 1 to n in scan order for `analyze_tiled` |
| `max_depth_m` | Deepest point, metres |
| `area_m2`, `volume_m3` | Area and volume of the filled region |
| `lat`, `lon`, `h3_lowpoint` | Deepest point, and the H3 cell (at the table's resolution) containing it |
| `h3_lowpoint_15` | The resolution-15 H3 cell (about 1 m across, the finest H3 has) containing the deepest point |
| `edge` | Within one cell of the raster border; size unknown (see [definition.md](definition.md)) |
| `dist_to_drain_m`, `drain_at_core` | With `drains_xy`: distance to the nearest drain, and whether a drain lies near the deep core |
| `building_overlap`, `likely_building_artifact` | With `buildings`: share of the depression under footprints, and whether it is at least 50% |

## cells (one row per H3 cell that holds depression pixels)

`h3`, `max_depth_m`, `depress_area_m2`, `depress_volume_m3`, `n_lowpoints`, `covered_area_m2`,
`coverage`, `depressed_share`. Depth is the maximum over the cell's pixels; area and volume are
sums, and `n_lowpoints` counts the depressions whose deepest point lies in the cell, so cells roll
up exactly with `Result.at_resolution`. `coverage` (0 to 1) is the share of the cell the DEM
covers, `covered_area_m2` that share times the cell's exact area, and `depressed_share` the
depressed area over the covered area, capped at 1 (see [definition.md](definition.md)).

The table's resolution is `Result.res` (also `meta["params"]["res"]`); `meta["res_requested"]`
records what was asked for, such as "auto".

## meta.json

Package and H3 versions, the DEM path and CRS, `cell_size_m`, `resolution_tier`, every
parameter used, counts of drains, buildings and
depressions, and for tiled runs the tile size and the size of the border graph. Keep it with the results: it is what
makes a run reproducible.
