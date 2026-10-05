# h3-lowpoints

**Experimental.** Finds closed depressions ("low points") in a lidar elevation model and describes
them on H3 cells, with optional storm-drain context. Developed and tested on central Austin, Texas.

<!-- After taking a screenshot of docs/demo/pilot_depressions.html, save it as docs/img/pilot.png
     and uncomment the next line:
![Depressions in a 12 km2 pilot area, H3 res 11](docs/img/pilot.png)
-->

Interactive demo (GitHub Pages): https://YOUR_USERNAME.github.io/h3-lowpoints/demo/pilot_depressions.html

> This is research output, **not a flood-risk map**. It says nothing about any specific property.

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

Pass `drains_xy=` (an array of x, y in the DEM's CRS) to add drain context.

## What we measured (and what we didn't)

Validated against Austin 311 tickets in three adjacent 12 km2 areas, using the thresholds fixed in
advance (significant = depth >= 0.3 m and area >= 150 m2). Flooding and standing-water tickets fall
within 10 m of a significant depression about **1.7 to 3.7 times as often** as other 311 tickets.

Limits:

- Most flood tickets are *not* near any detected depression, so recall is low.
- Having a recorded drain nearby did **not** make a depression less likely to have flood tickets.
- All three areas are in one city, one lidar project and one asset schema. Nothing here shows it works elsewhere.
- 311 tickets reflect where people live and report. They are context, not ground truth.

## Data and licensing

USGS 3DEP elevation data is public domain. Check each City of Austin dataset's own license before
redistributing derived data. Source data is not included in this repository.

## Status

Not on PyPI. Install from source: `pip install -e .`
