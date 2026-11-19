# Python API

Everything a user is meant to call, with every parameter. Functions not listed here are internal
and may change without notice.

```python
from h3lowpoints import (analyze, analyze_array, analyze_tiled, read_result, Result,
                         resolution_guide, rollup, rollup_saved, reproject_to_utm, validate)
```

Conventions used throughout:

- **Distances and depths are in metres**, never pixels, so a setting means the same on any data.
- **Boxes** (`bbox`) are `(west, south, east, north)` in degrees of longitude and latitude.
- **Warnings** (`UserWarning`) are raised when a request asks more of the data than it can give;
  the analysis still runs. **Errors** (`ValueError`) are raised for input that cannot be used.

## Analysis

### `analyze(dem_path, *, res="auto", min_depth=0.15, mask=None, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False, details=False, breach_crossings=None, breach_radius_m=100.0, workers=1)`

Find the depressions in an elevation file and describe them on H3 cells. Reads the whole raster
into memory; for rasters larger than memory use `analyze_tiled`, which gives the same answer.

| Parameter | Meaning |
|---|---|
| `dem_path` | A single-band raster file (GeoTIFF, VRT, anything rasterio opens) with a CRS, in a projected CRS (metres or feet) or in longitude/latitude degrees. Its nodata value, and NaN, mark missing pixels. |
| `res` | H3 resolution of the cell table: an integer 0 to 15, a string such as `"10"`, or `"auto"` (default) for the finest resolution whose cells hold at least four pixels. |
| `min_depth` | Shallowest depression kept, in metres (default 0.15). |
| `mask` | Optional boolean array of the raster's shape; `True` pixels are ignored like nodata (they are not covered and water can leave through them). Use it for open water. |
| `drains_xy` | Optional array of shape (n, 2): drain locations (x, y) in the raster's CRS. Adds `dist_to_drain_m` and `drain_at_core`. Drains are not used as outlets in the fill. |
| `buildings` | Optional GeoDataFrame or iterable of shapely polygons in the raster's CRS. Adds `building_overlap` and `likely_building_artifact`. |
| `drain_radius_m` | How close a drain must be to the deep core for `drain_at_core` (default 5 m). |
| `drain_core_frac` | The deep core is the part of a depression at least this fraction of its maximum depth deep (default 0.8). |
| `catchments` | Also follow the water ([rules](definition.md#catchments-and-spill)): adds `catchment_area_m2`, `spill_to`, `spill_lat` and `spill_lon` to the depression table and fills `Result.catchments` (default `False`). |
| `details` | Also describe each depression further ([rules](definition.md#levels-shape-and-fill-curve)): `ground_m`, `water_level_m`, `length_m`, `width_m`, `orientation_deg` and the fill curve (`area_*_down_m2`, `volume_*_down_m3`), and fill `Result.depression_cells` (default `False`). |
| `breach_crossings` | Optional road, railway and stream lines: OpenStreetMap lines from `sources.osm.fetch_lines`, or a tuple `(barriers, streams)` or `(barriers, streams, bridges)` of line layers (reprojected when they have a CRS, otherwise taken to be in the raster's CRS). The fill is run a second time with every embankment cut where a stream crosses it: along the stream, up to `breach_radius_m` each side, the ground is lowered to the higher of the lowest ground on each side, never raised and never below the stream's own ground, so a basin with no lower way out is not drained. The depressions stay those of the uncut raster; adds `breached_depth_m`, `road_dam` and `bridge_crossing`, and `meta["breach"]`. |
| `breach_radius_m` | How far along the stream, each side of a crossing, the cut reaches (default 100 m). |
| `workers` | Label pixels with their H3 cells in this many processes (default 1). The result is identical; only the time changes (labelling was 3× faster with 4 processes on a laptop with 4 cores; more did not help). Processes are started on first use, so on Windows and macOS a script must call the analysis from under `if __name__ == "__main__":`. |

Returns a `Result`. Raises `ValueError` when the raster has no CRS, a CRS that is neither
projected nor in degrees, a rotated or sheared grid, latitudes beyond ±90°, or an invalid `res`.
Warns when `res` is finer than the data supports, or when `min_depth` is below 1 m on data coarser
than 2 m.

### `analyze_array(arr, transform, crs, nodata=None, *, res="auto", min_depth=0.15, mask=None, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False, details=False, breach_crossings=None, breach_radius_m=100.0, workers=1, source="array")`

The same as `analyze` for an elevation array already in memory. `transform` is the raster's
affine transform (as from rasterio), `crs` anything rasterio or pyproj accept (such as
`"EPSG:32614"`), `nodata` the value marking missing pixels, and `source` the name recorded as
`meta["dem"]`.

### `analyze_tiled(dem_path, *, tile=4096, out_dir=None, format="csv", hours=None, mask_path=None, verbose=False, res="auto", min_depth=0.15, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False, details=False, workers=1, hooks=None, breach_crossings=None, breach_radius_m=100.0)`

Analyze a raster of any size in square tiles. The result is identical to `analyze` on the same
raster: tiles only decide how much memory is needed. See [Large regions](large-regions.md).

| Parameter | Meaning |
|---|---|
| `tile` | Tile edge in pixels (default 4096). Affects memory and speed, never the result. |
| `out_dir` | Write the tables to this folder as they are produced, instead of returning them. Use it when the output itself may not fit in memory (a state or a country). The returned `Result` then has `depressions = cells = None` and `path = out_dir`. |
| `format` | `"csv"` (default) or `"parquet"`, for `out_dir`. |
| `hours` | With `out_dir`: stop after about this many hours, at the end of a tile, by raising `Paused` (`h3lowpoints.store.Paused`, also importable as `h3lowpoints.tiling.Paused`; its `done` and `total` count tile steps, two per tile). Calling again with the same arguments continues. |
| `mask_path` | Optional raster on exactly the same grid as the DEM; non-zero pixels are ignored like nodata. |
| `verbose` | Print one line per tile per pass. |
| `hooks` | Optional list of steps run on every tile of the last pass, each adding tables to `Result.tables` (saved next to the depression table): `water.WaterHook` (`water_history`) and `floods.FloodHook` (`flood_events`, `flood_seen`). Their tables equal what `water_history` and `flood_events` give on the whole region. Their settings are part of the run's settings, so a resumed run with other hooks is refused. |
| `breach_crossings`, `breach_radius_m` | The road-crossing fix, as in `analyze`; a tiled run cuts exactly the same pixels to the same levels. |
| others | As in `analyze`. |

With `out_dir`, every tile's result is saved in `out_dir/_work` as soon as it is computed, so an
interrupted run continues where it stopped when called again with the same arguments (other
arguments are refused, so two runs are never mixed); `_work` is removed once the tables are
written.

Differences from `analyze`: `Result.labels` and `Result.depth` are `None`, and `id` numbers the
depressions 1 to n. Raises `ValueError` for `tile <= 0` or a mask of another shape, and
`ImportError` for `format="parquet"` without pyarrow (before any work is done).

### `clip.clip_result(folder, boundary, out_dir, *, format=None, name=None, rows=500000)`

Write the part of a saved result inside `boundary` (a shapely geometry in longitude/latitude, or
a vector file in any CRS) to `out_dir`: cells whose centre is inside, depressions whose
`h3_lowpoint` cell is kept, and, when the result has them, the whole catchments and all the
depression × cell rows of the kept depressions. Reads and writes the cell table in pieces, so any size can be
clipped. `name` labels the boundary in `meta["clip"]`. Returns `out_dir`. See
[Command line](cli.md#clip) for the rule's consequences.

### `rollup_saved(folder, res, rows=None)`

The cell table of a saved result rolled up to a coarser resolution,
read in pieces (exactly `rollup` of the whole table), for tables too large to load. Coverage is
not measured.

### `read_result(folder)`

Load a saved result (CSV or Parquet, from `Result.save`, `analyze_tiled(out_dir=...)` or the
command line). Returns a `Result` with the two tables and `meta`; H3 ids are read as text. It has
no footprint, so `at_resolution` on it rolls up depth, area, volume and counts but cannot measure
coverage. Raises `FileNotFoundError` if a file is missing.

### `resolution_guide(dem_path)`

What each H3 resolution means for this raster. Returns a dict:

```python
{"cell_size_m": 30.4, "tier": "coarse", "finest": 10, "summary_only": [0, 1, 2, 3],
 "levels": [{"res": 0, "cell_area_m2": 4.36e12, "pixels_per_cell": 4.7e9, "use": "summary only"},
            ...,
            {"res": 11, "cell_area_m2": 2149.6, "pixels_per_cell": 2.3, "use": "too fine for this DEM"}]}
```

`use` is `"summary only"` (resolutions 0 to 3), `"supported"` or `"too fine for this DEM"`.

### `rollup(cells, res)`

Aggregate a cell table to a coarser resolution `res`: maximum depth, summed area, volume and
`n_lowpoints`. It has no access to the elevation data, so it drops coverage; prefer
`Result.at_resolution`, which measures coverage afresh. Raises `ValueError` if `res` is not
coarser than or equal to the table's resolution.

### `reproject_to_utm(src_path, dst_path, resolution=None)`

Write a copy of a raster in its WGS 84 UTM zone (metres), with bilinear resampling. Not needed
for analysis (longitude/latitude rasters are analyzed natively, and resampling smooths sharp
edges); provided for workflows that need projected data. `resolution` is the output pixel size
in metres (default: matched to the input). Returns `dst_path`.

## The `Result` class

| Attribute | Content |
|---|---|
| `depressions` | The depression table (pandas DataFrame), or `None` for a result written to disk. |
| `cells` | The cell table, or `None` for a result written to disk. |
| `catchments` | The catchment table ([Outputs](outputs.md#the-catchment-table)) with `catchments=True`, else `None`. |
| `depression_cells` | The depression × cell table ([Outputs](outputs.md#the-depression--cell-table)) with `details=True`, else `None`. |
| `meta` | Settings, versions and counts (dict); see [Outputs](outputs.md#metajson). |
| `res` | The cell table's H3 resolution (the resolved value when `"auto"` was asked for). |
| `labels` | `analyze` only: integer raster, each depression's pixels set to its `id`, 0 elsewhere. |
| `depth` | `analyze` only: the depth raster (filled level minus ground) in metres. |
| `transform`, `crs` | The raster's grid. |
| `footprint` | Where the data is valid, used to measure coverage of any cell. Not saved. |
| `path` | The folder holding the tables, for results written to disk or read from disk. |
| `tables` | Tables added by `analyze_tiled(hooks=...)`, by name (`water_history`, `flood_events`, `flood_seen`), or `None`; [Outputs](outputs.md#the-water-history-table). |

| Method | Does |
|---|---|
| `at_resolution(res)` | The cell table at a coarser resolution: exact roll-up, coverage measured afresh. |
| `save(folder, format="csv")` | Write `depressions`, `cells` (and `catchments`, `depression_cells`, `susceptibility` and every table in `tables`, when present) and `meta.json`; `format` is `"csv"` or `"parquet"`. Returns the folder. |
| `load()` | For a result written to disk: a copy with its tables read back. |
| `to_html(path, title=None, max_cells=120000, max_points=20000, n_levels=6)` | Write the interactive web map ([The map](map.md)). Resolutions with more than `max_cells` cells are left out (coarser roll-ups are drawn instead); the deepest `max_points` depressions can be marked; `n_levels` resolutions are offered. Returns the path. |
| `to_deck(value="max_depth_m", elevation_scale=20.0, hover=(), pitch=45)` | A pydeck map for notebooks (needs the `viz` extra): extruded hexagons whose height and colour follow `value`. |

## Validation against observations

See [Validation](validation.md) for the method, how to choose controls and how to read the
result.

### `validate(result, observations, controls=None, *, boundary=None, n_random=None, max_rings=5, depth_classes=(0.5, 1.0, 2.0), classes=None, class_order=None, block_res=6, n_boot=1000, ci=0.95, seed=0, exclude_suspect=True, rows=500000)`

How much more often `observations` fall in or near depressed ground than `controls` do.

| Parameter | Meaning |
|---|---|
| `result` | A `Result`, or a folder written by `analyze`, `build`, `clip` or `Result.save` (read `rows` at a time, so the national dataset works). |
| `observations`, `controls` | Points in any form `validation.read_points` accepts. Without `controls`, `n_random` random points in `boundary` (default 10 per observation, at least 1000), with a warning. |
| `boundary` | The area studied: a shapely geometry in longitude/latitude or a vector file. Points outside it are left out. Without it, a `build` result's box is used if recorded. |
| `max_rings` | Largest distance, in H3 rings of the result's resolution. |
| `depth_classes` | Edges in metres of the depth classes of `by_depth`. |
| `classes` | A label for each depression: a Series indexed by depression `id`, a dict, or a DataFrame or `.csv`/`.parquet` file with `id` and `class` columns. Adds `by_class` and `class_ratios` ([Comparing classes](validation.md#comparing-classes-of-depressions)). Ground is assigned exactly from the result's `depression_cells` table when it has one, otherwise approximately from the depressions' `area_m2` and `volume_m3`. |
| `class_order` | The classes in order (default sorted): ties go to the earlier class, and each class is compared with each later one. `"unclassified"` is reserved. |
| `block_res`, `n_boot`, `ci`, `seed` | Bootstrap blocks (H3 resolution, not finer than the result's), samples, confidence level and seed. |
| `exclude_suspect` | Leave out the ground around depressions flagged `deeper_than_wide`. |

Returns a `Validation`. Raises `ValueError` for unusable settings, for no observations (or
controls) inside the area, and when neither `controls` nor `boundary` is given. Caveats (random
controls, few blocks, few observations, points left out) are issued as `UserWarning`s and kept in
`report["warnings"]`.

| `Validation` attribute or method | Content |
|---|---|
| `rings` | DataFrame, one row per k: `k`, `distance_m`, `obs_within`, `controls_within`, `obs_share`, `control_share`, `lift`, `lift_low`, `lift_high`. |
| `by_depth` | The same per `depth_class` and `k`. |
| `points` | One row per point used: `point_id`, `kind`, `lon`, `lat`, `h3`, `rings`, `depth_m`, `block`, and with `classes` also `class`. |
| `by_class` | With `classes`: the ring table per `class` (the given classes, then `unclassified`) and `k`; otherwise `None`. |
| `class_ratios` | With `classes`: one row per pair of classes and `k`: `class_a`, `class_b`, `k`, `ratio` (lift of `class_a` over lift of `class_b`), `ratio_low`, `ratio_high`, `undefined_share`; otherwise `None`. |
| `report` | Everything, with settings, counts and warnings, as a JSON-ready dict ([fields](validation.md#the-report)). |
| `warnings` | The list of caveats. |
| `summary()` | The ring table as text (with `classes`, also the class table and ratios at 0 and 1 rings). |
| `save(path, points=None)` | Write the report as JSON, and the point table to `points` (`.csv` or `.parquet`). Returns `path`. |

### `validation.read_points(source)`

Points as a DataFrame with `point_id`, `lon` and `lat`, from a CSV (`lon`/`lat`,
`longitude`/`latitude` or `lng`/`lat` columns, optional `id`), a vector file in any CRS, a
DataFrame or GeoDataFrame, or an (n, 2) array of longitude, latitude. Polygons and lines become a
point on them. Rows without coordinates are dropped. Raises `ValueError` for missing columns or
values that are not degrees.

### `validation.random_points(boundary, n, seed=0)`

`n` points spread evenly by area (on the sphere) over `boundary`, a shapely geometry in
longitude/latitude or a vector file. `seed` may be a number or a numpy `Generator`.

### `validation.validate_flood_map(result, flood_map, *, n=20000, seed=0, **kwargs)`

`validate` with a flood map instead of points ([Validation](validation.md#flood-maps)):
`flood_map` is a raster coded 1 flooded, 0 observed and dry, anything else not observed. Up to
`n` flooded pixels become the observations and up to `n` dry pixels the controls, drawn with
chances in proportion to pixel area (`seed`). Other arguments are those of `validate`. The
report gains a `flood_map` section. Raises `ValueError` when the map has no flooded or no
observed dry pixel.

### `validation.flood_map_points(flood_map, *, n=20000, seed=0)`

The points `validate_flood_map` uses: `(observations, controls, info)`, with the map's counts
of flooded, dry and not-observed pixels in `info`.

## Susceptibility: `h3lowpoints.susceptibility`

See [Susceptibility](susceptibility.md) for the method and [Outputs](outputs.md#the-susceptibility-table)
for the columns.

### `susceptibility.susceptibility(result, *, curve_numbers=None, curve_number=None, impervious=None, rain=None, ratio_years=(2, 10, 100), min_coverage=0.9, cache_dir=None, rows=500000, workers=1)`

One row per depression: its curve number, the rain that fills it and, with storms, how full they
leave it. Returns a DataFrame whose `attrs["settings"]` hold the settings for `meta.json`.

| Parameter | Meaning |
|---|---|
| `result` | A `Result` computed with `catchments=True`, or its folder (the catchment table is read `rows` at a time). |
| `curve_numbers` | SCS curve numbers: a raster in any CRS (its mean per H3 cell is used; in the US, `runoff.fetch_curve_numbers`), a `.csv` or `.parquet` table with columns `h3` and `curve_number` at the result's resolution (read in pieces; must be sorted by `h3`), a folder of such tables whose names sort in the order of their cells (read one after the other, as for a national run done in regions), or a DataFrame with those columns. |
| `curve_number` | Instead of `curve_numbers`: one curve number in (0, 100]. Exactly one of the two is required. |
| `impervious` | Optional percent paved, in the same forms (column `impervious_pct`), reported as `impervious_pct`. |
| `rain` | `None` (no storms), `"atlas14"` (24-hour storms of 1 to 1000 years from NOAA Atlas 14 grids), a depth in mm (one uniform storm, column `fill_ratio`), or a dict `{return period in years: depth in mm or function(lat, lon) -> mm}`. |
| `ratio_years` | The return periods given a `fill_ratio_<N>yr` column; all storms count for `fills_in_years`. |
| `min_coverage` | Share of a catchment's area an input must cover. |
| `cache_dir` | Keep downloaded rainfall grids here. |
| `workers` | Processes for labelling raster pixels with H3 cells. |

Raises `ValueError` for a result without catchments, for neither or both of `curve_numbers` and
`curve_number`, and for unusable settings.

### `susceptibility.write_susceptibility(folder, table)`

Writes `table` into a result folder's susceptibility table (`susceptibility.csv` or
`.parquet`, in the format of the folder's tables) and records its settings in `meta.json`
(`susceptibility`). A table from `susceptibility` replaces the index columns, one from
`drainage.drainage_flags` the drainage columns; the other group is kept. Returns the path.

### `susceptibility.cell_means(raster, res, *, parent=None, rows=1024, workers=1)`

The mean of a raster's values on each H3 cell at resolution `res` (each pixel belongs to the cell
containing its centre; no-data pixels are left out), as a table sorted by cell: `h3`, `mean`,
`n_pixels`. `parent` (a coarser H3 cell) keeps only the cells inside it, so a large area can be
done in pieces whose cells are complete. The raster is read `rows` rows at a time.

### `runoff.curve_numbers(land_cover, impervious, groups)`

The composite SCS curve number of each pixel from arrays on one grid: NLCD land cover classes,
percent paved (values above 100 count as 0) and soil group codes of `sources.soils`. NaN where the
land cover is unknown. The values are in `runoff.CN_TABLE`
([Susceptibility](susceptibility.md#runoff-curve-numbers)).

### `runoff.runoff_mm(rain_mm, cn)`, `runoff.rain_for_runoff_mm(runoff, cn)`, `runoff.retention_mm(cn)`

The SCS runoff equation and its exact inverse, in millimetres, and S, the most the ground can
still hold.

### `runoff.fetch_curve_numbers(bbox, out_path, *, cache_dir, year=None, source="annual")`

Curve numbers (float32, NaN for no data) for a box in the lower 48 US states, on NLCD's 30 m
grid, from NLCD land cover and imperviousness of `year` (from `source`, as in `fetch_land_cover`:
Annual NLCD 2024 by default) and the gNATSGO soil groups. The three
inputs are kept beside the output (`*_land_cover.tif`, `*_impervious.tif`, `*_soil_groups.tif`);
the soil survey's map-unit table is kept in `cache_dir`.

### `hydro.cascade.spill_order(ids, spill_to)`

`(order, target)`: `target[i]` is the position of the depression row `i` spills into (-1 when its
water leaves the table: `spill_to` 0 or a depression not listed), and `order` lists every
position before the one it spills into. Raises `ValueError` if the spill links loop.

### `hydro.cascade.cascade(order, target, own, volume)`

The water reaching each depression (m³), given each one's own runoff (`own`, one column per
storm, or 1-d for one storm) and its `volume`. Returns `(inflow, leaving)`, `leaving` being the
water that left the table. NaN in `own` makes everything downstream NaN.

### `hydro.cascade.rain_to_fill_upstream(order, target, area, curve_number, volume, rel=0.01)`

The uniform rain (mm) that fills each depression from its catchment `area` (m²) of the given
`curve_number`, counting the overflow of the depressions upstream that fill first, exact to
within a factor `1 + rel`; exactly the own rain where nothing spills in.

### `hydro.cascade.upstream_min(order, target, values)`

For each depression, the smallest of `values` over it and every depression upstream.

### `drainage.drainage_flags(result, *, streams=None, canals=None, roads=None, land_cover=None, near_m=30.0, min_share=None, max_distance_m=1000.0, crs=None, rows=500000)`

One row per depression: `land_cover`, `stream_m`, `canal_m`, `channel_share`, `spill_road_m`,
`drained`, `landlocked` ([Susceptibility](susceptibility.md#drained-or-landlocked)). Returns a
DataFrame whose `attrs["settings"]` hold the settings; write it with `write_susceptibility`.
Needs the `sources` extra.

| Parameter | Meaning |
|---|---|
| `result` | A `Result` or its folder. `channel_share` needs `details=True`, `spill_road_m` needs `catchments=True`. |
| `streams`, `canals`, `roads` | Lines: a vector file (any CRS), a GeoDataFrame or a GeoSeries. At least one is required. |
| `land_cover` | A raster of land cover classes, read at each lowest point. |
| `near_m`, `min_share` | The rule for `drained`; with `min_share` None (the default) `channel_share` is reported but not used. In the US pass highways and ramps as `roads` (`drainage.MAJOR_ROADS`, the TIGER classes S1100, S1200, S1630). |
| `max_distance_m` | Distances beyond this are NaN. |
| `crs` | Projected CRS, in metres, to measure in; by default the UTM zone of the result's centre. |

### `drainage.cells_along(lines, crs, res, step=None, batch=2000000)`

The H3 cells (as sorted unique integers) at resolution `res` that lines in projected `crs` pass
through, found by points every `step` metres along them (default a third of the cells' edge),
about `batch` points at a time so that a state's millions of lines fit in memory.
`drainage.in_sorted(values, sorted_values)` tells which values are in such a sorted array.

## Storms in time: `h3lowpoints.storms`

The duration model: one storm followed in time steps. See [Storms in time](storms.md).

### `storms.storm_response(result, *, hours, years=None, depth_mm=None, method="curve-number", curve_numbers=None, curve_number=None, condition="average", impervious=None, soil=None, pattern="atlas14", case="all", percent=50, seepage=None, drains=None, ponding_depth_m=0.1, after_hours=72.0, step_minutes=5.0, rarity=True, min_coverage=0.9, cache_dir=None, rows=500000, workers=1)`

One row per depression (`storms.STORM_COLUMNS`, plus the inputs used): when it fills, how deep
it gets, where its water goes and how long it stands ([Outputs](outputs.md#storm-tables)).
Returns a DataFrame whose `attrs["settings"]` hold the settings; write it with `write_storm`.

| Parameter | Meaning |
|---|---|
| `result` | A `Result` computed with `catchments=True`, or its folder. |
| `hours` | How long it rains. |
| `years`, `depth_mm` | The storm, one of the two: NOAA Atlas 14's storm of `years` (`ATLAS14_YEARS`) and `hours` on each catchment; or a depth in mm, or a function of (lat, lon) giving mm. |
| `method` | `"curve-number"` (SCS runoff of the rain fallen so far) or `"green-ampt"` (infiltration). |
| `curve_numbers`, `curve_number` | Method curve-number: as in `susceptibility`. |
| `condition` | `"average"`, `"dry"` or `"wet"` ground before the storm. |
| `impervious` | Percent paved per cell (raster, table, DataFrame with `impervious_pct`); for green-ampt and drains. |
| `soil` | For green-ampt and seepage: the dict of `fetch_soil_infiltration`, or a dict with `ksat` (mm/h), `suction` (mm), `deficit_average`, `deficit_dry` (shares) and optionally `fallback`, each a raster, table (column named as the key), DataFrame or number. |
| `pattern`, `case`, `percent` | `"atlas14"` (NOAA's curve of the region at the lowest point, chosen by `case` and `percent`), `"uniform"`, or a pair (time shares, cumulative rain shares). |
| `seepage` | Seepage through the floor; by default on when `soil` is given. |
| `drains` | None, or `{"years": N, "hours": T, "min_impervious": 20}`: catchments at least that percent paved lose rain up to the intensity of the N-year, T-hour Atlas 14 storm. |
| `ponding_depth_m`, `after_hours` | Standing water is at least this deep; the run continues this long after the rain. |
| `step_minutes` | The storm is cut into equal steps of about this many minutes. |
| `rarity` | Give each depression `storm_years` (needs the Atlas 14 grids; NaN when False). |
| `min_coverage`, `cache_dir`, `rows`, `workers` | As in `susceptibility`; `cache_dir` also keeps the temporal distributions and region maps. |

### `storms.write_storm(folder, table, name)`

Writes the table as `storm_<name>.<format>` in a result folder, in the format of its tables, and
its settings in `meta.json` under `storms.<name>`. `verify.check_storms` checks these tables
(water balance, bounds, ids); `check_saved` runs it.

### `storms.simulate(spill_to_index, depth_mm, cum, *, catch_area, volume, area, max_depth, dt, n_after=0, method="curve-number", curve_number=None, impervious=None, ksat_mm_h=None, suction_deficit_mm=None, seepage_mm_h=None, drain_mm_h=None, ponding_depth_m=0.1, pattern_of=None)`

The model on plain arrays, one value per depression, for your own inputs (real radar rain, say:
one cumulative curve per depression in `cum` and `pattern_of = arange(n)`). Returns a dict of
arrays (`own_m3`, and the water columns of the storm table) and the water that left the table.

### `storms.shape_table(volume, area, max_depth, levels=None)`

The known points (volume, depth at the lowest point, wet area) of each depression from its
measured levels (`{name: (volume_m3, area_m2)}` at `storms.LEVELS`) and its full point;
`storms.stage_of(store, *table)` gives the depth and wet area at any volumes, by the rule of
[Storms in time](storms.md#the-shape-of-a-depression).

### `storms.green_ampt_step(F, ks, sd, hours)`

Cumulative infiltration (mm) after `hours` with water standing on the ground, starting from `F`
mm, for conductivity `ks` (mm/h) and suction x water deficit `sd` (mm): the implicit Green-Ampt
equation solved exactly.

### `storms.pattern_steps(times, cumulative, n_steps)`

A cumulative rain curve (shares of the duration and of the rain) at the ends of `n_steps` equal steps.

### `runoff.adjust_curve_number(cn, condition="average")`

Curve number for `"dry"` (ARC I) or `"wet"` (ARC III) ground from the average one, by NRCS's
table 10-1 (`runoff.ARC_TABLE`), linear between its rows.

### `runoff.texture_class(name)`

The USDA texture class of an NRCS texture name ("Fine sandy loam" gives "sandy loam"), or None;
its wetting-front suction is `runoff.SUCTION_MM[...]` (Rawls, Brakensiek and Miller, 1983).

## Observed water: `h3lowpoints.water` and `h3lowpoints.floods`

The method, rules and limits are in [Seen wet and seen flooded](water-history.md); the columns
in [Outputs](outputs.md#the-water-history-table).

### `water.water_history(result, *, years=None, source=None, workers=1)`

One row per depression of a result held in memory (from `analyze`, which keeps the pixel
labels): its 1984-2021 JRC Global Surface Water record and `water_class` (outline method).
`years` limits the years used (default all); `source` is `None` to read windows from the JRC
server, or a folder of JRC tiles laid out as on the server. `workers` sets the processes used to
place pixels in H3 cells (same answer). The settings, rules and credit are in
`table.attrs["settings"]`. Raises `ValueError` for a result without labels.

### `water.water_history_by_cells(depressions, depression_cells, bbox, *, years=None, source=None, layers=None, workers=1)`

The same from a saved result's depression × cell table (cells method; `details=True`), for the
depressions in `bbox` (lon/lat). `depressions` needs `id`, `lat` and `lon`; `depression_cells`
needs `id`, `h3` and `area_m2`. An approximation: water anywhere in an H3 cell is spread over the
whole cell. `layers` passes JRC layers already read for the box's window.

### `water.WaterHook(source=None, years=None, workers=1)`

For `analyze_tiled(hooks=[...])`: the `water_history` table of a region too big to hold,
counted tile by tile (outline method), equal to `water_history` on the whole region. For a
national run download the JRC tiles first and pass the folder as `source`; reading windows from
the server is far too slow at that size.

### `floods.flood_events(result, scenes, *, reader=None, workers=1, advisory=False)`

Per depression of a result held in memory: in how many flood maps it was covered, observed and
flooded. `scenes` is a table from `sources.gfm.search` (one row per item) or a dict
`{name: path}` of your own rasters coded 1 flooded, 0 observed and dry, anything else not
observed. Returns `(summary, seen)` ([Outputs](outputs.md#the-flood-event-tables)). Items missing
a layer are skipped and listed in `attrs["settings"]["skipped_missing_layer"]`. `workers` reads
that many maps at once (threads). `advisory=True` (GFM only) also counts pixels under GFM's
advisory flags as not observed, as the national tables do. `reader` replaces the map reader (for
tests).

### `floods.FloodHook(scenes, *, workers=4, reader=None, advisory=False)`

For `analyze_tiled(hooks=[...])`: the `flood_events` and `flood_seen` tables of a region too big
to hold, equal to `flood_events` on the whole region. Each tile reads only the maps whose outline
reaches it.

## Confidence grade: `h3lowpoints.confidence`

The rule and its limits are in [Confidence grade](confidence.md); the columns in
[Outputs](outputs.md#the-confidence-table).

### `confidence.confidence(result, *, error_m, water=None, floods=None, agreement=None)`

One row per depression: base grade from depth against `error_m` (`ERROR_3DEP_30M` = 1.0,
`ERROR_GLO30` = 2.0, or your own figure), flags from the depression table (`road_dam`,
`bridge_crossing`, `likely_building_artifact`, where the analysis made them), support from
`water` (a water history table) and `floods` (a table with `id` and `scenes_flooded`), and the
grade. `agreement` (a table from `compare`) adds `terrain_agreement`, which is not part of the
grade. `attrs["checks"]` lists the checks that were run; a check not run leaves its column empty.

### `confidence.grade(depth_m, error_m, flagged, support)`

The rule on arrays: returns `(letters, base)`, the grade letters and the base grades (0 = A,
1 = B, 2 = C).

### `confidence.compare(a, b, *, confirm_share=0.25)`

Agreement of two results with labels (for example 30 m against 10 m terrain): one row per
depression of `a` with `compared_share`, `covered_share`, `match_id`, `match_share`,
`match_depth_m`, `depth_diff_m` and `confirmed` (`covered_share` ≥ `confirm_share`).
`attrs["summary"]` counts matched, missed and extra depressions ([Confidence grade](confidence.md#comparing-two-terrains-compare)).

## Verification: `h3lowpoints.verify`

See [Verification](verification.md) for what each check means.

| Function | Returns |
|---|---|
| `check_result(result)` | List of problems (empty when every invariant holds). Works on results in memory and on disk. |
| `check_tables(depressions, cells, meta, catchments=None, depression_cells=None, susceptibility=None)` | The same for the tables (the susceptibility table with its index and drainage columns). |
| `check_saved(folder, rows=500000)` | The same for a saved folder, reading the cell table `rows` at a time so any size can be checked; includes `check_storms`. |
| `check_storms(folder, meta=None, rows=500000)` | Problems in a folder's storm tables: missing tables, depressions not in the depression table or listed twice, water not conserved, fills or hours out of bounds. |
| `check_evidence(folder, meta=None, rows=500000)` | Problems in a folder's evidence: the water history and flood event tables (one row per depression, years and counts within their bounds and consistent, `flood_seen` adding up to `flood_events`) and the road-crossing columns (`breached_depth_m` between 0 and `max_depth_m`, `road_dam` matching it). Part of `check_saved`. |
| `compare_results(a, b, rel=1e-9)` | Differences between two results of the same data (empty when they agree). |
| `spot_check(dem_path, *, windows=3, size=600, tile=170, seed=0, **params)` | Cuts `windows` random `size` × `size` windows from a raster, analyzes each whole and tiled, and returns every disagreement or invariant failure. `params` go to both analyses (`res`, `min_depth`, ...). |

## Data sources: `h3lowpoints.sources`

All readers read only the parts of remote files a box needs (a failed remote raster read is
retried up to ten times with a doubling delay, riding out at least 17 minutes of lost connection;
the small requests that check which tiles exist, query catalogues or download a tile are retried
after 1, 2, 4 ... 128 seconds when the connection drops or the server is busy, about 4 minutes in
all), and treat only an HTTP 404 answer as "no tile here": any other failure raises,
so a broken connection is never mistaken for missing data. Details and licences: [Data sources](data-sources.md).

### `sources.copernicus.fetch_dem(bbox, out_path, *, source=None, max_tiles=16)`

Copernicus GLO-30 (global, 30 m surface model) for a box, written as a GeoTIFF (WGS 84, float32,
nodata −32768). `source` is a folder of local tiles instead of the public bucket; `max_tiles`
refuses boxes touching more 1° tiles, so a typo cannot start a continent-sized download. Missing
tiles (open ocean) are left as nodata.

### `sources.usgs.fetch_3dep(bbox, out_path, *, resolution=30, source=None, max_tiles=16)`

USGS 3DEP bare-earth elevation (United States) at `resolution` 30 or 10 metres, as a GeoTIFF
(NAD83 longitude/latitude, float32, nodata −999999). Each pixel is copied from the tile whose own
1° cell holds it (see [Data sources](data-sources.md#usgs-3dep-30-m-and-10-m)).

### `sources.usgs.build_3dep_vrt(bbox, path, *, resolution=30, source=None, cache_dir=None, workers=8)`

A virtual raster (VRT, a small text file pointing at the tiles) of 3DEP over a box of any size,
for `analyze_tiled`. `cache_dir` downloads each tile there once (an interrupted run keeps the tiles
already downloaded; a partial download never takes the real name) instead of streaming it, which pays off because a tiled analysis
reads the data twice. `workers` tiles are checked in parallel. Returns the path.

### `sources.usgs.fetch_lidar(bbox, out_path, *, items=None, max_area_km2=100.0)`

USGS 3DEP 1 m lidar for a box, as a GeoTIFF in the UTM zone of the survey covering the box's
centre (float32, nodata −9999). Where surveys overlap the newest wins; tiles in another UTM zone
are reprojected (bilinear). `items` lets you supply catalogue entries yourself (to pin one
survey); `max_area_km2` refuses larger boxes (100 km² is 100 million pixels).

### `sources.usgs.find_lidar_tiles(bbox, page_size=200)`

The 1 m lidar tiles in the USGS catalogue touching a box, newest survey first (dicts with
`title`, `publicationDate`, `downloadURL`, `boundingBox`).

### `sources.landcover.fetch_worldcover(bbox, out_path, *, source=None, max_tiles=4)`

ESA WorldCover 2021 land-cover classes (global, 10 m) as a GeoTIFF (WGS 84, uint8, 0 = no data).
Class codes and names are in `sources.landcover.WORLDCOVER_CLASSES`.

### `sources.landcover.build_water_mask(dem_path, out_path, *, source=None, cache_dir=None, tile=4096, hours=None, verbose=False)`

A water mask on exactly the pixel grid of an elevation raster (any CRS), from ESA WorldCover: a
pixel is 1 (water) when at least 5 of 9 evenly spaced points inside it fall on class 80, or
when at least 5 fall where WorldCover has no data and the pixel is at or below 0 m (the open sea,
see [Data sources](data-sources.md#water-mask-from-esa-worldcover)),
"permanent water bodies", else 0. Pass it as `mask_path` to `analyze_tiled`. `source` is a
folder of local WorldCover tiles, `cache_dir` downloads each tile once; otherwise tiles are read
over the internet.

- `out_path` ending in `.tif`: one GeoTIFF, written under a temporary name and renamed when
  complete (areas up to a few hundred million pixels).
- `out_path` ending in `.vrt`: built `tile` × `tile` pixels at a time, one small GeoTIFF per tile
  holding water in `<name>_tiles/`, the `.vrt` written last. An interrupted build continues where
  it stopped, and `hours` stops it at the end of a tile by raising `Paused` (for regions).

Rasters in longitude/latitude are sampled directly, treating their datum as WGS 84; other CRSs go
through a warped view of WorldCover. Returns the path.

### `sources.landcover.worldcover_vrt(bbox, path, *, source=None, cache_dir=None, workers=8)`

A virtual raster of the WorldCover tiles over a box of any size (missing ocean tiles read as 0).

### `sources.landcover.fetch_impervious(bbox, out_path, *, year=None, source="annual", max_area_km2=20000.0, data=None)`

NLCD percent impervious surface (lower 48 US states, 30 m) as a GeoTIFF in Conus Albers, values 0
to 100 and 255 for no data. `source="annual"` (default) reads Annual NLCD, `year` 1985 to 2024
(default 2024; `sources.landcover.ANNUAL_NLCD_YEARS`); `source="mrlc"` asks the MRLC service for an
older edition, `year` one of `sources.landcover.NLCD_YEARS` (default 2021), and there `data`
supplies the service's GeoTIFF bytes yourself (for tests or a cache).

### `sources.landcover.fetch_land_cover(bbox, out_path, *, year=None, source="annual", max_area_km2=20000.0, data=None)`

NLCD land cover classes (`sources.landcover.NLCD_CLASSES`; 0 for no data) for a box, at 30 m in
Conus Albers, from `source` and `year` as for `fetch_impervious`. Lower 48 US states only.

### `sources.landcover.annual_nlcd_url(product, year=2024)`

The URL of an Annual NLCD CONUS mosaic: `product` "LndCov" (classes) or "FctImp" (percent
impervious).

### `sources.nhd.fetch_flowlines(bbox, out_path=None, *, max_lines=200000, get=read_bytes)`

NHD high-resolution flowlines intersecting a box, from the NHD map service, as a GeoDataFrame in
WGS 84 with `ftype`, `fcode` and lines. `sources.nhd.STREAM_FTYPES` (460, 558) and
`CANAL_FTYPES` (336, 420, 428) pick streams and canals. For areas beyond about a degree, use
`state_flowlines`.

### `sources.nhd.state_flowlines(state, *, cache_dir, bbox=None, ftypes=None)`

A state's NHD high-resolution flowlines, from its GeoPackage zip (downloaded once into
`cache_dir`; up to 3.5 GB), unpacked once beside it and read through its spatial index. `state`
as named on The National Map ("Texas", "District of Columbia"); `bbox` and `ftypes` read only
part of it. `sources.nhd.remove_unpacked(state, *, cache_dir)` deletes the unpacked GeoPackage
(the zip is kept) and returns the bytes freed.

### `sources.soils.fetch_soil_groups(bbox, out_path, *, cache_dir, like=None, resolution=30.0, get=read_bytes, opener=None)`

Soil hydrologic group codes (`sources.soils.GROUPS`: 1 A, 2 B, 3 C, 4 D, 5 A/D, 6 B/D, 7 C/D,
8 a map unit without a group, 0 no data) for a box, from the USDA gNATSGO soil survey (July 2020
edition, read from Microsoft's Planetary Computer without an account). The grid is NAD83 / Conus
Albers at `resolution` metres with pixel edges where NLCD's are; `like` (a raster path) takes
that raster's grid exactly, which is how to combine the groups with an NLCD download pixel for
pixel. The map-unit table (12.5 MB) is kept in `cache_dir`. `sources.soils.group_table`,
`tiles` and `to_groups` are its parts.

### `sources.soils.fetch_soil_infiltration(bbox, out_stem, *, cache_dir, like=None, resolution=30.0, get=read_bytes, opener=None, post=_post)`

Writes the Green-Ampt soil rasters of a box (`<stem>_ksat.tif`, `_suction.tif`,
`_deficit_average.tif`, `_deficit_dry.tif`, `_fallback.tif`) on the grid of `fetch_soil_groups`
and returns their paths in a dict. Map units come from the gNATSGO grid; their horizons from
USDA Soil Data Access, kept in `cache_dir` ([Data sources](data-sources.md#usda-soil-survey-how-fast-water-soaks-in)).

### `sources.soils.infiltration_table(mukeys, cache_dir, *, batch=500, post=_post)`

The Green-Ampt parameters of map units (`mukey`, `ksat_mm_h`, `suction_mm`, `theta_sat`,
`theta_fc`, `theta_wp`, `texture`, `component`, `comppct`), each asked of Soil Data Access once
and kept in `cache_dir`. `infiltration_parameters(horizons)` is the rule applied to the rows it
returns; `sda_query(sql)` runs any query.

### `sources.soils.mukey_grid(bbox, *, like=None, resolution=30.0, get=read_bytes, opener=None)`

(map-unit keys, transform, CRS) of the gNATSGO grid on the output grid of `fetch_soil_groups`.

### `sources.roads.fetch_roads(bbox, out_path=None, *, cache_dir, year=2024, get=read_bytes)`

Every road of the counties a box touches, from Census TIGER/Line, cut to the box: a GeoDataFrame
in WGS 84 with the road class `MTFCC`. One zip per county is kept in `cache_dir`.
`sources.roads.counties(bbox)` lists the counties' GEOIDs and `county_roads(geoid, *, cache_dir)`
reads one county.

### `sources.boundaries.us_lower48(out_path=None, *, year=2025, data=None)`

The lower 48 US states and DC as one shapely geometry (longitude/latitude), from the US Census
Bureau's cartographic boundary file (1:500,000, public domain); Alaska, Hawaii and the
territories are left out. `out_path` also saves it (GeoPackage or GeoJSON). Needs the `sources`
extra.

### `sources.rainfall.design_rainfall(lat, lon)`

NOAA Atlas 14 precipitation-frequency estimates for a point in the US, in millimetres, as a
DataFrame: rows are durations (`"5-min"` ... `"60-min"`, `"2-hr"` ... `"24-hr"`, `"2-day"` ...
`"60-day"`), columns are average recurrence intervals in years (1, 2, 5, 10, 25, 50, 100, 200,
500, 1000). Raises `ValueError` where Atlas 14 has no estimates (Washington and Oregon).

### `sources.rainfall.rainfall_depth(lat, lon, duration="60-min", ari_years=10, table=None)`

One value from that table, in millimetres (for example the 1-hour storm expected once in 10
years). Pass `table` to avoid fetching it again.

### `sources.rainfall.Atlas14Grids(years=(1, 2, 5, 10, 25, 50, 100, 200, 500, 1000), duration="24h", *, cache_dir=None, volumes=None)`

NOAA Atlas 14 depths for many points at once, from NOAA's published grids (one zip per project
area and return period, about 1 to 4 MB each; `cache_dir` keeps them). `duration` is one of
NOAA's file codes (`sources.rainfall.ATLAS14_DURATIONS`: `"05m"` ... `"60m"`, `"02h"` ... `"24h"`,
`"48h"`, `"03d"` ... `"60d"`); `volumes` the project areas to use (default all of
`ATLAS14_VOLUMES`). `depth(years, lat, lon)` returns millimetres at each point: the value of the
grid cell containing it, the mean where two project areas overlap, NaN where none covers it
(Washington, Oregon, the sea, outside the US). A grid is downloaded and read the first time a
point falls within its project area's extent (`ATLAS14_EXTENTS`), so a city needs only its own
area's grids; `grid(volume, years)` returns one grid as `(values, west, north, cell)`.

### `sources.rainfall.Atlas14Storms(*, cache_dir=None, volumes=None)`

NOAA Atlas 14 storms of any duration from 5 minutes to 60 days. `depth(years, hours, lat, lon)`:
depth in mm, interpolated between published durations in log(depth) against log(duration);
`depths(hours, lat, lon)`: at every return period; `return_period(depth_mm, hours, lat, lon)`:
years between such storms (0 below the 1-year storm, inf above the 1000-year storm, NaN outside
Atlas 14). `years_of_depth(depth_mm, table)` is the rule on depths you already have.

### `sources.rainfall.TemporalPatterns(*, cache_dir=None)`

NOAA Atlas 14 temporal distributions. `region(lat, lon)`: the region name at each point (None
outside); `pattern(name, hours, case="all", percent=50)`: (time shares, cumulative rain shares)
of the published duration nearest to `hours` (6, 12, 24 or 96); `table(name, hours)`: every
curve. `parse_temporal(text)` reads one of NOAA's files, in either layout;
`temporal_url(name, hours)` gives its address.

### `sources.rainfall.atlas14_grid_url(volume, years, duration="24h")`

The URL of one of NOAA's zipped Atlas 14 grids.

### `sources.rainfall.read_ascii_grid(data)`

Reads an ESRI ASCII grid (the bytes of an `.asc` file, or of a zip holding one): returns
`(values, west, north, cell)`, with NaN for no data.

### `sources.jrc.read_layer(layer, bbox, *, year=None, month=None, source=None)`

One layer of JRC Global Surface Water (1984-2021, 30 m, worldwide 80° N to 60° S) over a lon/lat
box, as `(array, transform)` on the JRC grid (0.00025°). `layer` is `occurrence`, `recurrence`,
`extent`, `yearly` (with `year`, 1984 to 2021) or `monthly_recurrence` (with `month`, 1 to 12);
values in [Data sources](data-sources.md#jrc-global-surface-water). Only the 256 × 256 blocks the
box needs are read from the JRC server, or from a folder of tiles (`source`). Where no tile
exists the result is 0.

### `sources.jrc.read_window(layer, row0, col0, rows, cols, *, year=None, month=None, source=None)`

The same for a window of the global JRC grid (row and column offsets from 80° N, 180° W), as
returned by `sources.jrc.grid_window(bbox)`.

### `sources.jrc.grid_window(bbox)`

`(row0, col0, rows, cols)`: the window of the global JRC grid that covers a lon/lat box, snapped
outwards to whole pixels and clipped to the record. Raises `ValueError` for a box outside it.

### `sources.jrc.local_path(source, layer, row_off, col_off, *, year=None, month=None)`, `sources.jrc.tile_url(layer, row_off, col_off, *, year=None, month=None, base=...)`

Where one tile lives in a local folder laid out as on the server, and its URL on the JRC
server: for downloading the tiles of a large region once.

### `sources.gfm.search(bbox, start, end, *, min_flooded_pixels=None, page=500)`

Copernicus GFM catalogue items (Sentinel-1 radar flood maps, 2015 onwards) touching a lon/lat box
between two dates, from EODC's STAC catalogue (no account). One row per item (one scene cut to one
300 km tile): `item`, `datetime`, `tile`, `flooded_pixels` (of the whole scene),
`floodable_pixels`, `anomaly_detected`, `version`, `geometry` (lon/lat outline) and the URLs of
the `flood`, `exclusion`, `reference` and `advisory` layers. `min_flooded_pixels` keeps only
scenes with at least that many flooded 20 m pixels (filtered by the catalogue). Sorted by item id.

### `sources.gfm.read_scene(item, bbox=None, *, crs=None, opener=None, advisory=False)`

One item as a coded map on GFM's 20 m grid: 1 flooded, 0 observed and dry, 255 not observed
(outside the image, excluded, normally water, and with `advisory=True` under an advisory flag).
`bbox` limits the read (lon/lat, or in `crs`). Returns `(codes, transform, crs)`; `codes` is
`None` when the box misses the tile. Raises `ValueError` when a layer is missing or the layers
are not on one grid.

### `sources.gfm.write_flood_map(item, bbox, out_path)`

Write an item's coded map inside a lon/lat box to a GeoTIFF (nodata 255), the form
`validate --flood-map` and `flood_events` take. Returns the path, or `None` when the box misses
the tile.

### `sources.gfm.missing_layers(item, advisory=False)`

The layers an item has no file for (an item with any missing cannot be judged).

### `sources.gfm.credit(datetimes)`

The acknowledgement CEMS asks for, with the years of the scenes used.

### `sources.osm.fetch_lines(bbox, *, cache_dir, extract=None)`

OpenStreetMap roads, railways and streams in a lon/lat box, for the road-crossing fix. Downloads
the smallest Geofabrik extract that holds the box once into `cache_dir` (under its dated name)
and reads the lines in the box. `extract` names a Geofabrik URL or a local `.pbf` instead.
`lines.attrs["extract"]` holds the extract's file name (which carries its date) and
`attrs["credit"]` the ODbL credit. Which lines count: [Confidence grade](confidence.md#the-road-crossing-fix).

### `sources.osm.split_lines(lines)`

`(barriers, streams, bridges)` from the lines of `fetch_lines`, the three layers
`breach_crossings` also accepts as a tuple.

### `sources.osm.extract_for(bbox, *, cache_dir)`

`(id, url)` of the smallest Geofabrik extract whose area holds the whole box (Geofabrik's index
is kept in `cache_dir`). Raises `ValueError` when none does.

## Context: `h3lowpoints.context.readers` (needs the `sources` extra)

### `context.readers.read_drains(path, crs)`

Drain points from a GeoPackage, GeoJSON or Shapefile, or a CSV with `lon`/`lat`,
`longitude`/`latitude` (degrees) or `x`/`y` (already in `crs`) columns. Returns an (n, 2) array of
x, y in `crs`, ready for `drains_xy`.

### `context.readers.read_buildings(path, crs)`

Building footprints from a vector file, reprojected to `crs`, polygons only, as a GeoDataFrame
ready for `buildings`.

## Maps: `h3lowpoints.viz`

### `viz.webmap.build_map(cells, depressions, meta, path, *, footprint=None, title=None, subtitle=None, max_cells=120000, max_points=20000, n_levels=6)`

The engine behind `Result.to_html`, for tables you hold separately. With `footprint`, coarser
levels get coverage too. `subtitle` replaces the line describing the data. When `cells` is a
roll-up of the analysis, the map labels its finest level as rolled up from the analysis
resolution.

### `viz.webmap.map_from_dir(folder, path, **kwargs)`

The same from a saved result folder.

### `viz.deck.cells_to_geojson(cells, path=None, properties=None)`

A GeoJSON FeatureCollection of cell outlines (the H3 id becomes each feature's `id`; `properties`
lists the columns to include, default all). Writes it to `path` when given. Needs no extra
package.

### `viz.deck.cells_deck(cells, value="max_depth_m", elevation_scale=20.0, hover=(), pitch=45)`

The engine behind `Result.to_deck`, for any cell table (needs the `viz` extra).

## Lower-level helpers

| Function | Purpose |
|---|---|
| `h3lowpoints.resolution.finest_h3_resolution(pixel_m)` | Finest resolution whose cells hold at least four pixels of this size. |
| `h3lowpoints.resolution.resolution_tier(pixel_m)` | `"fine"`, `"medium"` or `"coarse"`. |
| `h3lowpoints.store.read_table(folder, name)` / `iter_table(folder, name, rows=500000, columns=None)` | One table of a saved result, whole or in pieces (H3 ids as text); `columns` reads only those of the named columns the table has. |
| `h3lowpoints.store.iter_file(path, rows=500000, columns=None)` | The same pieces from any `.csv` or `.parquet` table file. |
| `h3lowpoints.store.write_table(df, path, fmt="csv")` | Write a table as CSV or Parquet. |
| `h3lowpoints.hydro.depressions.depth_from_array(arr, nodata_mask=None, drains=None)` | The depth raster alone (priority-flood fill minus ground). `drains` is a boolean raster of extra outlets. |
| `Result.footprint.coverage(cells)` | Coverage and covered area of any list of H3 cells (any resolution) over this raster. |
