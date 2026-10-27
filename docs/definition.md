# What is computed

This page is the contract for the core of the library: what a "depression" is, and what the
results mean at the edges. Behaviour described here is covered by the tests in `tests/`. For a
gentler introduction see [Concepts](concepts.md); for every output column see
[Outputs](outputs.md).

## The fill

`h3lowpoints.hydro.depressions.depth_from_array` runs a priority-flood fill. Starting from
every outlet cell, it raises each cell to the lowest level water could stand at there. Outlets are

- cells on the raster border,
- cells next to nodata (a void or masked area is treated as a place water can leave).

`depth_from_array` also accepts a `drains` raster of extra outlets for custom workflows. The
`analyze` functions do not use it: drains supplied as `drains_xy` are reported on
(`dist_to_drain_m`, `drain_at_core`) but do not drain water in the fill, so a depression with a
drain still appears, flagged, rather than disappearing.

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

## Catchments and spill

With `catchments=True` (`--catchments` on the command line), every analysis also follows the
water: where rain on each pixel ends up, and where each depression overflows when it is full
(`h3lowpoints.hydro.routing`). The rules depend only on the terrain, never on the order in which
pixels are processed:

1. **Sinks.** Every 8-connected region of positive depth is a sink, however shallow. Water
   landing on a sink stays in it.
2. **Outlets.** Valid pixels on the raster border or next to an invalid pixel (nodata, NaN, a
   mask) are outlets, as in the fill. Water reaching one leaves the map.
3. **Steepest descent.** Every other pixel passes its water to the neighbour with the steepest
   drop in *ground* height: the drop divided by the distance between the pixel centres in
   metres (on longitude/latitude grids the east-west and north-south spacings differ and are
   measured per row). Ties go to the first neighbour in the order north-west, north,
   north-east, west, east, south-west, south, south-east.
4. **Flats.** A pixel with no lower neighbour lies on a flat: the connected non-sink pixels of
   the same height. It passes its water to the neighbour on the flat that is one step nearer to
   the flat's nearest way off (a pixel with a lower neighbour, or an outlet), counting
   8-neighbour steps; ties as in rule 3.
5. **Spill.** When a sink is full, its water surface joins every pixel of the same filled level
   connected to it. It overflows at that region's way off (a pixel with a neighbour of lower
   filled level, or an outlet) that comes first in reading order, onto that pixel's neighbour
   with the steepest drop in filled level, and from there the water follows rules 1 to 4.

**Depressions and shallow sinks.** Depressions are the sinks at least `min_depth` deep. A
shallower sink fills almost at once, so its water is passed on along its spill path. A
depression's **catchment** is therefore the land whose water reaches it first, plus the land
draining to every shallow sink that spills into it. Rain falling on the depression itself is
part of its catchment. Every valid pixel drains to exactly one depression or to an outlet.

**Spill links.** A depression's `spill_to` is the depression that finally receives its overflow
(through any chain of shallow sinks), or 0 when the overflow leaves the map. The receiving
depression is always lower (its filled level is strictly below), so overflow never runs in a
loop. Two depressions whose full surfaces meet at the same level share one water surface and the
same spill point.

**Choices made, and their limits.** Water follows one path per pixel (steepest descent, as in
the D8 method), not a spread over several neighbours; for catchments, which add up whole
pixels, this is the standard choice. A full depression with several equally low ways off
spills at the first in reading order only. Pipes, culverts and drains are not in the elevation
data: a road embankment over a culvert holds water back in the model. Checked in
`tests/test_routing.py` against an independent implementation of the same rules, pixel by
pixel, on smooth terrain, terrain rounded into flats and ties, nodata, and longitude/latitude
grids; three deliberately wrong variants of the rules (ties broken the other way, drop not
divided by distance, routing on the filled surface) are each detected.

**Tiled.** `analyze_tiled` and `build` give exactly the same catchments and spill links,
whatever the tile size (`h3lowpoints.hydro.tiled_routing`). Each tile is processed with a
one-pixel rim of its neighbours, whose filled levels the fill's border solve already knows, so
every rule that looks at a pixel's neighbours is applied exactly. Water is followed inside the
tile until it reaches a sink, an outlet, a pixel of another tile, a flat that continues into
another tile, or the full surface of a sink that continues into another tile. Shallow sinks that
lie wholly inside a tile pass their water on there already. Once every tile is done, the flats
that cross tile borders are solved as a whole (rule 4 over all their pixels), full surfaces that
cross borders are joined and take their first exit across all tiles (rule 5), and every path is
followed to its end. `tests/test_tiled_routing.py` compares tiled and whole on random terrain,
terrain rounded into flats and ties, nodata, masks, longitude/latitude grids, a perfectly flat
corridor winding through every tile, and a lake whose overflow runs through a tile that holds no
sink, at tile sizes down to 4 pixels; on the 30 m elevation data of Houston (1.36 million
pixels) tiles of 100, 333 and 512 pixels give results identical to the whole raster.

## Levels, shape and fill curve

With `details=True` (`--details`), each depression is described further
(`h3lowpoints.hydro.details`), identically by the whole-raster and the tiled analyses:

- **Levels.** `ground_m` is the ground height at the lowest point and `water_level_m` the level
  of the water when the depression is full (its filled level, the same over the whole
  depression). Their difference is `max_depth_m`. Heights are in the elevation data's vertical
  datum (for USGS 3DEP, NAVD 88 metres).
- **Fill curve.** With the water `t` metres below full (t = 0.1, 0.25, 0.5, 1, 2 and 5 m), a
  pixel of depth `d` is under water when `d > t` and holds `d - t` of water. The flooded area and
  the volume at each level are sums over the depression's pixels. With the curve, a volume of
  water can be turned into a level and a flooded area by interpolation (the rain simulator
  planned for the web app does this). The depression is treated as **one pool**: when it is
  only partly full, water is assumed to stand at one level everywhere in it, although separate
  hollows inside a large depression may in reality fill one by one.
- **Shape.** `length_m` and `width_m` are the axes of the ellipse with the same second moments
  as the depression's pixels (each pixel a uniform rectangle of its ground size, weighted by its
  area), 4 standard deviations each; `orientation_deg` is the direction of the long axis,
  clockwise from north, from 0 to 180. A one-pixel depression on 30 m data is 34.6 m by 34.6 m;
  a straight canal one pixel wide and n pixels long is about 1.15 × n pixels long and 34.6 m
  wide. The direction of a depression that is about as wide as it is long means little. On
  longitude/latitude grids, pixel sizes are taken at the depression's centre row, so very large
  depressions spanning many degrees of latitude are measured approximately.

The moments are kept per piece (weight, centre, centred sums) and merged exactly when pieces of
one depression from different tiles are joined. `tests/test_details.py` checks a one-pixel pit,
canals in four directions, a rectangle, every fill-curve value against the depression's own
pixels, north-up and south-up grids, and tiled against whole on random terrain, flats, nodata
and longitude/latitude grids; the network test repeats the comparison on Houston 30 m data.

## Tiles

`analyze_tiled` gives the result of `analyze`, whatever the tile size. The tile size trades
memory for speed and never changes a depth, an area or a flag. It does this exactly, not
approximately, in two passes (Barnes, Callaghan and Wheeler 2016):

1. **Pass 1.** Each tile is flooded from its border cells (and from its real outlets), each at
   its own elevation. Every cell learns which seed it was reached from, its *root*. Wherever two
   neighbouring cells have different roots, the larger of their levels is the lowest level at
   which water can cross between those two roots; only the lowest crossing per pair is kept.
   Seeds inside the tile (cells beside nodata, a mask or open water) are outlets: their level is
   their own elevation, so they are merged into one outlet node per tile, keeping each region's
   lowest crossing to any of them. A crossing into an outlet is never lower than the outlet's
   elevation, so this gives the same levels, and the graph no longer grows with the length of
   coastlines and lake shores.
2. **Between passes.** Border cells of neighbouring tiles that touch (diagonals included) are
   joined by single steps, and a minimax shortest path from the real outlets gives every border
   cell its exact final level.
3. **Pass 2.** Each tile is flooded again, now starting its border cells at those exact levels
   (its seeds are found again from the tile, exactly as in pass 1; outlets start at their own
   elevation).
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
graph, which has a node for every tile-border cell and one outlet node per tile, a few tens of
bytes each; very small tiles make that graph the larger part, as the 512-cell row shows (that
measurement predates the outlet nodes, when every outlet cell was a node). On a box of western
Lake Erie with open water masked, merging the outlets shrank the graph from 111,962 nodes and
314,818 crossings to 9,664 and 32,378, with identical results. `Result.labels` and `Result.depth` are not returned, and `id` numbers the depressions 1 to
n in raster-scan order instead of carrying raster label values.

For large regions the output itself becomes the larger part of the memory. How the tiled run
keeps it bounded (per-tile summaries, partitions spilled to disk, tables written as they are
produced) and what a state-sized run costs are described in [Large regions](large-regions.md).

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

## Resolution

`res="auto"` (the default) chooses the finest H3 resolution whose cells hold at least four DEM
pixels: 14 for 1 m lidar, 13 for 2 m, 11 for 10 m, 10 for 30 m. A finer resolution can be asked
for, with a warning, but its cells would hold less than the DEM measured.

Whatever the table's resolution, every depression also carries `h3_lowpoint_15`, the
resolution-15 cell of its deepest pixel's centre. Its location is only as precise as the DEM's
pixel: on a 30 m DEM it marks a 1 m cell inside a 30 m pixel. H3 cells do not nest exactly, so the
parent of `h3_lowpoint_15` at the table's resolution can occasionally differ from `h3_lowpoint` near a
cell edge; `h3_lowpoint` is the one that matches the cell table.

Resolutions 0 to 3 (cells of 12,000 km² and more) hold thousands of unrelated features. Read them
as summaries: `depressed_share` and `n_lowpoints`, not one deepest depth. The map opens those
levels on the share. `resolution_guide(dem)` lists, for a DEM, each resolution's cell area, pixels
per cell and recommended use.

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
measured, by assigning each pixel position in the box to its H3 cell. In a tiled run, which
pixels are invalid is recorded per tile during pass 1, so this never reads the elevation data a
second time. That is exact. A cell larger
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
whether a culvert the DEM misses drains it, or whether it is a pond. The
[susceptibility layer](susceptibility.md) is the step towards that question (which storm fills
it, and whether a mapped stream, canal or highway drains it). The full list of limits is in
[Accuracy and limits](limitations.md).
