# Getting started

This page takes you from nothing to a first analysis and a first map. Each step shows the
Python way and the command-line way; use whichever you prefer.

## 1. Install

You need Python 3.10 or newer:

```
pip install h3-lowpoints
```

To work on the library itself, install it from a clone instead:
`git clone https://github.com/UJJAWAL-01/h3-lowpoints`, then `pip install -e ".[dev]"` inside it.

Optional extras add features that need more packages:

| Extra | Adds | Install |
|---|---|---|
| `viz` | 3D maps inside Jupyter notebooks (pydeck) | `pip install "h3-lowpoints[viz]"` |
| `sources` | reading drain and building files (GeoPackage, Shapefile, GeoJSON), boundaries | `pip install "h3-lowpoints[sources]"` |
| `parquet` | saving and reading results as Parquet, smaller and faster than CSV | `pip install "h3-lowpoints[parquet]"` |
| `dev` | running the test suite (from a clone) | `pip install -e ".[dev]"` |

Combine them as `pip install "h3-lowpoints[viz,sources,parquet]"`. The interactive web map
(`to_html`) needs no extra.

The first analysis after installing takes a few extra seconds: the core fill is compiled to
machine code by numba once, then cached.

## 2. Run the built-in example (no download)

```
python examples/quickstart.py my_first_run      # from a clone of the repository
```

This builds a small synthetic 1 m terrain with three depressions (a deep pit, a broad sag and a
pit under a pretend building), analyzes it, and writes the results to `my_first_run/`. It prints
something like:

```
   max_depth_m  area_m2  drain_at_core  building_overlap
0         0.89    600.0           True               0.0
1         1.99     64.0          False               0.0
2         0.58    400.0          False               1.0
13 cells at H3 res 12, 5 after rolling up to res 10
```

Three depressions were found: 0.89 m, 1.99 m and 0.58 m deep. The first has a drain at its deepest
part; the third lies entirely under the building footprint, so it is flagged as a likely artifact
of the elevation data.

## 3. Analyze real terrain

Download elevation data for a box (west, south, east, north, in degrees). For anywhere in the
world, Copernicus GLO-30 (30 m) is the default; in the United States, USGS 3DEP gives bare-earth
data at 30 m, 10 m or 1 m. See [Data sources](data-sources.md) for which to choose.

```
h3lowpoints fetch-dem --from 3dep --bbox -97.9 30.1 -97.6 30.4 --out austin.tif
h3lowpoints analyze austin.tif --min-depth 0.5 --out austin_results --map austin.html
```

Or in Python:

```python
from h3lowpoints import analyze
from h3lowpoints.sources.usgs import fetch_3dep

fetch_3dep((-97.9, 30.1, -97.6, 30.4), "austin.tif")
res = analyze("austin.tif", min_depth=0.5)
```

`min_depth` is the shallowest depression to keep, in metres. On 30 m data, depths below about
half a metre are within the data's own error, so 0.5 m is a sensible start; on 1 m lidar the
default of 0.15 m is fine. The library warns you when a setting asks more of the data than it
can give.

## 4. Look at the results

```python
res.res                    # the H3 resolution chosen: 10 for 30 m data
res.depressions.head()     # one row per depression
res.cells.head()           # one row per H3 cell that holds depressed ground
res.meta                   # every setting and version, for reproducibility
```

The depression table:

| id | max_depth_m | area_m2 | volume_m3 | lat | lon | h3_lowpoint | h3_lowpoint_15 | edge |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.885 | 600.0 | 415.5 | 30.27511 | -97.75146 | 8c489e3405927ff | 8f489e340592449 | False |

Each row is one depression: how deep it is at its deepest point, how much ground it covers, how
much water it could hold, where its lowest point is (as latitude and longitude and as H3 cells),
and whether it touches the edge of the data (then its true size is unknown).

The cell table:

| h3 | max_depth_m | depress_area_m2 | depress_volume_m3 | n_lowpoints | covered_area_m2 | coverage | depressed_share |
|---|---|---|---|---|---|---|---|
| 8c489e3405927ff | 0.885 | 232.0 | 181.7 | 1 | 350.67 | 1.0 | 0.66 |

Each row is one H3 cell: the deepest depression depth inside it, the depressed area and volume
inside it, how many depressions have their lowest point in it, how much of the cell the data
covered, and what share of the covered part is depressed. Every column is explained in
[Outputs](outputs.md).

## 5. See it on a map

```python
res.to_html("map.html")
```

Open `map.html` in a browser. The hexagons are drawn in 3D over a basemap; you can switch H3
resolution (the finest and up to five coarser roll-ups), choose what colour and height show, and
mark the lowest points. The file contains its own data and needs only an internet connection for
the map library and basemap tiles. See [The map](map.md).

## 6. Change the resolution

```python
res.res                        # 10 (the finest the 30 m data supports)
coarse = res.at_resolution(7)  # a coarser table, rolled up exactly: no re-analysis
```

Depth takes the maximum, and area, volume and counts add up, so a coarser table is exactly what
an analysis at that resolution would give. Coverage is measured afresh for the bigger cells.
`h3lowpoints.resolution_guide("austin.tif")` lists every resolution with its cell size and how
many elevation pixels each cell holds for this DEM.

## 7. Save and reload

```python
res.save("austin_results")                    # depressions.csv, cells.csv, meta.json
res.save("austin_results", format="parquet")  # the same as Parquet (needs the parquet extra)

from h3lowpoints import read_result
again = read_result("austin_results")
```

## 8. Check a result

```
h3lowpoints verify austin_results
```

prints `all invariants hold`, or a list of problems. These checks need no reference answer, so
they work on any result from anywhere. See [Verification](verification.md).

## Next steps

- A whole state or region: [Large regions](large-regions.md) and `h3lowpoints build`.
- Drains and building footprints, to explain or discount depressions: the `drains_xy` and
  `buildings` parameters in the [Python API](python-api.md).
- What the numbers can and cannot support: [Accuracy and limits](limitations.md).
