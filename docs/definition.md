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

## Edges and tiles

Two flags describe depressions the data cannot fully describe. They mean different things.

**`edge`**: the depression lies within one cell of the data border (the raster border, or the
border of a tile's window). The border is an outlet, so the true depression is cut off there.
Depressions with `edge = True` are never classed as significant or suspect pits.

**`level_uncertain`**: only in tiled runs. A tile treats its border as an outlet, so a lake that
really spills beyond the buffer is seen at a lower level, and one depression can come back as
several pieces. A depression is flagged when its water level was set through a path to a tile
border that is not a real edge of the data. This is a *could differ* flag, not a *does differ*
flag: it marks everything that more data might change, so it is conservative and does not
suppress any class. Whole-raster runs never set it.

How conservative? On the Austin pilot area (3368 x 3884 cells, tiles of 1500), against a
whole-raster run:

| buffer | flagged `level_uncertain` | depressions that actually differ | classes that differ |
|---|---|---|---|
| 64 | 1238 of 1629 | 2 changed values, 4 extra fragments (all flagged) | 0 |
| 256 | 1179 of 1625 | 0 | 0 |

So at the default buffer nearly everything is flagged although nothing changed. Treat the flag as
a bound, and use the buffer to tighten it: with a buffer wider than every lake, a tiled run
equals `analyze` exactly (`tests/test_tiling.py`), and unflagged depressions are never wrong
(`test_narrow_buffer_never_returns_a_wrong_unflagged_depression`). An exact tiled algorithm that
removes the need for a buffer is on the roadmap (see CONTRIBUTING.md).

## H3 assignment

Each depression pixel belongs to the H3 cell containing the pixel's centre. A cell's
`depress_area_m2` and `depress_volume_m3` are sums over its pixels and `max_depth_m` is their
maximum. Because these are additive, `Result.at_resolution(r)` rolls cells up to any coarser
resolution exactly. A depression that spans several cells contributes to each of them.

## What it cannot tell you

A depression is a feature of the elevation model. It does not know whether water collects there,
whether a culvert the DEM misses drains it, or whether it is a pond. See
[waterlogging.md](waterlogging.md) for the optional screen built on top.
