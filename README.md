# h3-lowpoints

Find closed depressions ("low points") in an elevation model and map them on [H3](https://h3geo.org)
cells at any resolution, ready for 3D visualization.

> **Status: alpha (0.x).** The depression detection is checked against an independent method and
> across tilings. A depression is a feature of the terrain model: this library does not say
> whether water collects there, and it is **not a flood-risk map**. Where the project is going
> (global coverage, a susceptibility layer) is in [docs/roadmap.md](docs/roadmap.md).

## Install

Not on PyPI yet. From a clone:

```
pip install -e .                  # core
pip install -e ".[viz]"           # + 3D maps (pydeck)
pip install -e ".[sources]"       # + reading drain / building files and the USGS 3DEP downloader
```

## Quick start

```python
from h3lowpoints import analyze

res = analyze("dem.tif")          # DEM in a projected CRS with metre units
res.depressions.head()            # one row per depression
res.cells.head()                  # one row per H3 cell
res.save("outputs/my_area")       # depressions.csv, cells.csv, meta.json
```

A DEM in latitude and longitude (for example Copernicus GLO-30) is analyzed as it is: pixel areas
are computed exactly on the WGS 84 ellipsoid, so there is no need to resample it to UTM (which
blurs sharp edges). `h3lowpoints.reproject_to_utm` exists if you want a projected copy anyway.

### See it on a map

```python
res = analyze("dem.tif", res=10)
res.to_html("map.html")           # one self-contained interactive page: open it in a browser
```

```
h3lowpoints analyze dem.tif --res 10 --map map.html      # the same from the command line
h3lowpoints map results_folder --out map.html            # from results you saved earlier
python examples/gallery.py                               # five real places, with an index page
```

The map draws the H3 cells in 3D over a basemap (light, dark or satellite). You can switch between
the analysis resolution and every coarser roll-up, choose what colour and height show (deepest
depth, volume, area, share of the cell that is depressed, or how much of the cell the DEM covers),
and mark each depression's lowest point. It is one HTML file with the data inside it; it needs an
internet connection for the basemap and the deck.gl library, and no server or account. Settings
can go in the link (`map.html#res=6&metric=volume&base=satellite`) so you can share a view.

### Any H3 resolution

```python
res = analyze("dem.tif", res=12)  # analyze once at a fine resolution ...
coarse = res.at_resolution(9)     # ... then roll up: depth = max, area and volume add up
```

Roll-up is exact for depth, area and volume, so zooming between resolutions never needs a new
analysis. `h3lowpoints.viz.deck.cells_to_geojson(cells, "cells.geojson")` writes the cells for
deck.gl, kepler.gl or QGIS without any extra dependency, and `res.to_deck()` makes a pydeck map
inside a notebook.

### How much of each cell the DEM covers

Every cell has `coverage` (0 to 1) and `covered_area_m2`. A cell at the edge of the DEM, or beside a
masked lake, holds depressions from only part of its area, so compare cells by their share of the
covered area, not by raw totals. `at_resolution` measures coverage afresh at each resolution.

## Global data

```
h3lowpoints fetch-dem --bbox -97.9 30.1 -97.6 30.4 --out austin.tif   # Copernicus GLO-30, 30 m
h3lowpoints analyze austin.tif --res 9 --min-depth 1
```

`fetch-dem` reads the public Copernicus GLO-30 elevation data from AWS open data (no account).
It is a surface model with a vertical error of a few metres: it shows large basins, such as the
below-sea-level polders around Rotterdam, not street-level ponding. Use a lidar DEM for that.
More in `h3lowpoints.sources.copernicus`.

## What counts as a depression

The DEM is filled with the priority-flood algorithm (water leaves over the raster edge, into
nodata, or at a supplied drain). Fill depth minus original elevation is depression depth, and an
8-connected region of positive depth at least `min_depth` (default 0.15 m) deep is one
depression. Details, including how nested basins and raster edges are treated, are in
[docs/definition.md](docs/definition.md).

## Which DEMs it suits

| DEM cell size | Examples | What it resolves |
|---|---|---|
| 2 m or finer | USGS 3DEP 1 m, national lidar programs | Street-scale depressions |
| 2 to 12 m | 3DEP 1/3 arc-second (~10 m) | Large depressions only; a warning is raised |
| coarser | Copernicus GLO-30, FABDEM | Large basins only; vertical error often exceeds `min_depth` |

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

res = analyze_tiled("big_dem.tif", tile=4096)
```

The result is exactly what `analyze` would give, whatever the tile size: the tiles only decide
how much memory you need. Water that crosses a tile border, and depressions that span several
tiles, are handled exactly rather than approximately. On 39 million pixels of real terrain,
1024-cell tiles took 30 s against 28 s for the whole raster and needed about a fifth of the memory
(0.3 GB against 1.4 GB). How it works and how it is tested: [docs/definition.md](docs/definition.md).

## Command line

```
h3lowpoints analyze dem.tif --drains drains.csv --buildings buildings.gpkg --out results/
h3lowpoints analyze dem.tif --res 12 --tile 4096
h3lowpoints analyze dem_4326.tif --reproject
```

Drains: a point file (GeoPackage, GeoJSON, Shapefile) or a CSV with `x,y` (in the DEM's CRS) or
`lon,lat` columns. Buildings: a polygon file. Outputs are described in
[docs/outputs.md](docs/outputs.md).

## Limits

- Ponds, quarries and street sags are all depressions; the library does not know which ones hold
  water or cause problems.
- Lidar artifacts (notably under buildings) produce false depressions; pass `buildings=` to flag them.
- Coarse global DEMs show large basins, not street-level ponding.
- A depression is not a flood. Much of the Netherlands lies below sea level behind dikes and is
  pumped dry: the library finds those polders as large depressions.

## Project

- [docs/definition.md](docs/definition.md): precisely what is computed, including coverage
- [docs/outputs.md](docs/outputs.md): columns and files
- [docs/roadmap.md](docs/roadmap.md): where it is going, and how H3 resolution is handled
- [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md)

## Data and licensing

Code is Apache-2.0. No source data is included. Check the license of any elevation, drain or
building dataset you use before redistributing derived data (USGS 3DEP is public domain;
Copernicus DEM requires attribution).
