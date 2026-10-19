# Roadmap

The library works anywhere in the world on any elevation data you supply. The published data
products it builds (a national depression layer, and later a susceptibility layer) cover the
**United States**, where free, bare-earth, nationally consistent elevation data exists (USGS
3DEP). Everything is meant to cost nothing to run or to host: results are published as open
data files and static web pages.

Each step is finished, tested and documented before the next starts. Nothing is released yet.

## Done

**The depression layer, at any resolution**

- Priority-flood fill and 8-connected depressions, checked against an independent method.
- Native longitude/latitude grids with exact ellipsoid pixel areas; projected grids in metres or
  feet.
- Every H3 resolution: `res="auto"` picks the finest the data supports, every lowest point is
  also given at resolution 15, and coarser tables are exact roll-ups.
- Coverage per cell, so partly covered cells are never read as less depressed.
- Masks for water or areas to ignore.
- An interactive 3D web map, self-contained, shareable by link.

**Regions of any size**

- Exact tiled analysis (`analyze_tiled`): identical to the whole-raster result for any tile size.
- Bounded memory: per-tile summaries, per-partition spill files, tables written to disk as they
  are produced, CSV or Parquet output. Memory no longer grows with the size of the output.
- `h3lowpoints build`: any US box straight from 3DEP tiles, verified on completion.

**Data readers**

- USGS 3DEP 30 m, 10 m and 1 m lidar; Copernicus GLO-30 worldwide; NLCD imperviousness; ESA
  WorldCover; NOAA Atlas 14 design rainfall.

**Verification anyone can run**

- Invariants for any result (`h3lowpoints verify`, also in pieces for national results), spot
  checks of the tiling on the user's own data, and oracle and mutation tests in the suite.

## Next

1. **National US layer.** Run `build` over the conterminous US from 3DEP 30 m (11 to 13
   billion pixels; estimated 3 hours and under 10 GB of memory on a laptop, see
   [Large regions](large-regions.md)), verify it, and publish the tables (Parquet) as open data on Zenodo with a DOI, plus
   coarse summary maps. Resolution and `min_depth` will be chosen from the measured cost and the
   data's vertical accuracy, and documented with the release.
2. **General validation tools.** Score any set of observed locations (flood reports, high-water
   marks, insurance claims, anything with coordinates) against the depressions: how much more
   often observations fall in or near depressions than random locations do, with confidence
   intervals that respect spatial clustering (block bootstrap). Works for any region and any
   observations; never tuned to one city.
3. **Physical susceptibility index.** For each depression, compare the water it could receive
   with the storage it has:
   - its contributing catchment, from flow routing on the filled surface;
   - runoff = design rainfall (Atlas 14) × catchment area × a runoff coefficient from land cover
     and imperviousness (NLCD, WorldCover);
   - storage = the depression's volume;
   - the fill ratio, runoff / storage, is the index.

   Every term can be inspected, and the index needs no training labels. It will be validated with
   the tools of step 2 and documented with its evidence and its limits.
4. **Machine learning, only if it earns its place.** Train on the step 3 features, judge it by
   leave-one-region-out validation (never scored on the place it learned from), and keep it only
   if it beats the physical index on unseen regions. Labels of street-level waterlogging are
   scarce and biased towards where people report; that is audited first.
5. **The explorer web app.** A website in the spirit of h3geo.org, in a black-and-white design,
   that runs entirely in the visitor's browser (no server, no cost): try the analysis on a small
   box, browse the national layer at every resolution, view depressions flat, as wells below the
   ground or as columns, and read this documentation.
6. **Release 0.1.** Publish on PyPI, the documentation and app on GitHub Pages, and the data on
   Zenodo.

## Also planned

- **Vertical units.** An option to read heights in feet (or any factor), with the unit recorded
  in `meta.json`; today heights must be in metres ([Accuracy and limits](limitations.md)).
- **Continental scale beyond one machine.** The border graph of the exact tiling grows with the
  number of tile-border pixels; a hierarchical version (blocks of tiles joined the same way) would
  remove that last limit for continents.
- More data readers: other national lidar programs, FABDEM, water masks (JRC Global Surface
  Water).

## H3 resolution policy

| H3 res | Cell area | What is shown |
|---|---|---|
| 0 to 3 | 12,000 km² and up | Summaries only: share of the covered land that is depressed, counts, total volume. Never "hotspots": a cell this large holds thousands of unrelated features. |
| 4 to 6 | 36 to 1,800 km² | Regional summaries |
| 7 to 9 | 0.1 to 5 km² | Neighbourhood patterns, where the data supports them |
| 10 and finer | 0.015 km² and less | Individual depressions; 12 and finer only with data of 5 m or finer |

The library never reports a resolution finer than the data supports without a warning (cells must
hold at least four pixels).
