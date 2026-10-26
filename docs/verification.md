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
| With details: `water_level_m - ground_m` is `max_depth_m`; `0 < width_m <= length_m`; `orientation_deg` in [0, 180); flooded areas and volumes never grow as the water drops and each volume fits in the depth left times its area; the depression × cell table adds up to each depression's area, volume and fill curve, its deepest row is the depression's depth, and it names only listed depressions. | Levels, shapes or fill curves computed wrongly, or tables out of step. |
| With catchments: every catchment is at least as large as its depression; the catchment table adds up to `catchment_area_m2` and names only listed depressions; `spill_to` names a listed depression or 0, never the depression itself; following `spill_to` always ends; spill points are finite and in range. | Water counted twice or lost, overflow running in a loop, tables out of step. |
| With a susceptibility table: every depression appears once; `impervious_pct` lies in [0, 100] and `curve_number` in (0, 100]; `rain_to_fill_mm` is the storm whose SCS runoff over the catchment equals the volume; `rain_to_fill_upstream_mm` is at most `rain_to_fill_mm` and at least the smallest initial abstraction (0.2 S) of it and every catchment upstream (not checked after a clip, which drops upstream depressions); fill ratios are not negative, and a storm with a fill ratio of 1 or more is no rarer than `fills_in_years`. With drainage columns: distances lie in [0, `max_distance_m`], `channel_share` in [0, 1], `land_cover` is not negative, `drained` follows from the distances, the share and the rule in `meta.json`, and `landlocked` is its opposite. | Runoff, rain to fill, storm or drainage columns computed wrongly, or a table from another result. |

```
h3lowpoints verify results_folder     # "all invariants hold" (exit 0), or the problems (exit 1)
```

```python
from h3lowpoints.verify import check_result, check_saved
check_result(res)               # [] when every invariant holds
check_saved("results_folder")   # the same for files on disk
```

**Clipped results** (`h3lowpoints clip`, recorded in `meta["clip"]`) keep, for a depression that
straddles the boundary, only its cells inside it, so the cell table's totals no longer equal the
depression table's. For them the four checks that compare those totals (area, volume, deepest,
no cell deeper than every depression) are skipped; every other invariant still holds and is
checked, including the lowest-point counts, which clipping keeps exact.

Saved results are checked **in pieces**: the depression table is read whole, the cell table
500,000 rows at a time, keeping only each cell's 8-byte id until the end (to find duplicates). A
national result with tens of millions of cells is checked in modest memory.

Details get eight damages of their own (a wrong ground height, a width above the length, an
orientation of 200°, a flooded area or a volume that grows as the water drops, a depression ×
cell table that no longer adds up, a wrong deepest row, rows for an unknown depression).
Catchments get six more damages (a catchment table that no longer adds up, a catchment smaller
than its depression, a depression spilling into itself, spills forming a loop, a spill to an
unknown depression, catchment rows for an unknown depression), each of which must be caught.
A susceptibility table gets nine (a curve number out of range or shifted, a wrong rain to fill
or imperviousness, an upstream rain larger than the own one or far too small, a missing or
repeated row, a `fills_in_years` rarer than a storm that fills), and the drainage columns five (a flipped
`drained`, a `landlocked` equal to `drained`, a negative distance, a distance beyond the limit, a
share above 1), each of which must be caught.

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
| SCS runoff | TR-55 worked by hand (CN 80, 5 inches of rain: 2.89 inches), and its inverse to machine precision. |
| Curve numbers | The TR-55 table, class by class and group by group, and the composite with pavement. |
| Soil groups | Fake soil tiles in gNATSGO's layout: every pixel more than 10 m from a soil boundary takes exactly its map unit's group. |
| The spill cascade | A plain recursive version on random spill networks; water must be conserved, and a cascade passing on all the water instead of the overflow must disagree. |
| `rain_to_fill_upstream_mm` | A bisection rerunning the plain cascade with SCS runoff for each depression (within 1%); and a uniform storm of depth D must fill exactly the depressions whose value is at most D. |
| Imperviousness per H3 cell | Assigning every pixel to its cell one by one. |
| Atlas 14 grids | NOAA's point service at the same place (network test). |
| Drainage distances | shapely's distance from each point to each line set. |
| Cells a line crosses | An exact test against each hexagon's outline: every cell a line crosses for at least the sampling step is found, and no cell it misses. |
| NHD map service | The state GeoPackage over the same box (checked by hand: identical lines). |

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
