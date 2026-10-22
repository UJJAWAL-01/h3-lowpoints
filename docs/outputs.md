# Outputs

An analysis produces three things: a **depression table** (one row per depression), a **cell
table** (one row per H3 cell that holds depressed ground) and **meta** (every setting and version
used). In Python they are `Result.depressions`, `Result.cells` and `Result.meta`; on disk they are
three files in a folder. This page describes every file, column and field.

## Files

| File | Written by | Content |
|---|---|---|
| `depressions.csv` or `depressions.parquet` | every saved result | the depression table |
| `cells.csv` or `cells.parquet` | every saved result | the cell table |
| `catchments.csv` or `catchments.parquet` | results computed with `catchments=True` (`--catchments`) | the catchment table |
| `depression_cells.csv` or `depression_cells.parquet` | results computed with `details=True` (`--details`) | the depression × cell table |
| `meta.json` | every saved result | settings, versions, counts |
| `dem.vrt` | `h3lowpoints build` | the virtual raster the region was analyzed from (a small text file pointing at the elevation tiles) |
| `verify.json` | `h3lowpoints build` | `{"problems": [...]}`, empty when every check passed |
| `_work/` | a disk-backed run, until it finishes | every finished tile's result (including a compressed record of its invalid pixels, for coverage) and the run's settings (`manifest.json`), so an interrupted run can continue; removed once the tables are written |
| `_spill/`, `_spill_catchments/` | a disk-backed run, while it writes the tables | temporary per-cell sums; removed when the run ends, even if it fails |

**Format.** CSV is the default and opens anywhere. Parquet (`format="parquet"`, needs
`pip install h3-lowpoints[parquet]`) is typically several times smaller, keeps exact column types
and reads much faster; it is the better choice for large regions. A folder holds one format: saving
in the other format replaces the old files.

**CSV details.** Comma-separated with a header row, no index column, booleans as `True`/`False`,
numbers written with enough digits to read back exactly.

**Parquet details.** Compressed with zstd. Column types: text for H3 ids, 64-bit floats for
measurements, 64-bit integers for `id`, `n_lowpoints` and `spill_to`, booleans for flags.

**Row order.** Depressions are ordered by the position of their first pixel in reading order
(top row first, left to right). Cells are ordered by ascending H3 id (as a 64-bit integer), which
groups neighbouring cells under the same coarse parent together.

> **Reading the CSV files yourself:** read the H3 columns as text. Some H3 ids look like numbers
> in scientific notation: tools that guess column types, pandas among them, read the
> resolution-14 id `8e4516503458447` as `8 × 10^4516503458447`, i.e. infinity, and the id is lost.
> About one resolution-14 id in ten thousand is affected. In pandas use
> `pd.read_csv(path, dtype={"h3": str})` (and the same for `h3_lowpoint`, `h3_lowpoint_15`), or
> use `h3lowpoints.read_result`, which does this for you. Parquet has no such problem.

## The depression table

One row per depression. The first ten columns are always present; the others appear only when
the data behind them was supplied.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `id` | integer | | Identifier of the depression within this result. |
| `max_depth_m` | float | m | Depth at the deepest point: filled level minus ground height. Always at least `min_depth`. |
| `area_m2` | float | m² | Ground area of the depression: the sum of its pixels' ground areas. |
| `volume_m3` | float | m³ | Water it holds when full: the sum over its pixels of depth × pixel area. |
| `lat`, `lon` | float | degrees (WGS 84) | Centre of the deepest pixel. |
| `h3_lowpoint` | text | | H3 cell, at the table's resolution, containing (`lat`, `lon`). Matches a row of the cell table. |
| `h3_lowpoint_15` | text | | Resolution-15 H3 cell containing (`lat`, `lon`): the lowest point to the finest precision H3 has. |
| `edge` | boolean | | True when the depression comes within one pixel of the raster's edge: it may continue beyond the data, so its area and volume are lower bounds. |
| `deeper_than_wide` | boolean | | True when the depression is deeper than the side of a square of its own area: almost always an error in the elevation data (see below). |
| `dist_to_drain_m` | float | m | Only with drains: distance from the lowest point to the nearest drain. |
| `drain_at_core` | boolean | | Only with drains: a drain lies within `drain_radius_m` of the depression's deep core. |
| `building_overlap` | float | 0 to 1 | Only with buildings: share of the depression's pixels under building footprints. |
| `likely_building_artifact` | boolean | | Only with buildings: `building_overlap` is at least 0.5. |
| `catchment_area_m2` | float | m² | Only with `catchments=True`: ground area whose rain ends up in this depression, including the depression itself and the land draining to shallow sinks that spill into it. |
| `spill_to` | integer | | Only with `catchments=True`: `id` of the depression that receives this one's overflow when it is full, or 0 when the overflow leaves the map. |
| `spill_lat`, `spill_lon` | float | degrees (WGS 84) | Only with `catchments=True`: centre of the pixel where the full depression overflows. |
| `ground_m` | float | m | Only with `details=True`: ground height at the lowest point. |
| `water_level_m` | float | m | Only with `details=True`: water level when full; `water_level_m - ground_m = max_depth_m`. |
| `length_m`, `width_m` | float | m | Only with `details=True`: axes of the depression's equivalent ellipse; `width_m <= length_m`. |
| `orientation_deg` | float | degrees | Only with `details=True`: direction of the long axis, clockwise from north, 0 to 180. |
| `area_10cm_down_m2`, `area_25cm_down_m2`, `area_50cm_down_m2`, `area_1m_down_m2`, `area_2m_down_m2`, `area_5m_down_m2` | float | m² | Only with `details=True`: area under water when the water stands 0.1, 0.25, 0.5, 1, 2 or 5 m below full. |
| `volume_10cm_down_m3`, `volume_25cm_down_m3`, `volume_50cm_down_m3`, `volume_1m_down_m3`, `volume_2m_down_m3`, `volume_5m_down_m3` | float | m³ | Only with `details=True`: water held at the same six levels. |

How each value is computed, and the edge cases:

- **`id`.** From `analyze`, the label the depression had when the raster was labelled; labels of
  depressions dropped by `min_depth` are not reused, so ids can have gaps. From `analyze_tiled`
  (and `build`), 1 to n in order. In both cases the order is the reading order of each
  depression's first pixel. An id identifies a row within one result only; use `h3_lowpoint_15`
  or `lat`/`lon` to match depressions across runs.
- **Depth units.** Heights are taken to be metres. If a DEM stores heights in feet, depths and
  volumes come out in feet (and feet × m²) although the columns say metres; convert such a DEM
  first. See [Accuracy and limits](limitations.md).
- **Pixel areas.** On a projected grid every pixel has the same area (pixel width × height,
  converted to metres when the CRS is in feet). On a longitude/latitude grid each row's pixel area
  is computed exactly on the WGS 84 ellipsoid.
- **Lowest point.** The pixel with the greatest depth. If several pixels share it (within
  1 nanometre), the first in reading order is used, so the result does not depend on how the
  raster was tiled.
- **`h3_lowpoint` vs the parent of `h3_lowpoint_15`.** H3 cells do not nest exactly, so near a
  cell edge the coarse parent of `h3_lowpoint_15` can differ from `h3_lowpoint`. `h3_lowpoint` is
  the one consistent with the cell table (both use the point's exact position).
- **Precision of the lowest point.** `h3_lowpoint_15` is about 1 m across, but the position is
  only as precise as the elevation pixel it came from: on 30 m data it marks a 1 m cell inside a
  30 m pixel.
- **`edge`.** Measured on the depression's bounding box against the raster border. Nodata inside
  the raster is not an edge: water can leave there.
- **`deeper_than_wide`.** `max_depth_m > sqrt(area_m2)`. Natural hollows, and even open-pit
  mines, are far wider than they are deep; a depression this narrow is almost always a spike of
  bad pixels or a strip stored as 0 m in the elevation data. In the 3DEP 30 m national run, 43 of
  10.6 million depressions were flagged, up to 620 m deep, and they dominate any "deepest"
  figure: filter them out for depth statistics. Nothing is removed; the flag only marks them.
- **Drains.** `dist_to_drain_m` is geodesic (on the WGS 84 ellipsoid) for degree grids and
  straight-line for projected grids, to the nearest of all drains supplied. The **core** of a
  depression is its pixels at least `drain_core_frac` (default 0.8) of its maximum depth deep.
  "Within `drain_radius_m`" (default 5 m) uses whole pixels, rounded up, in a square
  neighbourhood.
- **Buildings.** Footprints are burned onto the pixel grid (a pixel counts when its centre is
  inside a footprint). On projected grids footprints under 20 square map units are ignored (20 m²
  in metre-based CRSs), to skip sheds and slivers; on degree grids none are ignored.
- **Catchments and spill.** The rules are in
  [Definition](definition.md#catchments-and-spill). `catchment_area_m2` is at least `area_m2`.
  Following `spill_to` from any depression always reaches 0, through depressions with ever lower
  water levels. The catchment areas of all depressions plus the area draining straight off the
  map equal the area of valid data.
- **Levels, shape and fill curve.** The rules are in
  [Definition](definition.md#levels-shape-and-fill-curve). The flooded areas and volumes never
  grow as the water drops (`area_m2 >= area_10cm_down_m2 >= ... >= area_5m_down_m2`, the same for
  volumes), are 0 once the drop reaches `max_depth_m`, and each volume is at most the depth left
  times the flooded area.

## The cell table

One row per H3 cell that contains at least one pixel of a depression that was kept. Cells with no
depressed pixel are not listed.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `h3` | text | | The cell's H3 id. All rows have the same resolution, `Result.res`. |
| `max_depth_m` | float | m | Greatest depth over the depressed pixels in the cell. |
| `depress_area_m2` | float | m² | Ground area of the depressed pixels in the cell. |
| `depress_volume_m3` | float | m³ | Water held by the depressed pixels in the cell. |
| `n_lowpoints` | integer | | Number of depressions whose lowest point (`h3_lowpoint`) is this cell. |
| `covered_area_m2` | float | m² | `coverage` × the cell's exact area. |
| `coverage` | float | 0 to 1 | Share of the cell the elevation data covers. |
| `depressed_share` | float | 0 to 1 | `depress_area_m2` / `covered_area_m2`, capped at 1. |

Details:

- **Which pixels count.** A pixel belongs to the H3 cell containing its centre. A depression
  larger than a cell contributes to every cell its pixels fall in, and a cell can hold parts of
  several depressions.
- **Roll-up.** `Result.at_resolution(r)` gives the table at any coarser resolution `r`:
  `max_depth_m` is the maximum of the children, area, volume and `n_lowpoints` add up, and
  coverage is measured afresh from the data footprint (it cannot be added up, because the finer
  table lists only cells with depressions).
- **Coverage.** The area of the cell's pixel positions that hold valid data, divided by the area
  of all pixel positions inside the cell (by the same centre rule), where positions beyond the
  raster count as not covered. Nodata, NaN and masked pixels are not covered. Cells wholly inside
  valid data have coverage exactly 1. A cell that touches the edge or nodata and whose bounding
  box spans more than 40,000 pixels is measured on a regular sample of those pixels, with an
  error well under 1%.
- **`depressed_share`.** The two areas come from different counts (the exact H3 area against a
  sum of whole pixels), so for a cell that is almost entirely depressed the ratio could exceed 1
  by a fraction of a percent; it is capped at 1.
- **Exactness.** The sums in the cell table equal the sums in the depression table (area and
  volume), and `n_lowpoints` adds up to the number of depressions. `h3lowpoints verify` checks
  this for any saved result.

## The catchment table

Only with `catchments=True`: one row per depression and H3 cell of its catchment, for the
depressions in the depression table (land draining off the map is not listed).

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `id` | integer | | The depression (`id` in the depression table). |
| `h3` | text | | An H3 cell at the table's resolution, `Result.res`. |
| `area_m2` | float | m² | Ground area of the cell's pixels whose rain ends up in this depression. |

A pixel belongs to the cell containing its centre. The rows of one depression add up to its
`catchment_area_m2`. A cell on a divide appears once for each depression it drains to. Rows are
ordered by H3 cell (as a 64-bit integer) and then by `id`.

**Clipped results.** `h3lowpoints clip` keeps, for every depression it keeps, its whole
catchment, including cells beyond the boundary: rain falling across a border still ends up in the
depression. A kept depression's `spill_to` may name a depression outside the boundary, which is
not in the clipped table. Land
cover, imperviousness, soils or rainfall can be attached to a catchment through these cells
without analysing the terrain again.

## The depression × cell table

Only with `details=True`: one row per depression and H3 cell it covers, ordered by cell and
then `id`. It splits the cell table by depression, and adds the fill curve per cell, so the
flooded part of each cell can be found for any water level of its depression.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `id` | integer | | The depression. |
| `h3` | text | | An H3 cell at the table's resolution. |
| `area_m2`, `volume_m3` | float | m², m³ | The depression's area and volume in this cell. |
| `max_depth_m` | float | m | The depression's greatest depth in this cell. |
| `area_10cm_down_m2`, `area_25cm_down_m2`, `area_50cm_down_m2`, `area_1m_down_m2`, `area_2m_down_m2`, `area_5m_down_m2` | float | m² | Its area in this cell under water when the water stands 0.1, 0.25, 0.5, 1, 2 or 5 m below full. |

The rows of one depression add up to its `area_m2`, `volume_m3` and fill-curve areas, and their
deepest `max_depth_m` is the depression's. Summed over the depressions in a cell, area and
volume give the cell table's `depress_area_m2` and `depress_volume_m3`. A clipped result keeps
every row of the depressions it keeps.

## meta.json

| Field | Meaning |
|---|---|
| `package_version`, `h3_version` | Versions of h3-lowpoints and of the H3 library used. |
| `dem` | Path (or name) of the elevation data analyzed. |
| `crs` | Its coordinate reference system. |
| `shape` | `[rows, columns]` of the raster. |
| `cell_size_m` | Typical pixel size in metres (the side of a square with the area of a pixel in the middle row). |
| `geographic` | True for a longitude/latitude grid. |
| `resolution_tier` | `fine` (pixels up to 2 m), `medium` (up to 12 m) or `coarse`. |
| `res_requested` | The resolution asked for, such as `"auto"` or `12`. |
| `params.res` | The resolution used (`Result.res`). |
| `params.min_depth` | Shallowest depression kept, metres. |
| `params.drain_radius_m`, `params.drain_core_frac` | Drain settings (used only with drains). |
| `n_drains` | Number of drains supplied (0 for none). |
| `n_buildings` | Number of building footprints supplied, or `null` when none were. |
| `n_depressions` | Rows in the depression table. |
| `n_edge` | Depressions with `edge = True`. |
| `tiling` | Tiled runs only: `tile` (tile size in pixels), `n_tiles`, `n_border_cells` and `n_crossings` (the size of the graph joining the tiles; see [Large regions](large-regions.md)). |
| `output` | Saved results: `format` (`csv` or `parquet`) and `n_cells` (rows in the cell table). |
| `clip` | Clipped results only: `boundary`, the membership `rule`, and `depressions_before` and `cells_before` (the counts before clipping). |
| `build` | `h3lowpoints build` only: `dataset` (`3dep` or `3dep10`), `bbox`, and `seconds` for downloading tiles and for the analysis. |

Example (from `examples/quickstart.py`):

```json
{
  "res_requested": 12,
  "package_version": "0.0.1",
  "h3_version": "4.5.0",
  "dem": "my_first_run/dem.tif",
  "crs": "EPSG:32614",
  "shape": [300, 300],
  "cell_size_m": 1.0,
  "geographic": false,
  "resolution_tier": "fine",
  "params": {"res": 12, "min_depth": 0.15, "drain_radius_m": 5.0, "drain_core_frac": 0.8},
  "n_drains": 1,
  "n_buildings": 1,
  "n_depressions": 3,
  "n_edge": 0,
  "output": {"format": "csv", "n_cells": 13}
}
```

Keep `meta.json` with the tables: it is what makes a result reproducible and checkable.

## Opening results in other tools

**pandas**

```python
from h3lowpoints import read_result
res = read_result("results")              # CSV or Parquet; H3 ids kept as text
res.cells.sort_values("depressed_share", ascending=False).head()
```

**DuckDB**

```sql
SELECT * FROM read_parquet('results/cells.parquet') ORDER BY depress_volume_m3 DESC LIMIT 10;
SELECT * FROM read_csv('results/cells.csv', types={'h3': 'VARCHAR'});
```

**QGIS, kepler.gl, deck.gl (cell outlines)**

```python
from h3lowpoints.viz.deck import cells_to_geojson
cells_to_geojson(res.cells, "cells.geojson")    # one polygon per cell, columns as properties
```

Open `cells.geojson` in QGIS, or drag it into kepler.gl. kepler.gl can also draw the cell table
directly: add an H3 layer and choose the `h3` column. The depression table can be loaded as
points from its `lon` and `lat` columns.

**GeoPandas**

```python
import geopandas as gpd
import h3
from shapely.geometry import Polygon

outline = [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in res.cells["h3"]]
cells = gpd.GeoDataFrame(res.cells, geometry=outline, crs=4326)
```
