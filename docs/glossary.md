# Glossary

**Analysis resolution.** The H3 resolution of a result's cell table (`Result.res`). Coarser
resolutions are derived from it by roll-up.

**Bare earth.** An elevation model of the ground with buildings and vegetation removed (also
*digital terrain model*, DTM). The right input for where water flows.

**Block bootstrap.** Estimating uncertainty by resampling whole areas (blocks) rather than single
points, so that points that cluster together are not counted as independent evidence. See
[Validation](validation.md#uncertainty-the-block-bootstrap).

**Bounding box (bbox).** A rectangle in longitude and latitude, given as west, south, east, north
in degrees.

**Catchment.** The land whose rain ends up in a given depression. See
[Definition](definition.md#catchments-and-spill).

**Cell.** In this documentation, an H3 hexagon. The squares of a raster are called *pixels*.

**Closed depression.** An area from which water cannot flow out until it fills to the lowest
point of its rim. Also called a sink, pit or low point.

**Cloud-optimized GeoTIFF (COG).** A GeoTIFF arranged so programs can read just the part they
need over the internet.

**Control points.** In validation, points standing for where observations could have come from
(addresses, buildings), against which observations are compared.

**Core (of a depression).** Its pixels at least `drain_core_frac` (default 80%) of its maximum
depth deep; used by `drain_at_core`.

**Coverage.** The share (0 to 1) of an H3 cell for which the elevation data has valid values.

**Covered area.** Coverage times the cell's exact area, in square metres.

**CRS (coordinate reference system).** What a raster's coordinates mean: metres or feet on a
map projection, or degrees of longitude and latitude.

**Depressed share.** A cell's depressed area divided by its covered area.

**Depth.** At a pixel: the filled level minus the ground height. Of a depression: the depth at
its deepest pixel (`max_depth_m`).

**DEM (digital elevation model).** A raster of heights. Covers both bare-earth and surface models.

**Drained depression.** One with a sign of drainage in mapped lines: a stream or canal at its
lowest point or across most of its cells, or a road (likely with a culvert) where it overflows.
See [Susceptibility](susceptibility.md#drained-or-landlocked).

**DSM (digital surface model).** An elevation model of the top surface, including roofs and tree
canopies. Copernicus GLO-30 is one.

**Edge depression.** A depression within one pixel of the raster border (`edge = True`). It may
continue beyond the data, so its area and volume are lower bounds.

**Exact tiling.** Analyzing a raster in tiles in a way that gives exactly the result of
analyzing it whole (`analyze_tiled`).

**Fill / filled level.** The lowest level water could stand at in each pixel, found by
priority-flood.

**Fill curve.** How a depression's flooded area and stored volume shrink as its water level
drops below full. See [Definition](definition.md#levels-shape-and-fill-curve).

**Fill ratio.** The water reaching a depression in a storm (its own runoff plus overflow from
upstream) divided by its volume; 1 or more means it fills. See
[Susceptibility](susceptibility.md#design-storms).

**Footprint.** Where a raster holds valid data; used to measure coverage of any H3 cell.

**Imperviousness.** The percent of the ground that is paved or built, so water cannot soak in.
NLCD gives it for every 30 m pixel of the lower 48 US states.

**H3.** A global grid of hexagons with 16 resolutions, from Uber. See [Concepts](concepts.md#h3).

**H3 id.** A cell's identifier: a 64-bit number written as 15 hexadecimal characters, such as
`8a489e34677ffff`.

**Invariant.** A property every correct result has, whatever the terrain (for example: cell areas
add up to depression areas). Checked by `h3lowpoints verify`.

**Landlocked depression.** One with no sign of drainage in the mapped lines given: water
reaching it can only soak in or evaporate, unless it is drained by something not mapped (storm
drains, pipes).

**Lidar.** Light detection and ranging: elevation measured from aircraft with laser pulses, the
source of 1 m elevation models.

**Lift.** In validation, the share of observations within a distance of depressed ground divided
by the same share for control points. 1 means no more often than the controls.

**Lowest point.** A depression's deepest pixel. Ties go to the first in reading order.

**Mask.** A raster marking pixels to ignore, such as open water. Masked pixels act like nodata.

**min_depth.** The shallowest depression kept, in metres.

**NHD.** The US National Hydrography Dataset: mapped streams, rivers, canals, ditches,
conduits and pipelines.

**Nodata.** A pixel with no valid height. Treated as a place water can leave.

**Outlet.** A place water can leave the model: the raster edge, pixels next to nodata or a mask.

**Parent / child.** An H3 cell's parent is the cell one resolution coarser that contains it; the
children are about seven cells one resolution finer.

**Parquet.** A compressed, typed table format read by pandas, DuckDB, Spark and most data tools.

**Partition.** In a disk-backed run, a group of cells sharing one coarse H3 parent, processed
together to bound memory.

**Pixel.** One square of a raster.

**Priority-flood.** The algorithm that computes the fill exactly by always growing the water from
its lowest edge.

**Rain to fill.** The uniform rain depth that fills a depression, from its own catchment alone
(`rain_to_fill_mm`) or with the overflow from upstream (`rain_to_fill_upstream_mm`). See
[Susceptibility](susceptibility.md#rain-to-fill).

**Raster.** A grid of values stored as an image-like file, usually GeoTIFF.

**Resolution (H3).** The size class of H3 cells, 0 (largest) to 15 (smallest).

**Return period.** For a storm, the average number of years between storms at least that large:
the 100-year storm has a 1% chance of being reached or exceeded in any year. NOAA Atlas 14 gives
storm depths for return periods of 1 to 1000 years.

**Ring (H3).** The cells at exactly k steps from a cell form its k-th ring; "within k rings"
means at most k steps away. One step is about 131 m at resolution 10.

**Roll-up.** Computing a coarser cell table from a finer one: maximum of depths, sums of areas,
volumes and counts.

**Runoff coefficient.** The share of the rain on the ground that runs off its surface rather than
soaking in; here `0.05 + 0.009 × imperviousness` (the Simple Method). See
[Susceptibility](susceptibility.md#runoff).

**Spill point.** The lowest point of a depression's rim, where it overflows when full. With
`catchments=True` it is reported (`spill_lat`, `spill_lon`), with the depression the overflow
reaches (`spill_to`).

**Spill cascade.** Depressions filling and passing their overflow, along the spill links, to
the depressions they spill into. See [Susceptibility](susceptibility.md#the-cascade).

**Spot check.** Re-running random windows of a raster whole and tiled to confirm they agree.

**Tile.** A square piece of a raster processed at once by `analyze_tiled`.

**VRT (virtual raster).** A small text file that presents many raster files as one, without
copying them.

**Volume.** The water a depression holds when full: depth times area summed over its pixels.
