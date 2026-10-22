# h3-lowpoints

[![PyPI](https://img.shields.io/pypi/v/h3-lowpoints)](https://pypi.org/project/h3-lowpoints/)
[![Tests](https://github.com/UJJAWAL-01/h3-lowpoints/actions/workflows/ci.yml/badge.svg)](https://github.com/UJJAWAL-01/h3-lowpoints/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-online-blue)](https://ujjawal-01.github.io/h3-lowpoints/)
[![Software DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.23552522-blue)](https://doi.org/10.5281/zenodo.23552522)
[![Dataset DOI](https://img.shields.io/badge/dataset-10.5281%2Fzenodo.23552773-blue)](https://doi.org/10.5281/zenodo.23552773)

Find closed depressions ("low points") in an elevation model and describe them on
[H3](https://h3geo.org) hexagon cells at any resolution, ready for maps, 3D views and analysis.

> **Status: alpha (0.x).** The method is tested against independent methods and on real terrain.
> A depression is a feature of the terrain model: this library does not say whether water
> collects there, and its output is **not a flood map**.

**Documentation: [ujjawal-01.github.io/h3-lowpoints](https://ujjawal-01.github.io/h3-lowpoints/)**
(also in [docs/](https://ujjawal-01.github.io/h3-lowpoints/)), with
[getting started](https://ujjawal-01.github.io/h3-lowpoints/getting-started), [concepts](https://ujjawal-01.github.io/h3-lowpoints/concepts),
[outputs](https://ujjawal-01.github.io/h3-lowpoints/outputs), the [Python API](https://ujjawal-01.github.io/h3-lowpoints/python-api), the
[command line](https://ujjawal-01.github.io/h3-lowpoints/cli), [data sources](https://ujjawal-01.github.io/h3-lowpoints/data-sources),
[large regions](https://ujjawal-01.github.io/h3-lowpoints/large-regions), [the map](https://ujjawal-01.github.io/h3-lowpoints/map),
[verification](https://ujjawal-01.github.io/h3-lowpoints/verification), [accuracy and limits](https://ujjawal-01.github.io/h3-lowpoints/limitations), a
[glossary](https://ujjawal-01.github.io/h3-lowpoints/glossary) and the [roadmap](https://ujjawal-01.github.io/h3-lowpoints/roadmap).

## What it does

1. **Fills** the terrain the way water would, to find every place water cannot flow out of, with
   its depth, area, volume and lowest point.
2. **Maps** every depressed pixel onto H3 cells: deepest depth, depressed area and volume, number
   of lowest points, and how much of each cell the data covers. Coarser resolutions are exact
   roll-ups, so one result serves every scale from a street to a continent.
3. **Scales** to regions far larger than memory with an exact tiled method: the answer never
   depends on the tile size, and tables are written to disk as they are produced.
4. **Fetches** open data for any box: USGS 3DEP (US, 30 m, 10 m and 1 m lidar), Copernicus GLO-30
   (worldwide), land cover and design rainfall.
5. **Shows** results as a self-contained, shareable 3D web map.
6. **Verifies** any result, anywhere, with checks that need no reference answer.

## Install

Python 3.10 or newer:

```
pip install h3-lowpoints                          # core
pip install "h3-lowpoints[viz,sources,parquet]"   # + notebook maps, drain/building files, Parquet output
```

## National dataset

Every closed depression on land in the lower 48 US states, from USGS 3DEP 30 m with open water
masked: 10.6 million depressions and 34 million H3 resolution-10 cells, as Parquet, with a
description of methods, columns and limits. Open data (CC BY 4.0) on Zenodo:
[doi.org/10.5281/zenodo.23552773](https://doi.org/10.5281/zenodo.23552773).

## Quick start

```python
from h3lowpoints import analyze
from h3lowpoints.sources.usgs import fetch_3dep

fetch_3dep((-97.9, 30.1, -97.6, 30.4), "austin.tif")   # USGS 3DEP, 30 m bare earth
res = analyze("austin.tif", min_depth=0.5)              # H3 resolution chosen from the data: 10

res.depressions            # one row per depression: depth, area, volume, lowest point, ...
res.cells                  # one row per H3 cell: deepest depth, area, volume, counts, coverage
res.at_resolution(7)       # the same cells rolled up exactly to resolution 7
res.to_html("map.html")    # interactive 3D map, one file
res.save("results")        # depressions.csv, cells.csv, meta.json (or format="parquet")
```

The same from the command line:

```
h3lowpoints fetch-dem --from 3dep --bbox -97.9 30.1 -97.6 30.4 --out austin.tif
h3lowpoints analyze austin.tif --min-depth 0.5 --out results --map map.html
h3lowpoints verify results
```

A whole US region, straight from the 3DEP tiles, verified on completion:

```
h3lowpoints build --bbox -98 30 -96 32 --out region --min-depth 0.5 --cache-dir tiles --format parquet
```

## What counts as a depression

The terrain is filled with the priority-flood algorithm; water leaves over the raster's edge and
into nodata or masked areas. Fill level minus ground is the depth, and an 8-connected area of
positive depth whose deepest point reaches `min_depth` (default 0.15 m) is one depression. The
precise rules are in [docs/definition.md](https://ujjawal-01.github.io/h3-lowpoints/definition).

## Limits

- Ponds, quarries, sinkholes and street sags are all depressions; the library does not know which
  hold water or cause problems, nor about drains the elevation data cannot see.
- Results are only as good as the elevation data: keep `min_depth` above its vertical error, and
  prefer bare-earth data. Road embankments over culverts appear as dams.
- Heights must be in metres.

The full list is in [docs/limitations.md](https://ujjawal-01.github.io/h3-lowpoints/limitations).

## Project

To cite the library, use [doi.org/10.5281/zenodo.23552522](https://doi.org/10.5281/zenodo.23552522) (all versions) or the
entry in [CITATION.cff](https://github.com/UJJAWAL-01/h3-lowpoints/blob/main/CITATION.cff).

[Contributing](https://github.com/UJJAWAL-01/h3-lowpoints/blob/main/CONTRIBUTING.md) · [Changelog](https://github.com/UJJAWAL-01/h3-lowpoints/blob/main/CHANGELOG.md) · [Citation](https://github.com/UJJAWAL-01/h3-lowpoints/blob/main/CITATION.cff)

Code is Apache-2.0 (see [LICENSE](https://github.com/UJJAWAL-01/h3-lowpoints/blob/main/LICENSE) and [NOTICE](https://github.com/UJJAWAL-01/h3-lowpoints/blob/main/NOTICE)). No data is included; data fetched through the library keeps its own licence
(USGS 3DEP, NLCD and NOAA Atlas 14 are public domain; Copernicus GLO-30 and ESA WorldCover require
attribution). See [docs/data-sources.md](https://ujjawal-01.github.io/h3-lowpoints/data-sources).
