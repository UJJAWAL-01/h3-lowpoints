# How results are verified

The library is meant to be used on places nobody has checked by hand, so its checks must work
anywhere, with no reference answer. There are three layers.

## 1. Invariants: `check_result`, `h3lowpoints verify`

Every correct result satisfies these, whatever the terrain:

- the cell table adds up to the depression table: total area, total volume and the deepest depth;
- `n_lowpoints` adds up to the number of depressions, and each lowest point lies in a listed cell
  with the right count;
- every cell and every `h3_lowpoint` is at the table's resolution, and every `h3_lowpoint_15`
  contains its lowest point;
- depths are at least `min_depth`, areas are positive, a volume never exceeds deepest depth times
  area, and nothing is NaN;
- coverage lies in (0, 1], `covered_area_m2` is coverage times the cell's exact area and never
  exceeds it, and `depressed_share` lies in [0, 1].

```
h3lowpoints verify results_folder          # "all invariants hold", or a list of problems (exit code 1)
```

The test suite corrupts a good result in 15 different ways and requires each to be caught.

## 2. Spot checks: `spot_check`, `verify --dem`

`spot_check(dem)` cuts random windows from any DEM (a GeoTIFF, or a VRT of remote tiles) and runs
each through both the whole-raster and the tiled analysis. They must agree exactly. This checks
the exact tiling on the real terrain of the region being built, not only on test terrain.

```
h3lowpoints verify results_folder --dem dem.vrt --windows 5
```

`h3lowpoints build` runs both layers on every region it builds and writes `verify.json` beside the
results.

## 3. Oracle tests in the test suite

The suite compares the library with independent methods: the fill with morphological
reconstruction, tiled runs with whole-raster runs (on random terrain, mazes and 60 random
configurations), pixel areas with an ellipsoid implementation, coverage with a brute-force
assignment of every pixel, and mosaics with the source tiles pixel by pixel. Deliberate breakages
of the tiling algorithm are each caught. Tests marked `network` repeat key checks on real data.

## What this does not check

These checks show the library computes what it says it computes. They do not show that a
depression floods. That needs observations, such as flood reports, and is the job of the
validation tools (step 4 of the roadmap): any set of observed locations, from anywhere, scored
against the depressions with the same metrics.
