# What is computed

This page is the contract for the core of the library: what a "depression" is, and what the
results mean at the edges. Behaviour described here is covered by the tests in `tests/`.

## The fill

`h3lowpoints.hydro.depressions.depth_from_array` runs a priority-flood fill. Starting from
every outlet cell, it raises each cell to the lowest level water could stand at there. Outlets are

- cells on the raster border,
- cells next to nodata (a void or masked area is treated as a place water can leave),
- cells marked in an optional `drains` raster.

Depth is the filled elevation minus the original elevation. The result is checked in
`tests/test_fill_reference.py` against morphological reconstruction (Soille), an independent
method, on random terrain.

## What is one depression

A depression is an **8-connected** region of cells with depth greater than zero whose maximum
depth is at least `min_depth` (default 0.15 m). The fill spreads through all eight neighbours,
so two pits touching at a corner are one depression.

Consequences you should know about:

- **Nested basins.** If basin A spills into basin B and the combined lake rises above the
  saddle between them, the saddle is under water and A and B are *one* depression with one
  lowest point. If the lake stops exactly at the saddle height, they are two.
- **Flat floors.** Every cell of a flat floor is equally deep. The reported lowest point is the
  first such cell in row-major order, so it does not depend on how the raster is windowed.
- **Small noise.** There is no minimum area. A single deep cell counts if it is at least
  `min_depth` deep.

## Edges

A depression has `edge = True` when it lies within one cell of the raster border. The border is
an outlet, so the true depression may extend beyond the data and its area and volume are lower
bounds. Cells next to nodata are outlets too, but a depression touching nodata is not flagged:
water really can leave there.

## Tiles

`analyze_tiled` gives the result of `analyze`, whatever the tile size. The tile size trades
memory for speed and never changes a depth, an area or a flag. It does this exactly, not
approximately, in two passes (Barnes, Callaghan and Wheeler 2016):

1. **Pass 1.** Each tile is flooded from its border cells (and from its real outlets), each at
   its own elevation. Every cell learns which seed it was reached from, its *root*. Wherever two
   neighbouring cells have different roots, the larger of their levels is the lowest level at
   which water can cross between those two roots; only the lowest crossing per pair is kept.
2. **Between passes.** Border cells of neighbouring tiles that touch (diagonals included) are
   joined by single steps, and a minimax shortest path from the real outlets gives every border
   cell its exact final level.
3. **Pass 2.** Each tile is flooded again, now starting its border cells at those exact levels.
   Depressions are labelled inside the tile, and pieces that meet across a tile border are
   joined, so a depression crossing many tiles is still one depression with one lowest point.

Why the crossings suffice: take the best path between two border cells inside a tile, and let
M be its highest elevation. Every cell on it joined its root at a level no higher than M, so the
sequence of roots along the path is a chain of crossings each no higher than M. Conversely any
chain of crossings corresponds to a real path no higher than its largest crossing. So the
bottleneck between roots equals the bottleneck between the border cells they came from.

What this costs, measured on 38.9 million pixels of real Copernicus terrain around Austin
(166,882 depressions, 8 cores idle, results identical in every case):

| mode | time | extra memory at peak |
|---|---|---|
| `analyze` | 28 to 30 s | 1.4 to 1.5 GB |
| `analyze_tiled`, 2048-cell tiles | 34 s | 0.50 GB |
| `analyze_tiled`, 1024-cell tiles | 30 s | 0.31 GB |
| `analyze_tiled`, 512-cell tiles | 29 s | 0.43 GB |

Each tile is read and flooded twice, yet the tiled run costs only a few percent more time here,
because its working arrays are smaller and fit the cache better. Memory is one tile plus the
graph, which has a node for every tile-border cell and every outlet cell (a coastline counts), a
few tens of bytes each; very small tiles make that graph the larger part, as the 512-cell row
shows. `Result.labels` and `Result.depth` are not returned, and `id` numbers the depressions 1 to
n in raster-scan order instead of carrying raster label values.

`tests/test_tiling.py` compares both modes depression by depression and cell by cell on random
terrain, terraces full of ties, integer DEMs, a serpentine corridor that routes water through
every tile, a lake with a distant sill, nodata and NaN on and across tile borders, drains,
buildings, masks, geographic and feet-based grids, tiles of 1 cell, and 60 random configurations.
Six deliberate breakages of the algorithm (dropping seam steps, ignoring diagonals, keeping the
highest crossing, and so on) are each caught by that suite. The network-marked tests repeat the
comparison on real terrain: the Austin block above, and also the Dutch coast, Death Valley, the
Alps and Norway's fjords, all identical.

## H3 assignment

Each depression pixel belongs to the H3 cell containing the pixel's centre. A cell's
`depress_area_m2` and `depress_volume_m3` are sums over its pixels and `max_depth_m` is their
maximum. Because these are additive, `Result.at_resolution(r)` rolls cells up to any coarser
resolution exactly. A depression that spans several cells contributes to each of them.

## Coverage

A cell at the edge of the DEM, or next to ignored cells (nodata, or a mask such as open water),
holds depressions from only part of its area. Each row of the cell table therefore carries
`coverage` and `covered_area_m2`.

A pixel belongs to the H3 cell that contains its centre, the same rule that assigns depressions to
cells. The coverage of a cell is the area of its valid pixels divided by the area of all pixel
positions inside it, counting positions beyond the raster as not covered. `covered_area_m2` is the
coverage times the cell's exact H3 area, so a cell wholly inside valid data has coverage 1 and the
full area of the H3 cell.

It is found cheaply: a bitmap with one flag per 16 x 16 block of pixels marks where invalid pixels
are, so only the cells whose bounding box touches a flagged block or leaves the raster are
measured, by assigning each pixel position in the box to its H3 cell. That is exact. A cell larger
than 40,000 pixel positions is measured on a regular sub-lattice anchored to the pixel grid, with a
coverage error well under 1%, and the result never depends on how the raster was tiled. On 38.9
million pixels of real terrain the extra time was about 2 to 3 seconds of 28.

`covered_area_m2` and `depress_area_m2` come from different counts (the exact H3 area against a
sum of whole pixels), so for a cell that is almost entirely depressed their ratio can exceed 1 by a
fraction of a percent. The map clamps it to 1.

Coverage cannot be summed over the cells of a finer table, because that table lists only the cells
that have depressions. `Result.at_resolution` therefore measures it afresh for the coarser cells.
`rollup` on its own, which has no DEM to look at, returns depth, area and volume only.
Rasters wider than 180 degrees of longitude are not handled across the antimeridian.

GLO-30 holds the sea inside its tiles as valid elevation of 0 m, so sea does not reduce coverage:
supply a mask if open water should not count.

## What it cannot tell you

A depression is a feature of the elevation model. It does not know whether water collects there,
whether a culvert the DEM misses drains it, or whether it is a pond. The planned susceptibility
layer (see [roadmap.md](roadmap.md)) is the step towards that question.
