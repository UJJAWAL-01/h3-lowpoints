# Concepts

This page explains the ideas behind h3-lowpoints without assuming any GIS or hydrology
background. Each section ends with what it means for the library. The precise rules are in
[Definition](definition.md).

## Elevation models

An **elevation model** is a grid of numbers, each giving the height of the ground at one spot.
Each square of the grid is a **pixel** (also called a *cell* of the raster; this documentation
says *pixel* to keep it apart from H3 cells). The grid is stored as a **raster** file, usually a
GeoTIFF.

Three properties of an elevation model decide what can be learnt from it:

- **Pixel size.** The ground distance one pixel spans: 1 m for airborne lidar, 10 m or 30 m for
  national and global products. Nothing smaller than a few pixels can be seen.
- **Vertical accuracy.** How far the heights can be from the truth: about 10 cm for good lidar, a
  metre or more for 30 m data, and several metres for global satellite products. A depression
  shallower than the vertical error may not be real.
- **What surface it measures.** A **digital terrain model** (DTM, also *bare earth*) is the
  ground with buildings and trees removed. A **digital surface model** (DSM) is the top of
  whatever is there: roofs, tree canopies. Water flows on the ground, so a bare-earth model is the
  right input. USGS 3DEP is bare earth; Copernicus GLO-30 is a surface model.

Some pixels hold no value, called **nodata**: outside the surveyed area, over water the sensor
could not measure, or where the data was removed. The library treats nodata as a place water
can leave (see [Outlets](#outlets) below).

A raster also has a **coordinate reference system** (CRS), which says what its coordinates mean.
Either the pixels are laid out in metres (or feet) on a flat map projection such as UTM, or in
degrees of longitude and latitude. The library handles both. For degree grids it computes each
pixel's true ground area on the WGS 84 ellipsoid, because a degree of longitude shrinks towards
the poles, so there is no need to reproject (resample) the data, which would blur it.

**For the library:** use the finest bare-earth model you have, and set `min_depth` above its
vertical error.

## Closed depressions

Pour water on the ground and it runs downhill until it either leaves (into a river, off the edge
of the map) or reaches a spot with higher ground on every side, where it pools. Such a spot is a
**closed depression** (also *sink*, *pit* or *low point*): a hollow from which water cannot flow
out until it fills to the level of the lowest point on its rim, the **spill point**.

```
  A slice through the ground:

                                          spill point (the lowest place on the rim)
     __                                   v
       \___                               ______
           \~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~/      \___     ground continues downhill:
            \       water surface        /          \___  water that spills goes away
             \__     when full (flat)   /
                \__         |        __/
                   \___     | depth /
                       \___ v   ___/
                           \___/
                       lowest point
```

Real examples: a pond, a quarry, a sinkhole, a sag in a road between two humps, a low corner of a
car park, a polder below sea level behind a dike, a basin in a desert with no outlet.

Three numbers describe a depression:

- **Depth** at a spot: how much water stands there when the depression is full. The **maximum
  depth** is the depth at the **lowest point**.
- **Area**: the ground area that would be under water.
- **Volume**: how much water it holds when full (the sum of depth times area over its pixels).

**For the library:** each depression is one row of the depression table, with its maximum
depth, area, volume and lowest point.

## Filling: how depressions are found

Imagine raising the water level everywhere at once, starting from all the places water can
leave. The water spreads inland, always climbing over the lowest available rim first. When it
reaches a hollow from a rim, the hollow fills to that rim's height. At the end every pixel has a
**filled level**: the lowest height water could stand at there. Subtract the ground height and
you get the **depth**. Pixels with depth zero drain freely; pixels with positive depth lie in a
depression.

The algorithm that does this efficiently is called **priority-flood**: it keeps a queue of the
lowest unprocessed pixels on the water's edge and always grows from the lowest one. It is exact,
and it moves through all eight neighbours of each pixel (diagonals too).

Two consequences:

- **Nested basins merge when they overflow together.** If basin A fills up and spills into basin
  B, and the combined water rises over the saddle between them, A and B are one depression with
  one lowest point. If the water stops exactly at the saddle, they are two.
- **A flat-bottomed depression has many equally deep pixels.** The library reports the first one
  in reading order (top row first, left to right), so the answer never depends on how the raster
  was cut into pieces.

### Outlets

Water leaves the map at an **outlet**. The library treats as outlets:

- the **edge of the raster**: nothing is known beyond it, so water is allowed to leave;
- pixels **next to nodata** or a **mask** (for example a lake you asked to ignore);
- **drains** you supply, such as storm drain inlets (optional).

A depression cut by the raster edge is flagged `edge = True`: its true size is unknown, and its
area and volume are only lower bounds. A depression next to nodata is not flagged, because water
really can leave there in the model.

### min_depth

Elevation data is noisy, so filling finds countless depressions a few centimetres deep that mean
nothing. `min_depth` (default 0.15 m) drops every depression whose maximum depth is below it.
There is no minimum area: a single pixel counts if it is deep enough.

**For the library:** depth is filled level minus ground; a depression is a connected area of
positive depth whose deepest point reaches `min_depth`.

## H3

[H3](https://h3geo.org) is a system, created at Uber and open source, that divides the whole
Earth into hexagons. It is used because hexagons tile evenly, every neighbour is the same
distance away, and each cell has a short **id** that anyone can use to join data without
sharing shapes.

- **Resolutions.** H3 has 16 resolutions, 0 (coarsest) to 15 (finest). Each step makes cells
  about seven times smaller in area:

  | res | average cell area | average edge | roughly |
  |---|---|---|---|
  | 0 | 4.36 million km² | 1,281 km | a subcontinent |
  | 3 | 12,393 km² | 69 km | a large county |
  | 5 | 253 km² | 9.9 km | a city |
  | 7 | 5.16 km² | 1.4 km | a neighbourhood |
  | 9 | 0.105 km² (10.5 ha) | 201 m | a few city blocks |
  | 10 | 15,048 m² | 76 m | a city block |
  | 12 | 307 m² | 11 m | a house and garden |
  | 14 | 6.3 m² | 1.6 m | a parking space |
  | 15 | 0.9 m² | 0.6 m | a paving slab |

- **Ids.** Each cell has a 64-bit id, usually written as 15 hexadecimal characters such as
  `89489e34677ffff`. The id encodes the resolution and the cell's place in the hierarchy.
- **Hierarchy.** Every cell has one **parent** at each coarser resolution. Seven children cover
  roughly, but not exactly, the area of their parent: hexagons cannot be split into smaller
  hexagons perfectly, so a child's edge can stick out slightly. This matters in one place,
  explained under `h3_lowpoint_15` in [Outputs](outputs.md).
- **Pentagons.** Twelve cells at every resolution are pentagons. They sit in the oceans and
  rarely matter for land analysis.

### From pixels to cells

Each depressed pixel is assigned to the H3 cell that contains the pixel's **centre**. A cell's
values are then:

- the **maximum depth** over its depressed pixels,
- the **sum of area** and the **sum of volume** of its depressed pixels,
- the **number of lowest points** (depressions whose deepest pixel lies in it).

A depression larger than a cell contributes to several cells; a cell can hold parts of several
depressions. Because maxima and sums can be recombined, a coarser table is computed exactly from
a finer one (a **roll-up**), with no new analysis.

### Choosing a resolution

An H3 cell smaller than a few elevation pixels would hold one or two pixels, and its numbers
would be noise. The library's rule: a cell must hold at least **four pixels** on average. By
default (`res="auto"`) it uses the finest resolution that satisfies the rule:

| pixel size | finest resolution (auto) |
|---|---|
| 1 m (lidar) | 14 |
| 2 m | 13 |
| 5 m | 12 |
| 10 m | 11 |
| 30 m | 10 |
| 90 m | 9 |

You can ask for any resolution from 0 to 15; a finer one than the data supports gives a warning.

Every depression also carries the resolution-15 cell of its lowest point (`h3_lowpoint_15`), so
its location is kept to the finest precision H3 has, whatever resolution the table uses. That
location is still only as precise as one elevation pixel.

**Very coarse cells summarize.** A cell at resolution 0 to 3 covers 12,000 km² or more and holds
thousands of unrelated depressions; its single deepest depth says little. Read those cells for
**shares and counts** (`depressed_share`, `n_lowpoints`), which is what the map shows there by
default.

## Coverage

A cell at the edge of the data, or next to nodata or a masked lake, was only partly measured. Its
depressed area is then low because less ground was seen, not because the ground is flatter.
Every cell therefore reports its **coverage**: the share of the cell (0 to 1) that the elevation
data covers, and the **covered area** in square metres. Compare cells by `depressed_share` (the
depressed area divided by the covered area), not by raw totals, when coverage differs.

## A depression is not a flood

The library describes the shape of the terrain model. It does not know about rain, soil,
drains the model cannot see (culverts, pipes), pumps, or whether a depression is already a pond.
A road underpass, a quarry, a sinkhole and a reservoir are all depressions. Much of the
Netherlands lies below sea level behind dikes and is pumped dry: it appears as large depressions
and does not flood. Use the results as a map of where water *could* collect if nothing removed
it, and as an input to further analysis. The [susceptibility layer](susceptibility.md) is the
step towards asking where water actually collects: which storm fills each depression, and
whether it has a mapped way out.
