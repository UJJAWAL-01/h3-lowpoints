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

### `analyze(dem_path, *, res="auto", min_depth=0.15, mask=None, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False, details=False, workers=1)`

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
| `workers` | Label pixels with their H3 cells in this many processes (default 1). The result is identical; only the time changes (labelling was 3× faster with 4 processes on a laptop with 4 cores; more did not help). Processes are started on first use, so on Windows and macOS a script must call the analysis from under `if __name__ == "__main__":`. |

Returns a `Result`. Raises `ValueError` when the raster has no CRS, a CRS that is neither
projected nor in degrees, a rotated or sheared grid, latitudes beyond ±90°, or an invalid `res`.
Warns when `res` is finer than the data supports, or when `min_depth` is below 1 m on data coarser
than 2 m.

### `analyze_array(arr, transform, crs, nodata=None, *, res="auto", min_depth=0.15, mask=None, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False, details=False, workers=1, source="array")`

The same as `analyze` for an elevation array already in memory. `transform` is the raster's
affine transform (as from rasterio), `crs` anything rasterio or pyproj accept (such as
`"EPSG:32614"`), `nodata` the value marking missing pixels, and `source` the name recorded as
`meta["dem"]`.

### `analyze_tiled(dem_path, *, tile=4096, out_dir=None, format="csv", hours=None, mask_path=None, verbose=False, res="auto", min_depth=0.15, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False, details=False, workers=1)`

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

| Method | Does |
|---|---|
| `at_resolution(res)` | The cell table at a coarser resolution: exact roll-up, coverage measured afresh. |
| `save(folder, format="csv")` | Write `depressions`, `cells` (and `catchments` and `depression_cells`, when present) and `meta.json`; `format` is `"csv"` or `"parquet"`. Returns the folder. |
| `load()` | For a result written to disk: a copy with its tables read back. |
| `to_html(path, title=None, max_cells=120000, max_points=20000, n_levels=6)` | Write the interactive web map ([The map](map.md)). Resolutions with more than `max_cells` cells are left out (coarser roll-ups are drawn instead); the deepest `max_points` depressions can be marked; `n_levels` resolutions are offered. Returns the path. |
| `to_deck(value="max_depth_m", elevation_scale=20.0, hover=(), pitch=45)` | A pydeck map for notebooks (needs the `viz` extra): extruded hexagons whose height and colour follow `value`. |

## Validation against observations

See [Validation](validation.md) for the method, how to choose controls and how to read the
result.

### `validate(result, observations, controls=None, *, boundary=None, n_random=None, max_rings=5, depth_classes=(0.5, 1.0, 2.0), block_res=6, n_boot=1000, ci=0.95, seed=0, exclude_suspect=True, rows=500000)`

How much more often `observations` fall in or near depressed ground than `controls` do.

| Parameter | Meaning |
|---|---|
| `result` | A `Result`, or a folder written by `analyze`, `build`, `clip` or `Result.save` (read `rows` at a time, so the national dataset works). |
| `observations`, `controls` | Points in any form `validation.read_points` accepts. Without `controls`, `n_random` random points in `boundary` (default 10 per observation, at least 1000), with a warning. |
| `boundary` | The area studied: a shapely geometry in longitude/latitude or a vector file. Points outside it are left out. Without it, a `build` result's box is used if recorded. |
| `max_rings` | Largest distance, in H3 rings of the result's resolution. |
| `depth_classes` | Edges in metres of the depth classes of `by_depth`. |
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
| `points` | One row per point used: `point_id`, `kind`, `lon`, `lat`, `h3`, `rings`, `depth_m`, `block`. |
| `report` | Everything, with settings, counts and warnings, as a JSON-ready dict ([fields](validation.md#the-report)). |
| `warnings` | The list of caveats. |
| `summary()` | The ring table as text. |
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

## Susceptibility: `h3lowpoints.susceptibility`

See [Susceptibility](susceptibility.md) for the method and [Outputs](outputs.md#the-susceptibility-table)
for the columns.

### `susceptibility.susceptibility(result, *, curve_numbers=None, curve_number=None, impervious=None, rain=None, ratio_years=(2, 10, 100), min_coverage=0.9, cache_dir=None, rows=500000, workers=1)`

One row per depression: its curve number, the rain that fills it and, with storms, how full they
leave it. Returns a DataFrame whose `attrs["settings"]` hold the settings for `meta.json`.

| Parameter | Meaning |
|---|---|
| `result` | A `Result` computed with `catchments=True`, or its folder (the catchment table is read `rows` at a time). |
| `curve_numbers` | SCS curve numbers: a raster in any CRS (its mean per H3 cell is used; in the US, `runoff.fetch_curve_numbers`), a `.csv` or `.parquet` table with columns `h3` and `curve_number` at the result's resolution (read in pieces; must be sorted by `h3`), or a DataFrame with those columns. |
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

### `runoff.fetch_curve_numbers(bbox, out_path, *, cache_dir, year=2021)`

Curve numbers (float32, NaN for no data) for a box in the lower 48 US states, on NLCD's 30 m
grid, from NLCD land cover and imperviousness of `year` and the gNATSGO soil groups. The three
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

### `drainage.cells_along(lines, crs, res, step=None)`

The H3 cells (as integers) at resolution `res` that lines in projected `crs` pass through, found
by points every `step` metres along them (default a third of the cells' edge).

## Verification: `h3lowpoints.verify`

See [Verification](verification.md) for what each check means.

| Function | Returns |
|---|---|
| `check_result(result)` | List of problems (empty when every invariant holds). Works on results in memory and on disk. |
| `check_tables(depressions, cells, meta, catchments=None, depression_cells=None, susceptibility=None)` | The same for the tables (the susceptibility table with its index and drainage columns). |
| `check_saved(folder, rows=500000)` | The same for a saved folder, reading the cell table `rows` at a time so any size can be checked. |
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

### `sources.landcover.fetch_impervious(bbox, out_path, *, year=2021, max_area_km2=20000.0, data=None)`

NLCD percent impervious surface (lower 48 US states, 30 m) as a GeoTIFF in EPSG:5070, values 0 to
100 and 255 for no data. `year` is one of `sources.landcover.NLCD_YEARS`. `data` supplies the
service's GeoTIFF bytes yourself (for tests or a cache).

### `sources.landcover.fetch_land_cover(bbox, out_path, *, year=2021, max_area_km2=20000.0, data=None)`

NLCD land cover classes (`sources.landcover.NLCD_CLASSES`; 0 for no data) for a box, at 30 m in
NAD83 / Conus Albers. Lower 48 US states only.

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

### `sources.rainfall.atlas14_grid_url(volume, years, duration="24h")`

The URL of one of NOAA's zipped Atlas 14 grids.

### `sources.rainfall.read_ascii_grid(data)`

Reads an ESRI ASCII grid (the bytes of an `.asc` file, or of a zip holding one): returns
`(values, west, north, cell)`, with NaN for no data.

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
