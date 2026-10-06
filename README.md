# h3-lowpoints

**Experimental research prototype.** Finds closed depressions ("low points") in a lidar elevation
model and describes them on H3 hexagon cells, with optional storm-drain context. Developed and
tested on central Austin, Texas.

> Research output, **not a flood-risk map**. It says nothing about any specific property.

## Sample output (pilot area, about 12 km2, H3 resolution 11)

Interactive versions: [terrain](https://ujjawal-01.github.io/h3-lowpoints/demo/terrain.html) |
[slope](https://ujjawal-01.github.io/h3-lowpoints/demo/slope.html) |
[depressions](https://ujjawal-01.github.io/h3-lowpoints/demo/pilot_depressions.html)

### 1. Terrain
![Terrain](docs/img/terrain.png)

Mean ground elevation per cell, extruded 3x. Used as a sanity check that the lidar, projection and
H3 conversion line up with real geography (the low-lying valley corridor is visible).

### 2. Slope
![Slope](docs/img/slope.png)

Mean slope per cell (scale 0 to 15 percent; darker red is steeper). Pale cells are flat ground,
where water drains poorly.

### 3. Depressions
![Depressions](docs/img/depressions.png)

Closed depressions of at least 0.15 m depth, in cells with at least 20 m2 of depression; darker and
taller means deeper. These include ponds, excavations and street sags, not only drainage problems.

## What it does

1. Fills a DEM with a priority-flood algorithm; fill depth minus original elevation is depression depth.
2. Labels depressions of at least 0.15 m and reports area, volume and the lowest point.
3. Marks a depression as `drained` if a recorded inlet or grate lies within 5 m of its deepest part.
4. Aggregates results to H3 cells (default resolution 11).

```python
from h3lowpoints import analyze

res = analyze("dem.tif")              # DEM in a projected CRS with metres
res.depressions.head()                # one row per depression
res.cells.head()                      # one row per H3 cell
res.save("outputs/my_area")
```

Pass `drains_xy=` (an array of x, y in the DEM's CRS) to add drain context. Pass `buildings=` (footprints in the same CRS) to add `building_overlap` and `likely_building_artifact`.

## Command line

```
h3lowpoints analyze dem.tif --drains drains.csv --buildings buildings.gpkg --out results/
```

Drains: a point file (GeoPackage, GeoJSON, Shapefile) or a CSV with `x,y` (in the DEM's CRS) or
`lon,lat` columns. Buildings: a polygon file. The DEM must be in a projected CRS with metres.
Outputs: `depressions.csv`, `cells.csv` and `meta.json` (parameters and versions).

## What we measured (and what we didn't)

Validated against Austin 311 tickets in three adjacent areas of about 12 km2 each, with thresholds
fixed in advance (significant = depth >= 0.3 m and area >= 150 m2). Share of tickets within 10 m of
a significant depression:

| Area | Flood / standing-water tickets | Flood tickets within 10 m (95% interval) | Other 311 tickets within 10 m | Ratio |
|---|---|---|---|---|
| Pilot | 476 | 13.9% (10.9 to 17.0) | 3.8% | 3.7x |
| East | 381 | 9.7% (6.8 to 12.6) | 5.7% | 1.7x |
| North | 441 | 7.3% (4.8 to 9.8) | 4.3% | 1.7x |

Robustness check (building confound): flood tickets and lidar artifacts could both concentrate
at buildings, so we repeated the test using only depressions with under 10% of their area inside
a building footprint and only tickets within 10 m of a building. Flood tickets were 2.3x to 6.4x
as likely as other 311 tickets to be within 10 m of such a depression (about 3.7x pooled). The
criterion was fixed before the run; the north area passed narrowly (interval lower bound about
equal to the control rate). Footprints are City of Austin data of mixed vintage (imagery 2012 to 2017).

Limits:

- Depressions mostly under building footprints are likely lidar artifacts (about 7 to 17% of significant depressions in the three areas). Pass `buildings=` to flag them.
- Most flood tickets are *not* near any detected depression, so recall is low.
- Having a recorded drain nearby did **not** make a depression less likely to have flood tickets.
- All three areas are in one city, one lidar project and one asset schema. Nothing here shows it works elsewhere.
- 311 tickets reflect where people live and report. They are context, not ground truth.
- The lidar was flown in 2017 and the City asset records are current, so recent construction can differ.

## Data and licensing

USGS 3DEP elevation data is public domain. Check each City of Austin dataset's own license before
redistributing derived data. Source data is not included in this repository.

## Status

Not on PyPI. Install from source: `pip install -e .`
