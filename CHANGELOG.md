# Changelog

## Unreleased (towards 0.1)

Not yet published. Breaking changes from the earlier prototype are expected while the API settles.

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
