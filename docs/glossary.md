# Glossary

**Analysis resolution.** The H3 resolution of a result's cell table (`Result.res`). Coarser
resolutions are derived from it by roll-up.

**Bare earth.** An elevation model of the ground with buildings and vegetation removed (also
*digital terrain model*, DTM). The right input for where water flows.

**Bounding box (bbox).** A rectangle in longitude and latitude, given as west, south, east, north
in degrees.

**Cell.** In this documentation, an H3 hexagon. The squares of a raster are called *pixels*.

**Closed depression.** An area from which water cannot flow out until it fills to the lowest
point of its rim. Also called a sink, pit or low point.

**Cloud-optimized GeoTIFF (COG).** A GeoTIFF arranged so programs can read just the part they
need over the internet.

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

**DSM (digital surface model).** An elevation model of the top surface, including roofs and tree
canopies. Copernicus GLO-30 is one.

**Edge depression.** A depression within one pixel of the raster border (`edge = True`). It may
continue beyond the data, so its area and volume are lower bounds.

**Exact tiling.** Analyzing a raster in tiles in a way that gives exactly the result of
analyzing it whole (`analyze_tiled`).

**Fill / filled level.** The lowest level water could stand at in each pixel, found by
priority-flood.

**Footprint.** Where a raster holds valid data; used to measure coverage of any H3 cell.

**H3.** A global grid of hexagons with 16 resolutions, from Uber. See [Concepts](concepts.md#h3).

**H3 id.** A cell's identifier: a 64-bit number written as 15 hexadecimal characters, such as
`8a489e34677ffff`.

**Invariant.** A property every correct result has, whatever the terrain (for example: cell areas
add up to depression areas). Checked by `h3lowpoints verify`.

**Lidar.** Light detection and ranging: elevation measured from aircraft with laser pulses, the
source of 1 m elevation models.

**Lowest point.** A depression's deepest pixel. Ties go to the first in reading order.

**Mask.** A raster marking pixels to ignore, such as open water. Masked pixels act like nodata.

**min_depth.** The shallowest depression kept, in metres.

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

**Raster.** A grid of values stored as an image-like file, usually GeoTIFF.

**Resolution (H3).** The size class of H3 cells, 0 (largest) to 15 (smallest).

**Roll-up.** Computing a coarser cell table from a finer one: maximum of depths, sums of areas,
volumes and counts.

**Spill point.** The lowest point of a depression's rim, where it overflows when full.

**Spot check.** Re-running random windows of a raster whole and tiled to confirm they agree.

**Tile.** A square piece of a raster processed at once by `analyze_tiled`.

**VRT (virtual raster).** A small text file that presents many raster files as one, without
copying them.

**Volume.** The water a depression holds when full: depth times area summed over its pixels.
