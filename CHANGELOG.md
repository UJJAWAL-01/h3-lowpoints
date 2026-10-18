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
