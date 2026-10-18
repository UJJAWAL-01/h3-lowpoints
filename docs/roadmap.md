# Roadmap

Direction: a **global depression layer on H3**, usable at every H3 resolution for visualization,
with a **susceptibility layer** for possible waterlogging built later from globally available
inputs. Nothing below is released yet; each phase is finished and tested before the next starts.

## Phase 1: the depression layer, globally

Done:

- **A global DEM source.** `h3lowpoints fetch-dem` reads Copernicus GLO-30 (30 m, open data, no
  login) from AWS. The US 3DEP 1 m reader stays as the high-resolution tier, and any national
  lidar DEM works through `analyze`.
- **Native geographic coordinates.** Latitude/longitude DEMs are analyzed as they are, with exact
  WGS 84 pixel areas per row; no resampling to UTM.
- **Exact tiled filling** (Barnes 2016): `analyze_tiled` equals `analyze` whatever the tile size,
  so a DEM larger than memory gives the same answer.
- **A mask.** `analyze(mask=...)` and `analyze_tiled(mask_path=...)` ignore cells such as open
  water, which then act as outlets. Copernicus does not publish a water mask with the AWS tiles,
  so supply one (for example from ESA WorldCover or JRC Global Surface Water).

- **Coverage per cell.** Every H3 cell reports how much of it the DEM covered, so a partly covered
  cell is never read as "less depressed" than a fully covered one.
- **An interactive map** (`Result.to_html`, `h3lowpoints map`, `examples/gallery.py`) to see all of
  this on a basemap at every resolution.

Still to do:

- **Continental scale.** Exact tiling holds one graph node per tile-border cell and per outlet
  cell. A continent needs the same method applied to blocks, joined hierarchically.
- Cost: about 1.7e11 land pixels at 30 m. The speed of one run is measured in the changelog;
  a global figure will be measured, not guessed.

Honest limit: GLO-30 is a surface model with vertical error of a few metres. It resolves large
basins, not street-level ponding. Street-scale depressions need lidar-derived DEMs, which exist
openly only for some countries.

## Phase 2: H3 resolution policy

H3 cell area: res 0 is 4.36 million km², res 3 is 12,393 km², res 5 is 253 km², res 7 is
5.2 km², res 9 is 0.105 km², res 11 is 0.0021 km².

| H3 res | Cell area | What is shown |
|---|---|---|
| 0 to 3 | 12,000 km² and up | Summary statistics only: depressed share of the covered land, total volume, deepest depression. Never "hotspots": a cell this large contains thousands of unrelated features. |
| 4 to 6 | 36 to 1,800 km² | Regional summaries and, later, the share of land that is susceptible |
| 7 to 9 | 0.1 to 5 km² | Neighbourhood-scale hotspots, where the DEM supports them |
| 10 and finer | under 0.02 km² | Only with DEMs of 2 m or finer |

The library already refuses to claim more than the DEM supports (H3 cells need at least four
pixels). This phase adds the upper end: coarse cells report shares and totals instead of flags.

## Phase 3: a physical susceptibility index (no labels needed)

For each depression, compare the water it could receive with the storage it has:

- its **contributing catchment**, from flow routing on the filled DEM;
- **runoff** = design rainfall x catchment x a runoff coefficient from land cover (ESA WorldCover);
- **storage** = the depression's volume;
- the **fill ratio** runoff / storage is the index.

Rainfall starts as a user-supplied number with a sensible default, with an optional global
raster later. Permanent water (JRC Global Surface Water) is excluded. The index is transparent:
every term can be inspected, and it works anywhere the inputs exist.

## Phase 4: machine learning, only if the data supports it

Global labels of street-level waterlogging do not exist. Candidate sources (satellite flood maps,
city flood reports, insurance claims) cover mostly river floods or a few cities, and each is
biased towards where people report. So before any model:

1. audit the label sources and what they actually measure;
2. train on the Phase 3 features, not raw rasters;
3. judge it with **leave-one-region-out** validation, so a model is never scored on the place it
   learned from.

If a model does not beat the physical index on unseen regions, the physical index stays the
answer. The previous Austin-only classes and their 311 comparison were removed for exactly this
reason: one city is not evidence for a global method.
