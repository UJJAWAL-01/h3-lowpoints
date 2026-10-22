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

### `analyze(dem_path, *, res="auto", min_depth=0.15, mask=None, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False)`

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
| `catchments` | Also follow the water ([rules](definition.md#catchments-and-spill)): adds `catchment_area_m2`, `spill_to`, `spill_lat` and `spill_lon` to the depression table and fills `Result.catchments` (default `False`). Not yet available in `analyze_tiled`. |

Returns a `Result`. Raises `ValueError` when the raster has no CRS, a CRS that is neither
projected nor in degrees, a rotated or sheared grid, latitudes beyond ±90°, or an invalid `res`.
Warns when `res` is finer than the data supports, or when `min_depth` is below 1 m on data coarser
than 2 m.

### `analyze_array(arr, transform, crs, nodata=None, *, res="auto", min_depth=0.15, mask=None, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8, catchments=False, source="array")`

The same as `analyze` for an elevation array already in memory. `transform` is the raster's
affine transform (as from rasterio), `crs` anything rasterio or pyproj accept (such as
`"EPSG:32614"`), `nodata` the value marking missing pixels, and `source` the name recorded as
`meta["dem"]`.

### `analyze_tiled(dem_path, *, tile=4096, out_dir=None, format="csv", hours=None, mask_path=None, verbose=False, res="auto", min_depth=0.15, drains_xy=None, buildings=None, drain_radius_m=5.0, drain_core_frac=0.8)`

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
`h3_lowpoint` cell is kept. Reads and writes the cell table in pieces, so any size can be
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
| `save(folder, format="csv")` | Write `depressions`, `cells` (and `catchments`, when present) and `meta.json`; `format` is `"csv"` or `"parquet"`. Returns the folder. |
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

## Verification: `h3lowpoints.verify`

See [Verification](verification.md) for what each check means.

| Function | Returns |
|---|---|
| `check_result(result)` | List of problems (empty when every invariant holds). Works on results in memory and on disk. |
| `check_tables(depressions, cells, meta)` | The same for three tables. |
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
pixel is 1 (water) when at least 5 of 9 evenly spaced points inside it fall on class 80,
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
| `h3lowpoints.store.write_table(df, path, fmt="csv")` | Write a table as CSV or Parquet. |
| `h3lowpoints.hydro.depressions.depth_from_array(arr, nodata_mask=None, drains=None)` | The depth raster alone (priority-flood fill minus ground). `drains` is a boolean raster of extra outlets. |
| `Result.footprint.coverage(cells)` | Coverage and covered area of any list of H3 cells (any resolution) over this raster. |
