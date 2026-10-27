# Changelog

## Unreleased

### Added
- **Susceptibility: how easily each depression fills** (`susceptibility.susceptibility` and
  `write_susceptibility`; `h3lowpoints index`), for any result computed with catchments. Per
  depression: the SCS curve number of its catchment (`curve_number`, the area-weighted mean over
  its H3 cells), the rain that fills it from its own catchment (`rain_to_fill_mm`, the exact
  inverse of the SCS runoff equation) and with the overflow of the depressions upstream
  (`rain_to_fill_upstream_mm`, exact to within 1%), and, with design storms, the share of its
  volume each storm brings (`fill_ratio_2yr`, `fill_ratio_10yr`, `fill_ratio_100yr`) and the
  smallest return period whose storm fills it (`fills_in_years`). Overflow passes down the spill
  links (`hydro.cascade`), conserving water. Inputs covering too little of a catchment give NaN,
  passed on downstream. Saved as `susceptibility.csv` or `.parquet` with its settings in
  `meta.json`; read back by `read_result`, carried by `clip`, and checked by `verify` (nine new
  damages caught). Checked against TR-55 worked by hand, a plain recursive cascade, a bisection,
  and a uniform storm. See [docs/susceptibility.md](docs/susceptibility.md).
- **Curve numbers** (`runoff`): the SCS runoff equation and its inverse, the TR-55 curve numbers
  of NLCD land cover on each soil hydrologic group (`CN_TABLE`), composite with the paved share,
  and `fetch_curve_numbers` (`fetch-landcover --from curve-numbers`), which builds them for any
  box in the lower 48 states from NLCD and the USDA soil survey. Per-cell inputs may also be a
  folder of tables, read one after the other in name order.
- `sources.soils.fetch_soil_groups` (`fetch-landcover --from soil-groups`): soil hydrologic groups
  from the USDA gNATSGO soil survey, read by parts from Microsoft's Planetary Computer without an
  account, on NLCD's grid pixel for pixel.
- `sources.rainfall.Atlas14Grids`: NOAA Atlas 14 depths for many points from NOAA's published
  grids (all seven project areas of the conterminous US, every duration and return period),
  with `atlas14_grid_url` and `read_ascii_grid`. It matches the point service to within 1%.
- `susceptibility.cell_means`: the mean of any raster on each H3 cell, optionally only the cells
  inside a coarser parent.
- `store.iter_file`: any `.csv` or `.parquet` table file in pieces.
- [Example maps](docs/example-maps.md): interactive previews of the index and the drainage flags
  for three test areas (Houston, North Dakota prairie potholes, California Central Valley).
- **Drained or landlocked** (`drainage.drainage_flags`, `h3lowpoints drainage`): per depression,
  the distance from its lowest point to the nearest mapped stream (`stream_m`) and canal, ditch,
  conduit or pipeline (`canal_m`), the share of its area in H3 cells a channel crosses
  (`channel_share`), the distance from its spill point to the nearest road (`spill_road_m`), its
  land cover class (`land_cover`), and `drained`/`landlocked`: a stream or canal within 30 m of
  the lowest point, or a highway or ramp within 30 m of the spill point. The rule was chosen by
  measuring each sign against chance on three test areas before any validation; any road, and
  the channel share, were left out of it as uninformative in cities and in the prairie
  potholes. From any line files; in the US `--nhd` and `--tiger` (highways and ramps; `--all-roads`
  for every road) fetch them. The drainage columns join the susceptibility
  table, and either group can be computed or redone without the other; `verify` checks them.
- US readers: `sources.nhd` (NHD high-resolution flowlines from the map service or a state's
  GeoPackage, unpacked once beside its zip), `sources.roads` (Census TIGER/Line roads by county) and
  `sources.landcover.fetch_land_cover` (NLCD land cover classes; `fetch-landcover --from
  nlcd-landcover`).

### Fixed
- **Remote reads had almost no block cache.** rasterio hands `GDAL_CACHEMAX` to GDAL in bytes, so
  the 256 the readers set meant 256 bytes, not 256 MB, and GDAL fetched the same blocks again and
  again. Every remote read through the readers was affected (3DEP, WorldCover, the soil survey);
  results were never wrong, only slow: a soil read now takes 3 s instead of 12 to 15 s.
- An HTML error page from the NLCD service (its web server failing for a moment) was reported as
  "no data for this box". It is now asked again after 5, 15, 45 and 120 s, and if it keeps
  failing, raised as an `OSError`; only the service's real answers outside its coverage (a
  GeoTIFF of no data, an XML exception) mean no data.

## 0.3.0 (26 October 2026)

### Changed
- **The water mask also masks the open sea**: pixels where ESA WorldCover has no data and the
  elevation is at or below 0 m. USGS 3DEP stores the sea beyond WorldCover's coverage as flat
  ground at 0 m; it was analysed as land, which the national routing could not settle (302 million
  flat sea pixels across tile borders). Results made before analysed that strip of sea as land.
- Memory of national runs with catchments and details, rehearsed at full size: the final assembly
  joins tile pieces column by column (peak 7.2 GB, down from more than 17 GB), flats across tile
  borders are settled without neighbour-pair lists, and `verify` and `clip` read the depression
  table in pieces (8.8 GB and 6.3 GB, down from 11.3 GB and 13.3 GB).
- A disk-backed run's saved settings fingerprint its mask's content, so a mask rebuilt in place
  cannot continue a run started with another.
- Disk-backed runs compress their saved tiles and temporary per-cell files: on 1° test boxes with
  catchments and details, the disk needed at peak fell by half.
- `meta.json` of a `build` with `--water-mask` states the whole rule of the mask, open sea
  included.

### Added
- **National dataset, version 0.2** ([doi.org/10.5281/zenodo.23669497](https://doi.org/10.5281/zenodo.23669497)): the lower 48 states with catchments, spill
  links, levels, shape, fill curves and the depression × cell table; 10,570,791 depressions. Made
  in 12.2 hours on a laptop ([docs/large-regions.md](docs/large-regions.md#with-catchments-and-details)).
  Against version 0.1, 99.6% of the depressions are identical; the others lie where USGS updated
  nine 3DEP tiles in Washington on 21 October 2026, apart from last-digit differences in the sums
  of a few hundred.
- **Catchments and spill links** (`catchments=True` in `analyze`, `analyze_array` and
  `analyze_tiled`; `--catchments` for `analyze` and `build`): where rain on every pixel ends up,
  by exact rules that do not depend on processing order (steepest descent on the ground, flats
  drained to their nearest way off, shallow sinks passing their water on). Adds
  `catchment_area_m2`, `spill_to`, `spill_lat` and `spill_lon` to the depression table and a
  catchment table (`id`, `h3`, `area_m2`), saved as `catchments.csv` or `.parquet`. The whole
  raster version is checked against an independent pixel-by-pixel implementation; the tiled
  version gives exactly the whole-raster answer for any tile size, also on real terrain
  (Houston, 30 m). `verify` checks the new invariants and `clip` keeps the whole catchments of
  the depressions it keeps. See [docs/definition.md](docs/definition.md#catchments-and-spill).
- **Levels, shape and fill curves** (`details=True`, `--details`): `ground_m`, `water_level_m`,
  `length_m`, `width_m`, `orientation_deg`, and the flooded area and volume with the water 0.1,
  0.25, 0.5, 1, 2 and 5 m below full; plus a depression × cell table (`depression_cells`) with
  each depression's area, volume, depth and flooded areas per H3 cell. Tiled and whole results are
  identical; `verify` checks them and `clip` carries the table. See
  [docs/definition.md](docs/definition.md#levels-shape-and-fill-curve).
- `workers=` (`--workers`): label pixels with H3 cells in several processes; identical results,
  labelling about 3× faster with 4 processes.
- `hydro.depressions.fill_from_array`: the ground, the filled surface and the invalid pixels.

## 0.2.0 (22 October 2026)

### Added
- **Validation against observations: `validate` and `h3lowpoints validate`.** Do observations
  (flood reports, claims, high-water marks, anything with coordinates) fall in or near depressed
  ground more often than control points? For each distance of 0 to `max_rings` H3 rings, the
  lift (share of observations within k rings over the share of controls), split also by the
  depth of the nearest depressed ground, with percentile intervals from a spatial block
  bootstrap (whole H3 cells at `block_res` resampled with their observations and controls).
  Reads observations and controls from CSV, vector files in any CRS (polygons become a point on
  them), DataFrames or arrays; leaves out points outside the boundary studied and the ground
  around depressions flagged `deeper_than_wide`; warns when the controls are random points, the
  blocks are few or points were left out. Works on any saved result in pieces: 5,000
  observations and 50,000 controls against the national dataset took 20 s and 0.7 GB. Writes a
  JSON report and a per-point table. See [docs/validation.md](docs/validation.md).
- `store.iter_table(..., columns=...)` reads only the named columns.
- A worked example in [docs/validation.md](docs/validation.md#worked-example-new-york-city-street-flooding):
  New York City 311 street-flooding reports against every address in the city, on the national
  dataset (lift 1.54, 95% interval 1.31 to 1.79, in depressed cells; about 1 beyond two rings).

### Fixed
- Reading CSV results with pandas 3.1, which deprecates `float_precision` (the test suite
  treats deprecations as errors). pandas 3.1 reads numbers back exactly by default; older
  versions still get `float_precision="round_trip"`.
- An unreadable vector file (points or boundary, for example a download cut off midway) is now
  an input error (`error: could not read ...`) instead of a traceback.

## 0.1.1 (documentation)

- Install from PyPI (`pip install h3-lowpoints`) in the README and the docs; badges; links to the
  documentation site and to the published national dataset ([doi.org/10.5281/zenodo.23552773](https://doi.org/10.5281/zenodo.23552773)). No change
  to the code.

## 0.1.0 (21 October 2026)

The first public release, on PyPI. The API may still change before 1.0.

### Direction
- The library is now a general depression layer on H3 (see [docs/roadmap.md](docs/roadmap.md)).
  The Austin-only waterlogging classes (`drained`, `unexplained`, `suspect_pit`), their thresholds,
  the City of Austin asset reader and the 311 comparison were removed: they rested on one city
  and are not evidence for a global method. The research scripts remain in git history
  (commit `ca4af00`).

### Changed
- Depressions are 8-connected, matching the fill. Pits touching at a corner are one depression.
  On a 1 m lidar test DEM this merged 75 of 1,700 depressions; max depths were unchanged.
- All distances are in metres: the old `radius` (cells) became `drain_radius_m`.
- Code is organised as `hydro` and `h3agg` (core), `context` (drains, buildings, readers),
  `sources` (USGS 3DEP) and `viz`. `analyze` takes keyword arguments after the DEM path.
- Drain and building columns appear only when drains or buildings are supplied.
- The deepest point of a depression is the first tied cell in row-major order.

### Added
- **`deeper_than_wide` column.** Flags depressions deeper than the side of a square of their own
  area, which are almost always errors in the elevation data (43 of 10.6 million in the national
  3DEP 30 m run, up to 620 m deep); nothing is removed. `verify` checks the flag.
- **Release automation.** Publishing a GitHub release builds the package and uploads it to PyPI
  through trusted publishing (`.github/workflows/release.yml`); package metadata lists the
  documentation site, changelog and issues, with the SPDX licence expression Apache-2.0.
- **Clipping a result to a boundary, after the analysis.** `clip.clip_result` and
  `h3lowpoints clip --boundary FILE|us48` keep cells whose centre is inside and depressions whose
  lowest point's cell is kept, in pieces for results of any size; `meta["clip"]` records it and
  `verify` skips only the total comparisons clipping makes inexact. `sources.boundaries.us_lower48`
  reads the Census Bureau's lower-48 outline. The national water-masked layer clipped to the
  lower 48 keeps 10,573,090 depressions and 34,046,636 cells.
- `pipeline.rollup_saved` rolls up a saved cell table in pieces; `build_map(subtitle=...)`, and maps
  of a roll-up say so instead of calling it the analysis.
- **A water mask from ESA WorldCover.** `build_water_mask` puts WorldCover's permanent water on an
  elevation raster's own pixel grid (water when at least 5 of 9 sample points are), and
  `h3lowpoints build --water-mask` uses it, so lakes and reservoirs act as open water instead of
  filling as giant depressions (the national run found the Great Lakes as its five largest).
  Checked pixel for pixel against sampling by hand, and on Lake Erie and the Great Salt Lake.
- **One outlet node per tile.** Cells beside nodata, a mask or open water are outlets whose level
  is their own elevation; they are merged into one node of the border graph per tile, keeping
  each region's lowest crossing, and pass 2 finds the seeds again from the tile instead of
  keeping them. Identical results; with water masked the graph shrank 12 times on western Lake
  Erie and 20 times on Great Salt Lake (now no larger than without the mask), which keeps a
  national run with the mask within a laptop's memory. Checkpoints now record a format version,
  so older ones are refused instead of misread.
- **The water mask is built tile by tile and much faster.** `build_water_mask(..., "water.vrt")`
  writes one small file per tile holding water, continues where it stopped, and honours `hours`;
  rasters in longitude/latitude are sampled directly instead of through GDAL's warper.
  `build --water-mask` uses it and shares the `--hours` budget with the analysis.
- **Coverage no longer re-reads the elevation data.** Pass 1 records each tile's invalid pixels
  (compressed, saved with the checkpoints), and partly covered cells are measured from those
  records. With the water mask, where every shoreline cell is partly covered, the Lake Erie box
  went from 72 s to 10 s and the Great Salt Lake box from 75 s to 25 s, with identical results.
- Converting coordinates reuses one transformer per CRS (building one took 10 ms, which dominated
  coverage measured cell by cell).
- **Long runs in parts, safe against interruption.** A run written to disk saves every tile's
  result as soon as it is computed (`out_dir/_work`), so after a crash, shutdown or lost
  connection the same call continues where it stopped, redoing at most one tile, with exactly the
  result of an uninterrupted run. `hours=` / `build --hours H` stop cleanly after H hours (exit
  code 3) to split a run into parts. A folder refuses to continue with other settings. Remote reads
  retry ten times with growing delays (at least 17 minutes of lost connection).
- **Results written to disk as they are produced, in CSV or Parquet.** `analyze_tiled(out_dir=...)`
  keeps its per-cell sums in partition files on disk (one per coarse H3 parent) and writes the
  depression and cell tables piece by piece, so the size of the output no longer limits the size
  of the region; `h3lowpoints build` and `analyze --tile` work this way. `Result.save(format=
  "parquet")`, `--format parquet` and the `parquet` extra add Parquet output; `read_result` loads
  any saved result; `check_saved` and `h3lowpoints verify` check the cell table in pieces.
- **Complete documentation** in `docs/`, starting at `docs/index.md`: getting started, concepts
  from scratch, every output column and metadata field, the Python API, the command line, data
  sources, large regions, the map, verification, accuracy and limits, and a glossary.
  `tests/test_docs.py` keeps it in step with the code (links, options, functions, columns, fields).

### Fixed
- **A dropped connection while checking which tiles exist stopped a build.** The readers' own
  requests (tile checks, catalogues, small downloads, cached tile downloads) now retry passing
  failures (a reset or dropped connection, a timeout, HTTP 408, 429 and 5xx) after 1 to 128
  seconds, about 4 minutes in all; a 404 still means "missing" at once, and a lasting outage
  still raises. Found when the USGS server reset one of the 1,450 checks of a national build.
- **H3 ids that look like numbers.** Reading a saved CSV turned resolution-14 ids such as
  `8e4516503458447` (about one in ten thousand) into infinity, corrupting `h3lowpoints map` and
  `verify` on 1 m lidar results. H3 columns are now always read as text, and CSV numbers read back
  exactly (`float_precision="round_trip"`).
- **A CRS given as text.** `analyze_array(arr, transform, "EPSG:32614")` crashed although any CRS
  pyproj accepts is documented as valid.
- Empty tables keep their column types (integers, booleans, text) instead of becoming text.

### Added (earlier in this release)
- **A third of the memory for large regions.** `analyze_tiled` releases each tile once it is
  summarised and assembles the cell table with sorted arrays instead of pandas groupby; coverage
  is measured in chunks with a summed-area table; GDAL's read cache is capped at 256 MB. The 3DEP
  Texas box (1.82 Gpx) now peaks at 3.6 GB extra instead of 11.0 GB, with identical output.
- **Regions of any size and general verification.** `build_3dep_vrt` writes a virtual raster over
  3DEP tiles (each pixel from its own tile, optional resumable tile cache), and `h3lowpoints build`
  runs it through the exact tiled analysis. `verify.check_result` / `h3lowpoints verify` test
  invariants that hold for every correct result, and `spot_check` re-runs random windows of any
  DEM tiled and whole; `build` runs both and writes `verify.json`. Its first run on real data found
  coverage values of 1 + 2e-16 from float rounding, now clamped. See docs/verification.md.
- **US data readers.** `fetch_3dep` (USGS 3DEP bare earth, 30 m and 10 m), `fetch_lidar` (3DEP 1 m;
  newest survey wins, other UTM zones reprojected), `fetch_impervious` (NLCD percent impervious),
  `fetch_worldcover` (global 10 m land cover) and `design_rainfall` (NOAA Atlas 14), with the CLI
  commands `fetch-dem --from ...`, `fetch-landcover` and `rainfall`. They read only the parts of
  cloud-optimised files a box needs and use only the standard library and rasterio; the old
  untested `sources/dem.py` and the `requests` dependency are gone.
  Found while testing on real data: neighbouring 3DEP tiles' 6-pixel overlaps differ by
  centimetres, so each pixel is now taken from the tile whose own cell holds it; and NLCD tags 0%
  impervious as "no data", which would have erased every park and field.
- **Finest resolution by default.** `res="auto"` (now the default, also `--res auto`) picks the finest
  H3 resolution the DEM supports (14 for 1 m lidar, 10 for 30 m). Every depression carries
  `h3_lowpoint_15`, its deepest point as a resolution-15 cell. Cells gain `n_lowpoints` and
  `depressed_share`; `resolution_guide(dem)` explains each resolution for a DEM; the map opens
  resolutions 0 to 3 on the share and can colour cells by the number of depressions.
- **Exact tiled analysis.** `analyze_tiled` now equals `analyze` for any tile size (Barnes 2016: two
  passes joined by a graph over the tile-border cells), including depressions that span many
  tiles. The earlier buffer-based version could silently split one large depression into several
  pieces; the `buffer` option and the `level_uncertain` flag are gone because they are no longer
  needed. Measured on 38.9 Mpx of real Copernicus terrain: identical results, 30 s against 28 s,
  0.3 GB against 1.4 GB of memory. Checked on the Dutch coast, Death Valley, the Alps and Norway.
- **Geographic coordinates.** DEMs in latitude/longitude are analyzed natively with exact WGS 84
  pixel areas, and projected DEMs in feet work too. Drain distances are geodesic.
- **Copernicus GLO-30.** `h3lowpoints fetch-dem` and `sources.copernicus.fetch_dem` read the global
  30 m DEM from AWS open data.
- **Interactive map.** `Result.to_html`, `h3lowpoints map` and `analyze --map` write one
  self-contained HTML page: 3D H3 cells over a basemap, every resolution from the analysis one down,
  several measures, and each depression's lowest point. `examples/gallery.py` builds five real
  places. A headless-browser test fails on any console error.
- **Coverage.** Every cell has `coverage` and `covered_area_m2`, the share of the cell the DEM
  covers, exact against a brute-force oracle and identical between whole and tiled runs
  (about 2 to 3 s extra on 38.9 Mpx).
- **Mask.** `analyze(mask=...)` and `analyze_tiled(mask_path=...)` ignore cells such as open water.
- `Result.at_resolution` and `rollup`: exact roll-up to coarser H3 resolutions.
- `Result.to_deck` and `viz.deck.cells_to_geojson`: 3D map and GeoJSON export.
- Resolution tiers, warnings for DEMs too coarse for the requested analysis, `reproject_to_utm`
  and `--reproject`.
- Tests: fill against an independent method, tiled against whole (with mutation checks),
  geographic areas against an independent ellipsoid implementation, vectorized against loop.

### Fixed
- Tiled runs could drop depressions with a flat floor, because the tie-break for the deepest
  cell depended on the window.
- The `edge` flag did not fire for depressions cut off by the raster border.
- Reprojecting a DEM to UTM blurred sharp depression rims and inflated areas; geographic DEMs
  are now analyzed without resampling.

### Performance
- Region summaries run over depression pixels only (about 25% faster on a 13-megapixel DEM);
  the drain check no longer loops over drains.
