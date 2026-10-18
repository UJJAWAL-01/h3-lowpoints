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

A depression whose extent or water level is not fully known has `edge = True`:

- it lies within one cell of the raster border (the border is an outlet, so the true depression
  is cut off), or
- in a tiled run, its water level was set through a path to a tile border that is not a real
  edge of the data. More data beyond that border could raise the level.

Depressions with `edge = True` are never classed as significant or suspect pits. The tile flag is
**conservative**: it marks every depression that could change, not only those that do. With a
buffer wider than any lake, a tiled run reproduces the whole-raster run exactly, including which
depressions are flagged (`tests/test_tiling.py`). A tile that sees a lower lake than the whole
raster does can split one depression into several; those pieces are always flagged.

## H3 assignment

Each depression pixel belongs to the H3 cell containing the pixel's centre. A cell's
`depress_area_m2` and `depress_volume_m3` are sums over its pixels and `max_depth_m` is their
maximum. Because these are additive, `Result.at_resolution(r)` rolls cells up to any coarser
resolution exactly. A depression that spans several cells contributes to each of them.

## What it cannot tell you

A depression is a feature of the elevation model. It does not know whether water collects there,
whether a culvert the DEM misses drains it, or whether it is a pond. See
[waterlogging.md](waterlogging.md) for the optional screen built on top.
