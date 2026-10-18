# Changelog

## Unreleased (towards 0.1)

Not yet published. Breaking changes from the earlier prototype are expected while the API settles.

### Changed
- Depressions are now 8-connected, matching the fill. Pits touching at a corner are one
  depression. On the Austin pilot area this merges 75 of 1,700 depressions; max depths are unchanged.
- Waterlogging is an optional layer (`waterlogging=`), on by default only for DEMs of 2 m or finer.
  Its columns (`significant`, `suspect_pit`, `cls`) are absent when it is off. Drain and building
  columns appear only when drains or buildings are supplied.
- All distances are in metres: `radius` (cells) became `WaterloggingParams.drain_radius_m`.
- Code moved into `hydro` (core), `h3agg`, `context` (drains, buildings, readers), `waterlogging`,
  `sources` (USGS, Austin) and `viz`. `analyze` takes keyword arguments after the DEM path.
- The deepest point of a depression is now the first tied cell in row-major order.

### Added
- Tiled runs flag depressions whose water level depends on a tile border (`edge`). Previously
  a tile could silently split one large depression into several unflagged pieces.
- `Result.at_resolution`, `rollup`: exact roll-up to coarser H3 resolutions.
- `Result.to_deck`, `viz.deck.cells_to_geojson`: 3D map and GeoJSON export.
- Resolution tiers, warnings for DEMs too coarse for the requested analysis, `reproject_to_utm`
  and `--reproject`, `--waterlogging` / `--no-waterlogging`.
- Tests: fill against an independent method, tiled against whole, vectorized against loop.

### Fixed
- Tiled runs could drop depressions with a flat floor, because the tie-break for the deepest
  cell depended on the window.
- The `edge` flag did not fire for depressions cut off by the raster border.

### Performance
- Region summaries run over depression pixels only (about 25% faster on a 13-megapixel DEM);
  the drain check no longer loops over drains.
