# Data sources

The library analyzes any elevation raster you give it. For convenience it can also download
open data for any box. This page describes each dataset: what it measures, how good it is,
where it exists, its licence, how the library reads it, and the surprises found while testing it.

No data is bundled with the library. Everything is read from the publisher's own servers, over
HTTPS, with no account or key, and only the parts a box needs are downloaded (the files are
cloud-optimized GeoTIFFs, which allow reading windows by HTTP range requests).

## Choosing elevation data

| Dataset | Pixel | Surface | Vertical accuracy (typical) | Area | Use for |
|---|---|---|---|---|---|
| USGS 3DEP 1 m lidar | 1 m | bare earth | ~10 cm | most of the US | street-scale depressions: sags, low corners, pits |
| USGS 3DEP 1/3″ | ~10 m | bare earth | about 1 m; better where built from lidar | the US | neighbourhood basins |
| USGS 3DEP 1″ | ~30 m | bare earth | about 1 m or more; varies with the source survey | the US | large basins, regional and national overviews |
| Copernicus GLO-30 | ~30 m | **surface** (roofs, trees) | ~2 to 4 m | the world | large basins outside the US |

Rules of thumb:

- Prefer **bare earth**. A surface model holds water on roofs and in gaps between trees, which
  produces depressions that are not in the ground.
- Set **`min_depth` above the vertical error**: about 0.15 m on lidar, 0.5 to 1 m on 10 to 30 m
  data. The library warns when `min_depth` is under 1 m on data coarser than 2 m.
- The finest H3 resolution follows from the pixel size (`res="auto"`): 14 for 1 m, 11 for 10 m,
  10 for 30 m.

Accuracy figures are the publishers' typical values, not guarantees for any one place; check the
metadata of the survey covering your area.

## USGS 3DEP 30 m and 10 m

- **What:** the US 3D Elevation Program's seamless bare-earth elevation at 1 arc-second (~30 m)
  and 1/3 arc-second (~10 m), built from the best source available for each area (increasingly
  lidar).
- **Where:** the United States. Missing tiles (open sea, outside the US) are nodata.
- **Licence:** public domain (US Government work). Credit "USGS 3D Elevation Program" is good
  practice.
- **Format:** 1° × 1° GeoTIFF tiles in NAD83 longitude/latitude, heights in metres, on the public
  `prd-tnm` bucket.
- **Library:** `fetch_3dep` (a GeoTIFF for a box), `build_3dep_vrt` (a virtual raster for a box
  of any size), `h3lowpoints fetch-dem --from 3dep|3dep10`, `h3lowpoints build`.

Things learnt while testing:

- **Tiles are named by their north-west corner**: `n31w098` covers 30° to 31° N and 98° to 97° W.
- **Neighbouring tiles overlap by 6 pixels, and the overlaps disagree.** The tiles are produced
  separately and the shared strips can differ by centimetres, enough to change the outline of a
  shallow depression along a seam. The library therefore takes every pixel from the tile whose
  own 1° cell contains it (never from a neighbour's overlap), copying values exactly with no
  resampling. `fetch_3dep` and `build_3dep_vrt` give identical pixels.
- **Size:** a 1° tile at 30 m is about 40 MB; Texas needs 155 tiles (6 GB). `build` can stream
  tiles over HTTP or download them once into `--cache-dir`, which is faster because a tiled
  analysis reads everything twice.

## USGS 3DEP 1 m lidar

- **What:** bare-earth elevation at 1 m from airborne lidar surveys, published per survey
  project.
- **Where:** most, not all, of the United States; coverage keeps growing. Where several surveys
  overlap, they differ in date.
- **Licence:** public domain.
- **Format:** 10 km × 10 km tiles in the UTM zone of each survey, heights in metres, listed in
  the USGS National Map catalogue (TNM API).
- **Library:** `fetch_lidar`, `h3lowpoints fetch-dem --from lidar` (boxes up to 100 km² by
  default).

How the library assembles a box:

- It asks the catalogue for every 1 m tile touching the box and sorts them **newest survey
  first**; where surveys overlap, the newest wins.
- The output is on the pixel grid of the survey covering the box's centre, so its values are
  copied, not resampled. Tiles from a survey in another UTM zone are reprojected (bilinear) onto
  that grid.
- Downtown Austin at 1 m gives H3 resolution 14 (`auto`), and lowest points to the metre.

## Copernicus GLO-30

- **What:** a global digital **surface** model at 1 arc-second (~30 m) from the TanDEM-X radar
  mission, distributed by the European Space Agency.
- **Where:** the whole land surface. Tiles that would be entirely ocean do not exist.
- **Licence:** free to use with attribution, as given by the Copernicus programme ("produced using
  Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018
  provided under COPERNICUS by the European Union and ESA; all rights reserved"). Check the
  current terms before redistributing derived data.
- **Format:** 1° × 1° cloud-optimized GeoTIFFs in WGS 84, heights in metres above the EGM2008
  geoid, on AWS open data.
- **Library:** `fetch_dem`, `h3lowpoints fetch-dem` (the default source).

Things to know:

- It is a **surface model**: buildings and forest canopy are included. It shows large basins
  (the below-sea-level polders around Rotterdam come out as one large depression), not
  street-level ponding.
- **Ocean inside a tile is valid elevation 0 m**, not nodata, so it does not lower coverage. Supply
  a mask if open water should not count.
- **Lakes and rivers are flattened but not marked.** A flattened lake surface has no depth in the
  fill. Use a water mask (for example permanent water from WorldCover) if lakes should be
  excluded.
- **Tiles narrow towards the poles.** Above 50° latitude tiles have fewer columns. A box spanning
  bands of different widths is mosaicked at the finest width, with a warning.

## NLCD percent impervious

- **What:** the US National Land Cover Database's share of each 30 m pixel covered by pavement,
  roofs and other impervious surfaces, 0 to 100%.
- **Where:** the lower 48 states. Years 2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021.
- **Licence:** public domain (MRLC consortium, US Government).
- **Format:** served by MRLC's web coverage service in NAD83 / Conus Albers (EPSG:5070).
- **Library:** `fetch_impervious`, `h3lowpoints fetch-landcover --from nlcd`. Output values are
  0 to 100, with 255 for no data.

A trap found while testing: **the service labels 0 as "no data"**, but in this product 0 means
"0% impervious" (a park, a field, a forest). Read naively, every unbuilt pixel would vanish. The
library drops that label and treats only values above 100 as no data.

It is the paved share of the curve numbers of the susceptibility index
([Susceptibility](susceptibility.md#runoff-curve-numbers)).

## NLCD land cover

- **What:** the National Land Cover Database's class of each 30 m pixel: open water (11),
  developed land of four intensities (21 to 24), barren land (31), forests (41 to 43), shrubland
  (51, 52), grassland (71 to 74), pasture and hay (81), cultivated crops (82), wetlands (90, 95);
  in `NLCD_CLASSES`.
- **Where, licence, format:** as NLCD impervious above. Years 2001 to 2021.
- **Library:** `fetch_land_cover`, `h3lowpoints fetch-landcover --from nlcd-landcover`. Output is
  uint8 class codes, 0 for no data (outside the lower 48 states, open sea), as the service sends.

The drainage flags read it at each lowest point (`land_cover`), so residential and farm
depressions can be studied apart.

## USDA soil survey (gNATSGO): hydrologic groups

- **What:** the soil map unit of every 10 m pixel of the lower 48 states (the gridded National
  Soil Survey Geographic Database of USDA NRCS), and each unit's hydrologic group (`hydgrpdcd`,
  dominant condition): A (sand, gravel; water soaks in fast), B, C, D (clay, shallow soil, a high
  water table; it sheds water), or a dual group A/D, B/D, C/D for wet soils (the first letter if
  artificially drained). Some units have no group: urban land, water, pits, rock outcrop.
- **Where:** the lower 48 states.
- **Licence:** CC0 as published on Microsoft's Planetary Computer; USDA data are public domain.
- **Format:** the July 2020 edition, as cloud-optimized GeoTIFF tiles of 16,384 × 16,384 pixels
  (163.84 km) with overviews at 20 to 320 m, in the USGS version of Conus Albers, and the map-unit
  table as Parquet (317,894 units, 12.5 MB). A read token is issued anonymously by the Planetary
  Computer's public API, so no account is needed.
- **Library:** `sources.soils.fetch_soil_groups`, `h3lowpoints fetch-landcover --from soil-groups`;
  `runoff.fetch_curve_numbers` (`--from curve-numbers`) combines them with NLCD.

The groups are read from the soil grid's 20 m overview onto 30 m pixels (each pixel takes the map
unit at its centre, which the overview can shift by one 10 m pixel, far less than the precision
of a soil map). Checked in October 2026 on three test boxes: Houston's soils are 84% group D
(clays), North Dakota's mostly C, the Central Valley's a mix of A to D. The table lists one unit
twice ("Water"), which is harmless and dropped.

## USGS National Hydrography Dataset (NHD)

- **What:** the mapped surface water of the US at high resolution (1:24,000 or finer): streams
  and rivers, canals and ditches, underground conduits, pipelines, with a feature type (`ftype`)
  on every line: 460 stream or river, 558 artificial path (through a lake or a wide river), 336
  canal or ditch, 420 underground conduit, 428 pipeline, 334 connector, 566 coastline.
- **Where:** the US. NHD high resolution was last updated in 2023, when USGS moved to its
  successor, the 3D Hydrography Program; it is still the national map of small ditches.
- **Licence:** public domain.
- **Format:** a map service (2,000 lines per request) for boxes, and one GeoPackage zip per state
  (3 MB to 3.5 GB; about 45 GB for the lower 48 states and DC), in NAD83. A state's file covers
  whole river basins that cross its border, so neighbouring files overlap.
- **Library:** `sources.nhd.fetch_flowlines` (a box, from the service), `state_flowlines` (a state,
  by box and feature type), `h3lowpoints drainage --nhd`.

A state's GeoPackage is unpacked once beside its zip (1.7 to 2.6 times the zip's size: Texas
6.4 GB) and read through its spatial index: Texas unpacked and read for a 1° box in about a
minute. Reading it straight from the zip is not workable, because a compressed zip cannot be
read by parts: every lookup decompresses the file from its start again (Texas ran for more than
20 minutes without finishing). `remove_unpacked` deletes the unpacked file when done.

Checked in October 2026: for a box in Washington, DC the service and the state file gave the
same 430 lines (165.0 km). The service can be slow (single requests took 1 to 4 minutes, some
timed out); for large areas use the state files. In that DC box most lines are underground
pipelines (387 of 430, described as "Pipeline: General Case; Underground"), which the drainage
flags count with canals.

## US Census Bureau TIGER/Line roads

- **What:** every public road, one file per county (3,233 counties and equivalents in 2024),
  with its class (`MTFCC`: S1100 primary road, S1200 secondary road, S1400 local street, S1630
  ramp, S1740 private road, ...).
- **Licence:** public domain.
- **Format:** zipped shapefiles of 1 to 12 MB per county, in NAD83.
- **Library:** `sources.roads.fetch_roads` (the counties a box touches, cut to the box),
  `county_roads`, `counties`, `h3lowpoints drainage --tiger`.

## ESA WorldCover

- **What:** global land-cover classes at 10 m for 2021: tree cover, shrubland, grassland,
  cropland, built-up, bare or sparse vegetation, snow and ice, permanent water bodies, herbaceous
  wetland, mangroves, moss and lichen (codes 10 to 100, in `WORLDCOVER_CLASSES`).
- **Where:** the world.
- **Licence:** CC BY 4.0. Credit "© ESA WorldCover project 2021 / Contains modified Copernicus
  Sentinel data (2021) processed by ESA WorldCover consortium".
- **Format:** 3° × 3° tiles in WGS 84 on AWS.
- **Library:** `fetch_worldcover`, `h3lowpoints fetch-landcover` (the default). Output is uint8
  class codes, 0 for no data.

Its "permanent water bodies" class is a convenient water mask for `analyze(mask=...)`.

### Water mask from ESA WorldCover

`build_water_mask` (and `h3lowpoints build --water-mask`) turns WorldCover into a mask on the
elevation raster's own pixel grid: each elevation pixel is sampled at 3 × 3 evenly spaced points,
and it is water when at least 5 of the 9 fall on "permanent water bodies". A test compares this
pixel for pixel with the same sampling done by hand. For grids in longitude/latitude (all of
3DEP) each point is looked up directly in the WorldCover pixel containing it, treating the grid's
datum as WGS 84; other CRSs are sampled through a warped view of WorldCover. On 52 million pixels
of Wisconsin the two ways agree on all but 0.02% of pixels, which lie on the edges of water where
the metre between NAD83 and WGS 84 moves a sample point into the next WorldCover pixel.

Why it matters: lakes are flat in elevation data, and a lake whose outlet (a dam, locks, a narrow
river) stands higher than its surface fills as one huge depression. Measured on two boxes of 3DEP
30 m data:

| Box | Without the mask | With the mask |
|---|---|---|
| Western Lake Erie (−83.5, 41.4, −82.5, 42.1) | largest depression 465 km² (the lake); 662 km² depressed in all | largest 2.5 km²; 101 km² in all |
| Great Salt Lake (−113.1, 40.6, −111.8, 41.7) | largest 3,835 km² (the lake); 4,366 km² in all | largest 172 km², in the desert west of the lake; 312 km² in all |

Ninety-eight per cent of the depressions on land were unchanged; what disappeared were the lakes
and the low shore land merged into them.

**The open sea.** WorldCover maps land and the water near it; beyond that it has no data. USGS
3DEP, however, continues offshore and stores the sea there as flat ground at 0 m (sampled every
0.1° east of Atlantic City: WorldCover's water reaches about 25 km out, then 3DEP goes on at 0 m
for roughly another 8 km before it stops). Without a rule for it, that sea is analysed as land: harmless for depressions
(it has no depth) but a flat of hundreds of millions of pixels for water routing. So a pixel is
also water when at least 5 of its 9 points fall where WorldCover has no data **and** its own
elevation is at or below 0 m: open sea. The height condition keeps any gap in WorldCover over dry
land from being masked. Elevations are read only where WorldCover has no data, so inland the mask
costs no extra reading. Found by the national run of 22 to 23 October 2026, whose routing failed
on 302 million flat sea pixels; results published before that (version 0.2 and the v1 national
dataset) analysed this strip of sea as land at 0 m.

Limits: only *permanent* water (2021) is masked, not seasonal flooding; water narrower than about
half a pixel (15 m at 30 m) is not seen; WorldCover's own classification errors carry over.
Elevation grids on NAD83 and WorldCover on WGS 84 differ by about a metre, far below a pixel.

## NOAA Atlas 14 design rainfall

- **What:** how much rain falls in a storm of a given duration (5 minutes to 60 days) and rarity
  (expected once in 1 to 1000 years), from NOAA's Precipitation Frequency Data Server.
- **Where:** the parts of the US covered by Atlas 14. In tests in 2026 every place tried had
  estimates **except Washington and Oregon**. Wherever the server has no estimates, the library
  raises an error asking you to supply a depth yourself.
- **Licence:** public domain.
- **Library:** `design_rainfall` (the whole table, in millimetres), `rainfall_depth` (one value),
  `h3lowpoints rainfall LAT LON`.

Durations are labelled as NOAA labels them: `5-min`, `10-min`, `15-min`, `30-min`, `60-min`,
`2-hr`, `3-hr`, `6-hr`, `12-hr`, `24-hr`, `2-day`, `3-day`, `4-day`, `7-day`, `10-day`, `20-day`,
`30-day`, `45-day`, `60-day`. Values are partial-duration-series estimates at a point; over a city
they change little, so one point near the centre is a fair design value.

**Grids, for many points.** For a whole region (every H3 cell of a result), `Atlas14Grids` reads
NOAA's published grids instead (`https://hdsc.nws.noaa.gov/pub/hdsc/data/`): ESRI ASCII grids 30
arc-seconds (about 800 m) apart, in thousandths of an inch (converted to millimetres), one zip of
about 1 to 4 MB per project area and return period, downloaded only for the project areas a
region lies in. Seven grid sets cover the conterminous US (checked in October 2026):

| Folder | Project area |
|---|---|
| `sw` | Southwest: volumes 1 (semiarid Southwest) and 6 (California) |
| `orb` | Volume 2, Ohio River Basin and surrounding states |
| `mw` | Volume 8, Midwestern states |
| `se` | Volume 9, Southeastern states |
| `ne` | Volume 10, Northeastern states |
| `tx` | Volume 11, Texas |
| `inw` | Volume 12, Interior Northwest (Idaho, Montana, Wyoming) |

Sampled on a regular lattice over the lower 48 states, they cover every state except Washington
and Oregon (0.2% of their points), and the coastal states up to their shores (97% to 99.9% of
points within the Census state outlines, which include bays and sounds). The files do not share
one grid, and their no-data value differs (-9 or -999), so each is read with its own header.
Where two project areas meet, both have values in a thin strip (0.4% of covered points); there
they differ by about 5% typically and up to 41% (100-year 24-hour grids), as NOAA notes
discrepancies at project boundaries are inevitable, and the mean is used. The 24-hour grid
matches the point service to within 1% (a network test, at Houston).

## US Census Bureau boundaries

- **What:** the cartographic boundary files of the US states, generalized for mapping at
  1:500,000.
- **Licence:** public domain.
- **Library:** `sources.boundaries.us_lower48` and `h3lowpoints clip --boundary us48`: the lower
  48 states and DC as one outline, for clipping results to the US after the analysis.
- **Accuracy:** along coasts and the national borders the generalized line can be a few hundred
  metres from the legal one; it decides membership only for cells that close to it.

## Your own data

Any single-band elevation raster with a coordinate reference system works: national lidar from
other countries, FABDEM, NASADEM, a drone survey. Requirements and tips:

- a projected CRS in metres or feet, or longitude/latitude in degrees;
- a north-up grid (no rotation);
- missing pixels marked by the file's nodata value or NaN;
- **heights in metres**: the library does not convert vertical units (see
  [Accuracy and limits](limitations.md));
- a mask (`mask=` or `mask_path=`) for water or areas to ignore.

Drains (inlets, grates) and building footprints can come from any vector file; see `read_drains`
and `read_buildings` in the [Python API](python-api.md). City open-data portals often publish both.
