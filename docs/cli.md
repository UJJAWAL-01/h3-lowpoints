# Command line

Installing the package adds one command, `h3lowpoints`, with eleven subcommands. Every option is
listed here; `h3lowpoints <command> --help` prints the same in the terminal. `python -m h3lowpoints`
runs the same command (useful where the scripts folder is not on the PATH).

| Command | Does |
|---|---|
| [`analyze`](#analyze) | Analyze an elevation file. |
| [`build`](#build) | Analyze a US region of any size straight from USGS 3DEP, then verify it. |
| [`verify`](#verify) | Check a saved result. |
| [`clip`](#clip) | Keep the part of a saved result inside a boundary (for example the US). |
| [`validate`](#validate) | Do observations fall in or near depressions more often than chance? |
| [`index`](#index) | How easily each depression fills: runoff, rain to fill, design storms. |
| [`drainage`](#drainage) | Drained or landlocked: mapped channels and roads near each depression. |
| [`map`](#map) | Make the interactive web map from a saved result. |
| [`fetch-dem`](#fetch-dem) | Download elevation data for a box. |
| [`fetch-landcover`](#fetch-landcover) | Download land cover for a box. |
| [`rainfall`](#rainfall) | Design rainfall for a point in the US. |

**Exit codes.** 0 on success. 1 when `verify`, `build`, `clip`, `index` or `drainage` found problems. 3 when `build` stopped at
its `--hours` limit (run it again to continue). Any input error
(a bad box, a missing file, a CRS that cannot be used, a missing optional package) prints
`error: <explanation>` and exits with code 1. `h3lowpoints --version` prints the version.

**Boxes** are given as `--bbox WEST SOUTH EAST NORTH` in degrees, for example
`--bbox -97.9 30.1 -97.6 30.4`. A box crossing the antimeridian (180°) must be split in two.

## analyze

```
h3lowpoints analyze DEM [options]
```

| Option | Default | Meaning |
|---|---|---|
| `DEM` | | Elevation raster (GeoTIFF or anything GDAL reads) in a projected CRS or in degrees. |
| `--out FOLDER` | `h3lowpoints_out` | Where to write `depressions`, `cells` and `meta.json`. |
| `--res RES` | `auto` | H3 resolution 0 to 15, or `auto` for the finest the data supports. |
| `--min-depth M` | `0.15` | Shallowest depression kept, in metres. |
| `--format csv\|parquet` | `csv` | Table format (Parquet needs the `parquet` extra). |
| `--tile N` | `0` | Analyze in tiles of N × N pixels and write the tables as they are produced (for rasters, or results, larger than memory). 0 analyzes the whole raster at once. The result does not depend on N. |
| `--drains FILE` | | Drain points: GeoPackage, GeoJSON, Shapefile, or CSV with `lon,lat` or `x,y` columns. Adds the drain columns. Needs the `sources` extra. |
| `--buildings FILE` | | Building footprint polygons. Adds the building columns. Needs the `sources` extra. |
| `--map FILE.html` | | Also write the interactive map. |
| `--catchments` | off | Also follow the water: each depression's catchment area, the depression it overflows into and where (`catchment_area_m2`, `spill_to`, `spill_lat`, `spill_lon`), and the catchment table. See [Definition](definition.md#catchments-and-spill). |
| `--details` | off | Also describe each depression further: ground and water levels, length, width and orientation, the area and volume as the water drops, and the depression × cell table. See [Definition](definition.md#levels-shape-and-fill-curve). |
| `--workers N` | `1` | Label pixels with their H3 cells in N processes: the same result in less time (4 is a good choice on most laptops). |
| `--reproject` | off | First reproject the raster to its UTM zone. Not needed (degree grids are analyzed natively) and it smooths sharp edges. |

Example:

```
h3lowpoints analyze austin.tif --min-depth 0.5 --out results --map austin.html
```

prints the number of depressions found, the resolution used and how many touch the data's
border:

```
<n> depressions on H3 resolution 10 written to results
  <m> cut off by the data border
```

## build

```
h3lowpoints build --bbox WEST SOUTH EAST NORTH --out FOLDER [options]
```

Analyzes a box of any size in the United States from USGS 3DEP, without first assembling one big
file: it writes a virtual raster pointing at the 3DEP tiles, runs the exact tiled analysis,
writes the tables as they are produced, then verifies the result. See
[Large regions](large-regions.md).

| Option | Default | Meaning |
|---|---|---|
| `--bbox` | | The region. |
| `--out FOLDER` | | Output folder: `dem.vrt`, `depressions`, `cells`, `meta.json`, `verify.json`. |
| `--from 3dep\|3dep10` | `3dep` | 30 m (1 arc-second) or 10 m (1/3 arc-second) bare earth. |
| `--res RES` | `auto` | H3 resolution (auto: 10 for 30 m, 11 for 10 m). |
| `--min-depth M` | `0.15` | Shallowest depression kept, metres. For 30 m data 0.5 to 1 is sensible. |
| `--tile N` | `4096` | Tile size in pixels (memory and speed, never the result). |
| `--format csv\|parquet` | `csv` | Table format. Parquet is recommended for large regions. |
| `--cache-dir DIR` | | Download each 3DEP tile here once (about 40 MB per 1° tile at 30 m) instead of reading it over the internet twice. Faster (about 1.65× in a measured test) but needs disk for every tile; without it nothing is stored. An interrupted run keeps the tiles already downloaded. See [Large regions](large-regions.md#streaming-or-caching-the-tiles). |
| `--water-mask` | off | Treat permanent water (ESA WorldCover class 80, "permanent water bodies") and the open sea beyond WorldCover (where the elevation is at or below 0 m) as open water instead of terrain: lakes, reservoirs and the sea then act as places water leaves, instead of filling as giant depressions. The mask is built once, tile by tile, on the elevation data's own pixel grid, as `water.vrt` with one small file per tile holding water in `water_tiles/`; it continues where it stopped, counts against `--hours`, and later parts of the run reuse it. See [Data sources](data-sources.md#water-mask-from-esa-worldcover). |
| `--hours H` | | Stop after about H hours (building the water mask included), at the end of a tile, and exit with code 3. Every tile is saved as it finishes, so running the same command again continues where it stopped. Use it to split a long run into parts; see [Large regions](large-regions.md#running-in-parts). |
| `--spot-check N` | `0` | Also cut N random windows and require the whole and tiled analyses to agree exactly. |
| `--map FILE.html` | | Also write the interactive map (for small regions; a state has millions of cells). |
| `--catchments` | off | Also follow the water: catchment areas, spill links and the catchment table, exactly as `analyze --catchments`. |
| `--details` | off | Also levels, shape, fill curve and the depression × cell table, exactly as `analyze --details`. |
| `--workers N` | `1` | Label pixels with their H3 cells in N processes: the same result in less time. |

The run prints one line per tile per pass, then a summary and the verification outcome. Exit
code 1 means the verification found a problem (listed in `verify.json`). Every tile is saved as
it finishes: if the run is interrupted (a crash, a shutdown, a lost connection, Ctrl+C), the same
command continues where it stopped, redoing at most the tile that was in progress.

```
h3lowpoints build --bbox -98 30 -96 32 --out central_texas --min-depth 0.5 \
    --cache-dir tiles --format parquet --spot-check 3
```

## verify

```
h3lowpoints verify FOLDER [--dem DEM] [--windows N]
```

Checks a saved result against the invariants every correct result satisfies (see
[Verification](verification.md)), reading the cell table in pieces so a result of any size can be
checked. With `--dem`, also cuts N (default 3) random windows from that raster and requires the
whole and tiled analyses to agree, using the settings in `meta.json`. Prints
`all invariants hold` (exit 0) or the list of problems (exit 1).

## clip

```
h3lowpoints clip FOLDER --boundary FILE|us48 --out FOLDER [--format csv|parquet]
```

Keeps the part of a saved result inside a boundary, after the analysis, so the hydrology stays
exact (no artificial outlet along the boundary). A cell is kept when its centre is inside, and a
depression when the cell of its lowest point is kept. The clipped result records `meta["clip"]`
and is verified on writing.

| Option | Default | Meaning |
|---|---|---|
| `--boundary` | | A vector file (GeoPackage, GeoJSON, Shapefile; any CRS), or `us48` for the lower 48 US states and DC from the US Census Bureau (downloaded once and saved in the output folder as `boundary_us48.gpkg`; needs the `sources` extra). |
| `--out FOLDER` | | Where to write the clipped result (not the input folder). |
| `--format csv\|parquet` | as the input | Table format. |

```
h3lowpoints clip conus_30m --boundary us48 --out us48_30m
```

## validate

```
h3lowpoints validate FOLDER --observations FILE [--controls FILE] [--boundary FILE] [options]
```

Measures how much more often observations (flood reports, claims, high-water marks) fall in or
near depressed ground than control points do, at distances of 0 to `--max-rings` H3 rings, with
block-bootstrap confidence intervals. Prints the table and any warnings, and writes the report.
See [Validation](validation.md) for the method, the choice of controls and how to read the
result.

| Option | Default | Meaning |
|---|---|---|
| `--observations FILE` | | Observed locations: a CSV with `lon`/`lat` (or `longitude`/`latitude`, `lng`/`lat`) columns in degrees, or a vector file in any CRS. An `id` column is carried through. |
| `--controls FILE` | | Where observations could have come from (addresses, building footprints), in the same forms. Without it, random points in `--boundary` are used, with a warning. |
| `--boundary FILE` | the result's box, if any | The area studied (vector file, any CRS). Points outside are left out. Required without `--controls`. |
| `--n-random N` | 10 per observation, at least 1000 | Number of random controls when `--controls` is not given. |
| `--max-rings K` | `5` | Largest distance measured, in rings of the result's resolution. |
| `--classes FILE` | | A label for each depression (`.csv` or `.parquet` with `id` and `class` columns, such as "fills in a 2-year storm" or "landlocked"): also split the lift by the class of the nearest depressed ground and compare the classes ([Comparing classes](validation.md#comparing-classes-of-depressions)). |
| `--class-order A,B,...` | sorted | The classes in order: ties go to the earlier class, and each class is compared with each later one. |
| `--block-res R` | `6` | H3 resolution of the bootstrap blocks; must not be finer than the result's. |
| `--boot N` | `1000` | Bootstrap samples (0: no intervals). |
| `--seed S` | `0` | Random seed (bootstrap and random controls). |
| `--keep-suspect` | | Keep the ground around depressions flagged `deeper_than_wide` (left out by default). |
| `--out FILE` | `validation.json` | The report (JSON). |
| `--points FILE` | | Also write the per-point table (`.csv`, or `.parquet` with pyarrow). |

```
h3lowpoints validate us48_30m_water --observations reports.csv --controls addresses.csv --boundary city.gpkg --points points.csv
```

## index

```
h3lowpoints index FOLDER (--curve-numbers FILE | --curve-number CN) [--rain atlas14|MM] [options]
```

Compares the rain reaching each depression with what it holds, for a result made with
`--catchments`, and writes the susceptibility table (`susceptibility.csv` or `.parquet`, in the
result's format) into the folder, with its settings in `meta.json`. Prints a summary, then
verifies the folder. See [Susceptibility](susceptibility.md) for the method.

| Option | Default | Meaning |
|---|---|---|
| `--curve-numbers FILE` | | SCS curve numbers: a raster in any CRS (its mean per H3 cell is used; in the US, the `fetch-landcover` command makes one: see its `curve-numbers` source), or a `.csv`/`.parquet` table with columns `h3` and `curve_number` at the result's resolution, sorted by `h3`, or a folder of such tables whose names sort in the order of their cells. |
| `--curve-number CN` | | Instead of `--curve-numbers`: one curve number (0 to 100) for all ground. |
| `--impervious FILE` | | Optional: percent paved, as a raster or a table of `h3` and `impervious_pct`, reported as `impervious_pct` (the curve numbers already account for it). |
| `--rain atlas14\|MM` | | `atlas14`: 24-hour storms of 1 to 1000 years from NOAA Atlas 14 (US; only the grids of the project areas the result lies in are downloaded, about 10 to 40 MB each). A number: one uniform storm of that many millimetres. Without it, only the rain-to-fill columns. |
| `--min-coverage F` | `0.9` | Share of a catchment an input must cover; below it the columns needing that input are NaN. |
| `--cache-dir DIR` | | Keep the downloaded rainfall grids here and reuse them. |
| `--workers N` | `1` | Label raster pixels with H3 cells in this many processes. |

```
h3lowpoints index houston --curve-numbers cn.tif --rain atlas14 --cache-dir atlas14
```

## drainage

```
h3lowpoints drainage FOLDER [--streams FILE] [--canals FILE] [--roads FILE] [--nhd] [--tiger] [options]
```

Looks for signs that each depression is drained: a mapped stream or canal at its lowest point,
a highway or ramp at its spill point. Adds the drainage columns to the folder's
susceptibility table (keeping the index columns), records the settings in `meta.json`, prints
how many are drained and landlocked, and verifies the folder. At least one source of lines is
needed. See [Susceptibility](susceptibility.md#drained-or-landlocked).

| Option | Default | Meaning |
|---|---|---|
| `--streams FILE` | | Stream and river lines (vector file, any CRS). |
| `--canals FILE` | | Canal, ditch, conduit and pipeline lines. |
| `--roads FILE` | | Road lines. |
| `--nhd` | | US: fetch streams (NHD types 460, 558) and canals (336, 420, 428) for the result's area from the NHD map service, instead of `--streams` and `--canals`. |
| `--tiger` | | US: fetch the highways and ramps (classes S1100, S1200, S1630) of the counties the result touches from Census TIGER/Line, instead of `--roads`. Needs `--cache-dir`. |
| `--all-roads` | | With `--tiger`: every road, not only highways and ramps. |
| `--land-cover RASTER` | | Land cover classes, read at each lowest point (for example NLCD classes from `fetch-landcover`). |
| `--near-m M` | `30` | A channel this close to the lowest point, or a road this close to the spill point, drains the depression. |
| `--min-share F` | off | Also count as drained when channels cross cells holding at least this share of its area. |
| `--max-distance-m M` | `1000` | Distances beyond this are left empty. |
| `--cache-dir DIR` | | Keep the downloaded county road files here. |

```
h3lowpoints drainage houston --nhd --tiger --cache-dir tiger --land-cover classes.tif
```

## map

```
h3lowpoints map FOLDER [--out map.html] [--title TEXT]
```

Writes the interactive map of a saved result (CSV or Parquet). See [The map](map.md). A result
read from disk has no data footprint, so coarser levels of this map show depth, area, volume and
counts but not coverage; maps written by `analyze --map` or `Result.to_html` have coverage at
every level.

## fetch-dem

```
h3lowpoints fetch-dem --bbox WEST SOUTH EAST NORTH --out FILE.tif [--from DATASET] [options]
```

| `--from` | Data | Area |
|---|---|---|
| `copernicus` (default) | Copernicus GLO-30, 30 m surface model | the world |
| `3dep` | USGS 3DEP 1 arc-second, ~30 m bare earth | United States |
| `3dep10` | USGS 3DEP 1/3 arc-second, ~10 m bare earth | United States |
| `lidar` | USGS 3DEP 1 m lidar bare earth | most of the United States |

| Option | Default | Meaning |
|---|---|---|
| `--source DIR` | | Read tiles from a local folder instead of the internet (not for `lidar`). |
| `--max-tiles N` | `16` | Refuse boxes touching more than N 1° tiles, so a typo cannot start a huge download. Lidar boxes are limited to 100 km² instead. |

Prints the size of the raster written, for example
`1081 x 1081 pixels (0.000277778 degrees each) written to austin.tif`.

## fetch-landcover

```
h3lowpoints fetch-landcover --bbox WEST SOUTH EAST NORTH --out FILE.tif [--from worldcover|nlcd|nlcd-landcover|soil-groups|curve-numbers] [--year Y] [--nlcd-source annual|mrlc] [--cache-dir DIR]
```

`worldcover` (default): ESA WorldCover 2021 classes, 10 m, worldwide. `nlcd`: NLCD percent
impervious surface, 30 m, lower 48 US states. `nlcd-landcover`: NLCD land cover classes. Both come
from Annual NLCD (`--nlcd-source annual`, the default: any `--year` from 1985 to 2024, default
2024) or from the MRLC service's older editions (`--nlcd-source mrlc`: 2001, 2004, 2006, 2008,
2011, 2013, 2016, 2019 or 2021, default 2021).
`soil-groups`: soil hydrologic groups from the USDA soil survey (gNATSGO), on a 30 m grid.
`curve-numbers`: SCS curve numbers on NLCD's 30 m grid from NLCD land cover, imperviousness and
the soil groups, with the three inputs kept beside the output. Both keep the soil survey's
map-unit table in `--cache-dir` (default: the output's folder). See [Data sources](data-sources.md).

## rainfall

```
h3lowpoints rainfall LAT LON [--duration D] [--years Y]
```

Without options, prints the full NOAA Atlas 14 table of design rainfall depths in millimetres
(rows: durations from 5 minutes to 60 days; columns: return periods from 1 to 1000 years). With
`--duration` and/or `--years`, prints one depth (defaults `60-min` and 10 years):

```
h3lowpoints rainfall 30.27 -97.74 --duration 24-hr --years 100
```
