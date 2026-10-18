# The waterlogging add-on

The core library says where the terrain holds water in a closed depression. This optional layer
asks a narrower question: which depressions look like candidates for drainage review. It is a
screening heuristic, not a model of flooding.

## What it does

Each depression gets a class (`WaterloggingParams` holds every threshold; distances in metres):

| Class | Meaning |
|---|---|
| `drained` | A recorded drain lies within `drain_radius_m` (5 m) of the deep core (the part deeper than 80% of the maximum) |
| `suspect_pit` | At least 1.5 m deep but under 150 m²: more likely an artifact or excavation than a street sag |
| `unexplained` | Significant (at least 0.3 m deep and 150 m²) with no recorded drain nearby: the candidates |
| `minor` | Everything else |

Depressions flagged `edge` are never significant or suspect pits, because their size is unknown.
Without drain data, `unexplained` means only "significant"; the library warns when you run it that way.

The add-on is on by default for DEMs of 2 m or finer and off otherwise, because that is the only
resolution it was checked at.

## The evidence, and its limits

The thresholds were fixed before testing. They were checked against City of Austin 311 tickets in
three adjacent areas of about 12 km² each (Texas, USGS 1 m lidar from 2017). Significant
depressions were those with depth ≥ 0.3 m and area ≥ 150 m². Share of tickets within 10 m of one:

| Area | Flood / standing-water tickets | Within 10 m (95% interval) | Other 311 tickets within 10 m | Ratio |
|---|---|---|---|---|
| Pilot | 476 | 13.9% (10.9 to 17.0) | 3.8% | 3.7x |
| East | 381 | 9.7% (6.8 to 12.6) | 5.7% | 1.7x |
| North | 441 | 7.3% (4.8 to 9.8) | 4.3% | 1.7x |

A building-confound check repeated the test with only depressions under 10% inside a building
footprint and only tickets within 10 m of a building: flood tickets were 2.3x to 6.4x as likely
as other tickets to be near such a depression (about 3.7x pooled). The north area passed narrowly.

What this does **not** show:

- Most flood tickets are *not* near any detected depression, so recall is low.
- A recorded drain nearby did **not** make a depression less likely to have flood tickets, so the
  `drained` class should not be read as "solved".
- Three adjacent areas of one city, one lidar project and one asset schema is one data point,
  not evidence of transfer.
- 311 tickets reflect where people live and report. They are context, not ground truth.
- The lidar dates from 2017 and the drain records are current, so recent construction can differ.
- These figures were produced before version 0.1, when pits touching at a corner were counted
  as separate depressions. 8-connected labeling merges them (about 4% fewer depressions on the pilot
  area). The comparison has not yet been re-run on this version.

Footprints used for the building check are City of Austin data of mixed vintage (imagery 2012 to 2017).

## Using it elsewhere

Treat the classes as a way to rank depressions for a closer look, and validate against local
records before acting on them. Contributions of validation in other places, even negative
results, are welcome; see [CONTRIBUTING.md](../CONTRIBUTING.md).
