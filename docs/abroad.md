# Outside the US: four regions on Copernicus GLO-30

The same code that made the US national tables was run, unchanged, on four regions abroad with
the global Copernicus GLO-30 terrain: depressions, the road-crossing fix, 38 years of satellite
water, radar flood events and the confidence grade. This is a **demonstration that the method
runs anywhere**, not a test: no hypothesis was scored abroad. The rules were written down before
any data of these regions was read (phase 7 study rules, method note D).

## What was run

| Setting | Value |
|---|---|
| Terrain | Copernicus GLO-30 (`sources.copernicus.fetch_dem`), a **surface** model |
| Water mask | ESA WorldCover permanent water and open sea (`build_water_mask`) |
| Analysis | `analyze_tiled`, `min_depth` 0.5 m, H3 resolution 10, tiles of 4,096 pixels |
| Road-crossing fix | OpenStreetMap lines from the smallest Geofabrik extract holding the box (dated) |
| Water history | `WaterHook`, JRC Global Surface Water 1984-2021, read from the JRC server |
| Radar floods | `gfm.search`, 2015-2025, scenes with at least 25,000 flooded pixels; 100 items drawn at random (seed 0) where there were more; advisory flags count as not observed |
| Grade | `confidence()` with e = 2.0 m (`ERROR_GLO30`) |

Every region ran end to end on a laptop and passed `verify` with no problem.

## Results

| | Lagos, Nigeria | Netherlands | Ganges delta, Bangladesh | Salado basin, Argentina |
|---|---|---|---|---|
| Box (W, S, E, N) | 3.0, 6.3, 3.7, 6.8 | 4.2, 51.6, 5.8, 52.6 | 89.5, 23.0, 90.8, 24.0 | -60.5, -36.5, -59.0, -35.5 |
| Depressions | 33,099 | 112,837 | 125,573 | 52,453 |
| Their area | 534 km² | 2,853 km² | 4,636 km² | 2,026 km² |
| Largest | 22.2 km² | 14.5 km² | 48.8 km² | 8.3 km² |
| Seen wet (some years, seasonal, permanent) | 125 | 453 | 25,353 | 17,950 |
| Never seen wet | 14,675 | 91,261 | 57,077 | 30,734 |
| Too small / too few views | 10,623 / 7,676 | 21,095 / 28 | 43,143 / 0 | 3,572 / 197 |
| Radar items (candidates, used) | 277, 100 | 5,142, 97 | 5,842, 99 | 1,967, 99 |
| Observed / flooded in at least one scene | 3,551 / 28 | 47,368 / 397 | 54,805 / 5,011 | 49,445 / 13,305 |
| Grades A / B / C / D | 993 / 3,686 / 28,094 / 326 | 5,996 / 15,984 / 86,968 / 3,889 | 10,059 / 39,492 / 75,060 / 962 | 752 / 22,988 / 28,657 / 56 |
| Road crossings (found, cut) | 890, 688 | 20,709, 13,429 | 4,020, 2,776 | 318, 246 |
| Road dams | 59 | 712 | 261 | 20 |
| Run time (analysis) | 11 min | 21 min | 22 min | 28 min |

Radar items not used: those without an advisory file, or whose catalogue entry gives a
wildcard pattern instead of one (3 in the Netherlands, 1 each in the Ganges and Salado boxes).

## Reading the results

- **The record follows the climate.** In the Salado basin, a flat plain of the Pampas known
  for floods, a third of the depressions were seen wet by Landsat and a quarter of those the
  radar observed were flooded at least once. In the Ganges delta a fifth were seen wet. In
  Lagos, nearly a quarter of the depressions have too few clear years: the tropical coast is under
  cloud much of the year, which optical satellites cannot see through.
- **The Netherlands' polders do not come out as one basin.** Their canals and lakes are masked
  as open water, which acts as a way out, so the polders split into many depressions (the
  largest is 14.5 km²). Water there is managed by pumps the terrain does not show.

## What a surface model changes

GLO-30 is a **surface** model: it holds roofs and tree tops, not the ground under them. Gaps
between buildings and between tree crowns become "depressions" metres deep that are not in the
ground. Measured at the lowest point of each depression against ESA WorldCover (10 m):

| | Lagos | Netherlands | Ganges delta | Salado basin |
|---|---|---|---|---|
| Depressions with the lowest point on built-up land | 43.5% | 31.2% | 9.7% | 1.0% |
| on trees | 41.0% | 37.3% | 66.2% | 5.6% |
| **Grade A depressions on built-up land or trees** | **90.0%** | **81.1%** | **63.8%** | **34.0%** |

So on GLO-30, **grade A does not mean "real"** in cities and forests: a deep depression there is
often a gap in the roofs or the canopy. The grade looks only at depth, flags and observed water,
not at what covers the ground. Where it matters, use a bare-earth model (lidar, or a corrected
global model such as FABDEM where its licence suits), or leave out built-up and tree cover. The
open farmland of the Salado basin is the case where GLO-30 comes closest to the ground.

Two more consequences of the terrain:

- With e = 2.0 m most depressions are grade C (55% to 85% by region): shallower than the model's
  own error. That is the rule working as intended, not a fault of the regions.
- The road-crossing fix depends on OpenStreetMap: culverts and small streams are mapped far more
  completely in the Netherlands (20,709 crossings) than in the Salado box (318).

## Limits

- Four boxes are a demonstration of the method, not a sample of the world; nothing here was
  scored against observations.
- 100 radar items per region, drawn at random from the catalogue, are a sample: a depression
  never flooded in them may have flooded in another scene. Radar floods are mostly river floods.
- The JRC record ends in 2021; GLO-30 comes from radar data of about 2010-2015, so later building is missing.
- See also [Seen wet and seen flooded](water-history.md), [Confidence grade](confidence.md) and
  [Accuracy and limits](limitations.md).
