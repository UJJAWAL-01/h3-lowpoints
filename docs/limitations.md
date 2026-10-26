# Accuracy and limits

What the results can be used for, what they cannot, and the known ways they can mislead. If you
find a limit that is not listed here, please open an issue.

## What the results are

A faithful, verified description of the closed depressions **in the elevation model you gave
it**: exact to the model, the same whatever tiling is used, and checked by invariants on every
build ([Verification](verification.md)). Every limit below comes from the gap between the model
and the world, or from choices the library makes and states.

## The results are not a flood map

- A depression is where water **could** collect if nothing removed it. Whether it does depends on
  rain, soil, drains, pumps and what the depression already is (a pond, a reservoir, a quarry, a
  sinkhole, a basement ramp).
- Engineered drainage the model cannot see is the most common reason a depression stays dry:
  storm drain inlets, pipes, culverts. Supplying drains (`drains_xy`) flags depressions with a
  drain at their deep core, but the fill does not route water into them.
- Much of the Netherlands lies below sea level behind dikes and is pumped dry: the library finds
  those polders as large depressions, correctly for the terrain, but they do not flood.
- The library does not estimate how deep or for how long water stands. The susceptibility index
  (below) estimates how much rain fills each depression, as a screening index.

## The susceptibility index

[Susceptibility](susceptibility.md) compares the runoff reaching each depression with its volume.
It inherits every limit above, and adds its own:

- **No drains, pipes, pumps or infiltration timing.** Engineered drainage can keep dry a
  depression the index says fills; this matters most in cities, where most street sags have
  inlets.
- **One runoff coefficient per H3 cell** from imperviousness (Simple Method). It was made for
  typical storms: in rare storms, fields and lawns shed more water than it assumes, so those
  storms are underestimated there. Soils are not used.
- **Volumes, not timing.** A storm enters only through its total depth; how fast it falls and how
  long water stays are not modelled.
- **Spill links from the terrain model**: water passes under roads through culverts the model
  cannot see.
- **Rainfall data.** NOAA Atlas 14 covers the US except Washington and Oregon; where its project
  areas meet, neighbouring grids differ by a few percent. Elsewhere, rain must be supplied.
- **Evidence against observations is not yet measured** (see
  [Susceptibility](susceptibility.md#the-table-and-its-checks)).

## Limits that come from the elevation data

- **Vertical error.** A depression shallower than the data's vertical error may not exist. Keep
  `min_depth` above it: about 0.15 m on good lidar, 0.5 to 1 m on 10 to 30 m data, more on global
  surface models. The library warns when `min_depth` is below 1 m on data coarser than 2 m.
- **Pixel size.** Nothing narrower than a few pixels is seen. A 30 m model cannot show a street
  sag; a 1 m model can. Pixels per H3 cell are kept at four or more by `res="auto"`.
- **Bridges, culverts and overpasses act as dams.** Bare-earth models often keep a road
  embankment across a stream but not the culvert under it, or keep a bridge deck. The fill then
  ponds water upstream of the road: a false depression, sometimes large. Inspect large depressions
  that end at a road or rail line.
- **Buildings.** In lidar, ground under buildings is interpolated and can leave false pits;
  `buildings=` flags depressions mostly under footprints (`likely_building_artifact`). Surface
  models (Copernicus GLO-30) include roofs, so walls and courtyards make depressions that are not
  in the ground: prefer bare-earth data.
- **Water surfaces.** Lakes and rivers appear as flat surfaces with no depth (their bed is not
  measured). Ocean inside a GLO-30 tile is valid elevation 0 m, not nodata: it does not lower
  coverage, and water crosses it to the raster edge like any land, so land below sea level beside
  it fills up to 0 m as a depression. Supply a water mask where this matters.
- **Lakes held by their outlets.** A lake whose outlet is a dam, a set of locks or a narrow river
  appears as one huge depression when the outlet stands higher than the lake surface in the
  data. In the national 3DEP 30 m run, the five largest depressions found were the Great Lakes:
  Lake Superior's surface is a flat 183 m while its outlet at Sault Ste. Marie reads 184 to 186 m,
  so the model fills the whole lake (82,400 km²). Correct for the data, meaningless for ponding:
  mask permanent water (for example the "permanent water bodies" class of ESA WorldCover) before
  reading such results. By contrast, the next largest, the Great Basin of Nevada and the
  Bonneville basin around the Great Salt Lake, really have no outlet.
- **Errors in the elevation data.** A few pixels can hold wrong values: spikes hundreds of metres
  deep, or strips stored as 0 m. They make narrow, impossibly deep depressions, which the
  `deeper_than_wide` column flags (43 of 10.6 million in the national 3DEP 30 m run).
- **Survey seams.** Where two surveys meet, a small step in the data can create or cut off shallow
  depressions along the seam. The library takes each 3DEP pixel from its own tile so it adds no
  seam of its own, but it cannot remove seams in the data.
- **Data age.** The terrain is as it was when surveyed. New construction, grading or erosion since
  then is not reflected.

## Choices the library makes

- **The raster edge is an outlet.** Nothing is known beyond the data, so water may leave there.
  A depression cut by the edge is flagged `edge = True`; its area and volume are lower bounds.
  Analyze a box larger than the area you care about.
- **Nodata and masked pixels are outlets.** Water can leave into a void or a masked lake. This is
  usually right for lakes and the sea; for a void in the middle of land it can split or shrink a
  depression. Such depressions are not flagged as `edge`.
- **Eight-way flow.** Water moves to all eight neighbours, so pits that touch only at a corner
  are one depression.
- **Nested basins.** Basins that fill and overflow into each other below the final water level are
  one depression with one lowest point; their separate structure is not reported.
- **Flat floors.** On a flat bottom the lowest point is the first deepest pixel in reading order,
  a fixed rule, not the "centre" of the floor.
- **No minimum area.** One deep pixel counts as a depression. On noisy data, raise `min_depth`.
- **Coverage on sampled cells.** Cells that touch the edge or nodata and span more than 40,000
  pixels have their coverage measured on a regular sample, with an error well under 1%.

## Things to watch for in your own data

- **Heights must be in metres.** The library does not convert vertical units. A DEM whose heights
  are in feet (common in US state-plane lidar deliveries) gives depths and volumes in feet, in
  columns labelled metres. Convert it first (multiply by 0.3048, or 0.3048006 for US survey feet).
  Horizontal units in feet are handled correctly.
- **Geographic data is treated as WGS 84.** Datum differences (NAD83 against WGS 84) are a metre
  or two, far below a pixel, so they do not matter for depressions.
- **Rotated grids are refused**, and rasters wider than 180° of longitude are not handled across
  the antimeridian. Split such data.
- **Building footprints smaller than 20 square map units are ignored** on projected grids (20 m² in
  a metre CRS, but about 1.9 m² in a feet CRS).

## Limits of H3 itself

- **Children do not nest exactly.** Seven children cover their parent only approximately, so the
  coarse parent of a fine cell is not always the coarse cell containing the same point. This is
  why `h3_lowpoint` (exact at the table's resolution) and the parent of `h3_lowpoint_15` can
  differ near a cell edge. Roll-ups use the H3 parent, as is standard; the totals are exact.
- **Cell areas vary.** H3 cells of one resolution differ in area by up to about a factor of two
  across the globe. Compare cells by shares (`depressed_share`) rather than raw totals across
  large regions; `covered_area_m2` uses each cell's exact area.

## Performance limits

- `analyze` holds the whole raster in memory, about 36 bytes per pixel at peak (1.4 GB for 39
  million pixels); use `analyze_tiled` beyond a few hundred million pixels.
- `analyze_tiled` with `out_dir` handles regions far larger than memory; time grows with the
  number of pixels. Measured figures are in [Large regions](large-regions.md).
- The interactive map embeds its data and draws at most 120,000 cells per resolution
  ([The map](map.md)).
