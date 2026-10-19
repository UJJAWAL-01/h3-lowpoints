# Command line

Installing the package adds one command, `h3lowpoints`, with seven subcommands. Every option is
listed here; `h3lowpoints <command> --help` prints the same in the terminal.

| Command | Does |
|---|---|
| [`analyze`](#analyze) | Analyze an elevation file. |
| [`build`](#build) | Analyze a US region of any size straight from USGS 3DEP, then verify it. |
| [`verify`](#verify) | Check a saved result. |
| [`map`](#map) | Make the interactive web map from a saved result. |
| [`fetch-dem`](#fetch-dem) | Download elevation data for a box. |
| [`fetch-landcover`](#fetch-landcover) | Download land cover for a box. |
| [`rainfall`](#rainfall) | Design rainfall for a point in the US. |

**Exit codes.** 0 on success. 1 when `verify` or `build` found problems. Any input error
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
| `--cache-dir DIR` | | Download each 3DEP tile here once (about 40 MB per 1° tile at 30 m) instead of reading it over HTTP twice. An interrupted run keeps the tiles already downloaded. Recommended. |
| `--spot-check N` | `0` | Also cut N random windows and require the whole and tiled analyses to agree exactly. |
| `--map FILE.html` | | Also write the interactive map (for small regions; a state has millions of cells). |

The run prints one line per tile per pass, then a summary and the verification outcome. Exit
code 1 means the verification found a problem (listed in `verify.json`).

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
h3lowpoints fetch-landcover --bbox WEST SOUTH EAST NORTH --out FILE.tif [--from worldcover|nlcd] [--year Y]
```

`worldcover` (default): ESA WorldCover 2021 classes, 10 m, worldwide. `nlcd`: NLCD percent
impervious surface, 30 m, lower 48 US states, for `--year` 2001, 2004, 2006, 2008, 2011, 2013,
2016, 2019 or 2021 (default). See [Data sources](data-sources.md).

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
