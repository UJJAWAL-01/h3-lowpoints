# h3-lowpoints

Find closed depressions ("low points") in an elevation model and map them on [H3](https://h3geo.org)
cells at any resolution, ready for 3D visualization. An optional add-on screens depressions that
may be waterlogging zones, to help prioritize drainage review.

> **Status: alpha (0.x).** The depression detection is tested against an independent method and
> across tilings. The waterlogging add-on is a heuristic validated on one city; see
> [what it does and does not show](docs/waterlogging.md). It is research output, **not a
> flood-risk map**, and says nothing about any specific property.

## Install

Not on PyPI yet. From a clone:

```
pip install -e .                  # core
pip install -e ".[viz]"           # + 3D maps (pydeck)
pip install -e ".[sources]"       # + reading drain / building files, USGS and Austin downloaders
```

## Quick start

```python
from h3lowpoints import analyze

res = analyze("dem.tif")          # DEM in a projected CRS with metre units
res.depressions.head()            # one row per depression
res.cells.head()                  # one row per H3 cell
res.save("outputs/my_area")       # depressions.csv, cells.csv, meta.json
```

A DEM in geographic coordinates (for example Copernicus GLO-30) needs reprojecting first:
`h3lowpoints.reproject_to_utm("dem_4326.tif", "dem_utm.tif")`.

### See it in 3D and at any H3 resolution

```python
res = analyze("dem.tif", res=12)  # analyze once at a fine resolution ...
coarse = res.at_resolution(9)     # ... then roll up: depth = max, area and volume add up
res.to_deck().to_html("map.html") # extruded hexagons; height and colour follow max depth
```

Roll-up is lossless for depth, area and volume, so zooming between resolutions never needs a
new analysis. `h3lowpoints.viz.deck.cells_to_geojson(cells, "cells.geojson")` writes the cells
for deck.gl, kepler.gl or QGIS without any extra dependency.

## What counts as a depression

The DEM is filled with the priority-flood algorithm (water leaves over the raster edge, into
nodata, or at a supplied drain). Fill depth minus original elevation is depression depth, and an
8-connected region of positive depth at least `min_depth` (default 0.15 m) deep is one
depression. Details, including how nested basins and raster edges are treated, are in
[docs/definition.md](docs/definition.md).

## Which DEMs it suits

| DEM cell size | Examples | Depression detection | Waterlogging add-on |
|---|---|---|---|
| 2 m or finer | USGS 3DEP 1 m, national lidar programs | Full | On by default (the only tier validated) |
| 2 to 12 m | 3DEP 1/3 arc-second (~10 m) | Large depressions only; a warning is raised | Off by default |
| coarser | Copernicus GLO-30, FABDEM | Large basins only; vertical error often exceeds `min_depth` | Off by default |

Distances are in metres, so the same settings mean the same thing at any resolution. The library
warns when the requested H3 resolution is too fine for the DEM (cells with fewer than four
pixels) or when `min_depth` is below what a coarse DEM can resolve.

## Optional context

```python
res = analyze("dem.tif", drains_xy=drains, buildings=footprints)
```

`drains_xy` (an array of x, y in the DEM's CRS) adds `dist_to_drain_m` and `drain_at_core`.
`buildings` (polygons in the same CRS) adds `building_overlap` and `likely_building_artifact`,
because lidar often shows false depressions under buildings. These columns appear only when you
supply the data.

## Large rasters

```python
from h3lowpoints import analyze_tiled

res = analyze_tiled("big_dem.tif", tile=4096, buffer=256)
```

A tile sees its border as an outlet, so a lake that really spills beyond the buffer is seen at a
lower level. Depressions whose water level depends on a tile border are flagged `edge`, never
returned as final. The flag is conservative: it marks everything that *could* change with more
data. Raise `buffer` to resolve more of them (see `docs/definition.md`).

## Command line

```
h3lowpoints analyze dem.tif --drains drains.csv --buildings buildings.gpkg --out results/
h3lowpoints analyze dem.tif --res 12 --no-waterlogging --tile 4096
h3lowpoints analyze dem_4326.tif --reproject
```

Drains: a point file (GeoPackage, GeoJSON, Shapefile) or a CSV with `x,y` (in the DEM's CRS) or
`lon,lat` columns. Buildings: a polygon file. Outputs are described in
[docs/outputs.md](docs/outputs.md).

## Limits

- Depressions are a property of the terrain model. Ponds, quarries and street sags are all
  depressions; the library does not know which ones hold water or cause problems.
- Lidar artifacts (notably under buildings) produce false depressions; pass `buildings=` to flag them.
- The waterlogging classes come from one city and one lidar project. Nothing here shows they
  transfer elsewhere. Read [docs/waterlogging.md](docs/waterlogging.md) before relying on them.
- Tiled results can differ from whole-raster results for depressions flagged `edge`.

## Project

- [docs/definition.md](docs/definition.md): precisely what is computed
- [docs/outputs.md](docs/outputs.md): columns and files
- [docs/waterlogging.md](docs/waterlogging.md): the add-on and its evidence
- [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md)

## Data and licensing

Code is Apache-2.0. USGS 3DEP elevation data is public domain. Check each City of Austin
dataset's own license before redistributing derived data. Source data is not included here.
