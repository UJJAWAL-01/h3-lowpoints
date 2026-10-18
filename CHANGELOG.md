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
- Tiled runs flag depressions whose water level depends on a tile border (`level_uncertain`).
  Previously a tile could silently split one large depression into several unflagged pieces.
- `Result.at_resolution` and `rollup`: exact roll-up to coarser H3 resolutions.
- `Result.to_deck` and `viz.deck.cells_to_geojson`: 3D map and GeoJSON export.
- Resolution tiers, warnings for DEMs too coarse for the requested analysis, `reproject_to_utm`
  and `--reproject`.
- Tests: fill against an independent method, tiled against whole, vectorized against loop.

### Fixed
- Tiled runs could drop depressions with a flat floor, because the tie-break for the deepest
  cell depended on the window.
- The `edge` flag did not fire for depressions cut off by the raster border.

### Performance
- Region summaries run over depression pixels only (about 25% faster on a 13-megapixel DEM);
  the drain check no longer loops over drains.
