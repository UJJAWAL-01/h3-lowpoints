# How results are verified

The library is meant to be used on places nobody has checked by hand, so its checks must work
anywhere, with no reference answer. There are three layers: invariants any user can run on any
result, spot checks on the user's own data, and oracle tests in the test suite. A fourth layer,
validation against observed flooding, is planned (see the end of this page).

## 1. Invariants: `check_result`, `h3lowpoints verify`

An invariant is a property every correct result has, whatever the terrain. Breaking one means the
result is wrong (or was damaged after it was written).

| Invariant | What breaking it reveals |
|---|---|
| The cell table's total depressed area equals the depression table's total area; the same for volume. | Pixels lost, counted twice, or assigned to the wrong table when cells were built or merged. |
| The deepest cell is exactly as deep as the deepest depression, and no cell is deeper. | Depths altered or mismatched between tables. |
| `n_lowpoints` adds up to the number of depressions, and each lowest point lies in a listed cell with the right count. | Lowest points dropped, duplicated or put in the wrong cell. |
| Every cell and every `h3_lowpoint` is a valid H3 id at the table's resolution; cell ids are unique. | Ids corrupted (for example by a reader that turned an id into a number), mixed resolutions, duplicated cells. |
| Every `h3_lowpoint_15` contains its depression's `lat`/`lon`. | Locations and ids out of step. |
| Depths are at least `min_depth`, areas positive, volumes between 0 and maximum depth × area, and no value is NaN or infinite; latitudes and longitudes are in range. | Arithmetic or filtering errors. |
| Coverage lies in (0, 1]; `covered_area_m2` is coverage × the cell's exact area and never exceeds it; `depressed_share` lies in [0, 1]. | Coverage errors. |
| `meta.json`'s depression count matches the table. | Tables and metadata from different runs. |

```
h3lowpoints verify results_folder     # "all invariants hold" (exit 0), or the problems (exit 1)
```

```python
from h3lowpoints.verify import check_result, check_saved
check_result(res)               # [] when every invariant holds
check_saved("results_folder")   # the same for files on disk
```

Saved results are checked **in pieces**: the depression table is read whole, the cell table
500,000 rows at a time, keeping only each cell's 8-byte id until the end (to find duplicates). A
national result with tens of millions of cells is checked in modest memory.

The test suite damages a good result in 15 different ways (dropping a cell, doubling an area,
repeating an id, a NaN depth, a depression below `min_depth`, an impossible volume, ids at the
wrong resolution, a moved lowest point, an off-by-one count, coverage above 1, a wrong covered
area, a share above 1, a wrong count in `meta.json`, cells at mixed resolutions, a lowest point
outside the table) and requires each to be caught, both in memory and when read back from disk in
pieces of three rows. It also checks that duplicate cells in different pieces and ids that are not
H3 cells are reported.

## 2. Spot checks: `spot_check`, `verify --dem`

The tiled analysis must give exactly the whole-raster answer. `spot_check(dem)` cuts random
windows from any raster (a GeoTIFF, or a VRT of remote tiles), analyzes each both ways, and
requires them to agree depression by depression and cell by cell, and both to pass the
invariants. This tests the exact tiling on the real terrain being analyzed, not only on test
terrain.

```
h3lowpoints verify results_folder --dem dem.vrt --windows 5
```

`h3lowpoints build` runs the invariants on every region it builds (and spot checks with
`--spot-check N`), and writes the outcome to `verify.json` beside the results.

## 3. Oracle tests in the test suite

An oracle is an independent way of computing the same thing. The suite has over 500 tests; it is
set up to run on every change on Linux and Windows with Python 3.10, 3.12, 3.13 and 3.14. It
compares:

| Library part | Oracle |
|---|---|
| The fill | Morphological reconstruction (Soille), a different algorithm, on random terrain. |
| Tiled analysis | The whole-raster analysis, depression by depression and cell by cell: random terrain, terraces full of ties, integer elevations, a serpentine corridor through every tile, a lake with a distant sill, nodata and NaN across tile borders, drains, buildings, masks, degree and feet grids, tiles of one pixel, and 60 random configurations. |
| Writing results to disk | The same run held in memory, in CSV and Parquet, with tables written and read in small pieces and cells spread over several partitions. |
| Array aggregation | pandas group-by on random data. |
| Pixel areas | An independent ellipsoid computation. |
| Coverage | Assigning every pixel to its H3 cell by brute force, on projected, degree, feet, polar and antimeridian grids. |
| Mosaics | The source tiles, pixel by pixel. |

**Mutation testing.** Deliberate bugs are planted in the code, one at a time, to prove the tests
notice them. Six breakages of the tiling algorithm (dropping seam steps, ignoring diagonals,
keeping the highest crossing, and others), six breakages of the memory-saving code
(off-by-one block bounds, a forgotten raster edge, a wrong chunk offset, summing instead of taking
a maximum, ignoring a sort key, using the wrong depression for a piece) and thirteen breakages of
the disk output and checking code (a wrong partition key, a spill file given every partition's
data, repeated CSV headers, H3 ids read as numbers, inexact float reading, dropped pieces left in,
miscounted lowest points, tables never written in pieces, stale files of the other format,
disabled duplicate and missing-cell checks, mangled ids, a CRS given as text) are each caught.
Writing this layer found three gaps in the tests, which were closed before release.

**Real data.** Tests marked `network` (run with `H3LP_NETWORK=1`) repeat key checks on real
terrain: Copernicus data around Austin, the Dutch coast, Death Valley, the Alps and Norway's
fjords (tiled equals whole in every case), the real 3DEP, lidar, NLCD, WorldCover and Atlas 14
services, a live `build` of a small region, and the web map in a headless browser. The 3DEP box
around Texas (1.8 billion pixels, 1.75 million depressions) passes every invariant.

## What this does not check

These checks show that the library computes what it says it computes. They do not show that a
depression floods. That needs observations, such as flood reports or high-water marks, and is the
job of the planned validation tools: any set of observed locations, from anywhere, scored against
the depressions with the same metrics and compared with random placements. See the
[Roadmap](roadmap.md) and [Accuracy and limits](limitations.md).
