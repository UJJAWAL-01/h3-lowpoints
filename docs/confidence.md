# Confidence grade and the road-crossing fix

Some depressions in an elevation model are not real places where water collects: a road
embankment shown without the culvert under it, a bridge deck, a building, or a dip smaller than
the model's own error. `h3lowpoints.confidence` gives each depression a grade from A to D by a
fixed rule, with one column per reason, so a user can filter, map or check them.

> **What the grade is not.** In the national test (Study 3, [Evidence](evidence.md#study-3-observed-water-and-the-confidence-grade-lower-48))
> the grade did **not** separate confirmed from unconfirmed depressions clearly enough to call it
> validated: grades A, B and C were confirmed by finer terrain in falling order, but grade D was
> not the lowest. Use it as a transparent summary of the checks below, not as a probability.

## Quick start

```python
from h3lowpoints import analyze
from h3lowpoints.confidence import confidence, ERROR_3DEP_30M
from h3lowpoints.sources import osm
from h3lowpoints.water import water_history

box = (-99.5, 47.7, -98.5, 48.3)
lines = osm.fetch_lines(box, cache_dir="osm")                 # roads, railways, streams
res = analyze("dem_30m.tif", min_depth=0.5, breach_crossings=lines)
grades = confidence(res, error_m=ERROR_3DEP_30M, water=water_history(res))
grades.grade.value_counts()
```

## The rule

The rule was written down before any data was looked at (phase 7 study rules, section 5):

1. **Base grade** from the depth (`max_depth_m`) against the vertical error `e` of the
   elevation data: **A** if depth ≥ 2e, **B** if e ≤ depth < 2e, **C** if depth < e.
2. **Any flag sets D.** The flags:
   - `road_dam`: the depression goes, or becomes shallower than the minimum depth, once road and
     railway embankments over streams are cut (below);
   - `artifact`: a likely building artefact (`likely_building_artifact`, when building footprints
     were given), or a bridge line passing over the depression (`bridge_crossing`).
3. **Support raises the grade one level** (D to C, C to B, B to A): water seen in at least one
   year (water history class `permanent`, `seasonal` or `some_years`), or flooded in at least one
   radar scene ([Seen wet and seen flooded](water-history.md)).

**Vertical error `e`:**

| Data | e | Basis |
|---|---|---|
| USGS 3DEP 30 m | 1.0 m (`ERROR_3DEP_30M`) | An assumption: USGS states 0.82 m RMSE for the 10 m product; no figure for 30 m was found, so it is rounded up. |
| Copernicus GLO-30 | 2.0 m (`ERROR_GLO30`) | About 1.7 m RMSE against ICESat worldwide, 2.3 m against lidar in an independent city study. |
| Your own data | your figure | |

A check that was not run (no road lines, no buildings, no water history, no flood maps) leaves
its column empty (missing, not False); `attrs["checks"]` lists the checks that were run. The
table's columns are listed in [Outputs](outputs.md#the-confidence-table); `reason` gives the
grade in words.

**Agreement with a second terrain** (`compare`, below) can be added as `terrain_agreement`. It is
reported beside the grade and is **not part of it**, so that the grade can be tested against it.

## The road-crossing fix

An elevation model sees a road embankment but not the culvert or bridge opening under it, so
the embankment shows as a dam and the ground upstream as a depression. With
`breach_crossings=lines`, `analyze` and `analyze_tiled` run the fill a second time on ground cut
at every crossing:

- **Lines** (OpenStreetMap, `sources.osm.fetch_lines`, or your own): embankments are roads of
  the main classes (motorway to residential, service, `*_link`) and railways (including disused
  and abandoned beds), not in tunnels; streams are `waterway` river, stream, canal, drain or
  ditch, culverts included; bridges are embankments tagged `bridge`.
- **Crossings**: every point where an embankment meets a stream.
- **The cut**: along the stream, up to `breach_radius_m` (100 m) each side of the crossing, every
  pixel is lowered to the higher of the lowest ground on each side. Ground is never raised and
  never cut below the stream's own ground, so a basin with no lower way out is not drained.
  Where cuts share a pixel the lowest wins, so the order does not matter, and a tiled run cuts
  exactly the same pixels.

The depressions stay those of the uncut ground. Three columns are added:
`breached_depth_m` (the greatest depth after the cut, never more than `max_depth_m`),
`road_dam` (`breached_depth_m` below `min_depth`) and `bridge_crossing`. `meta["breach"]` counts
the lines, crossings, cut pixels and flags.

**Limits.** The fix is only as good as the map: a missing culvert line leaves its dam; a stream
drawn on the wrong side of a road misses its crossing. In Houston many `bridge_crossing` flags
are freeway underpasses: real low ground that is pumped, not a data error. So grade D means "not
a natural ponding place, or likely a data error", not "not there".

## Comparing two terrains: `compare`

`compare(a, b)` matches the depressions of two analyses of the same place, for example 30 m
against 10 m terrain. Both are laid on the finer grid; each depression of `a` gets the share of
its area that `b` covers (`compared_share`), the share of that part that depressions of `b`
cover (`covered_share`), its best match and the depth difference. It is **confirmed** when
`covered_share` is at least 25% (`CONFIRM_SHARE`). `attrs["summary"]` counts matched, missed and
extra depressions. This is how Study 3 tested the grade.

## In the US national tables

With e = 1.0 m, the 10,527,850 depressions of the national run (lower 48, 30 m) were graded:

| Grade | Depressions | Base grade (before flags and support) |
|---|---|---|
| A | 1,640,777 | 1,575,441 |
| B | 3,201,819 | 3,175,431 |
| C | 5,496,866 | 5,776,978 |
| D | 188,388 | |

123,791 are `road_dam`, 85,070 have an artefact flag (bridges; no buildings were used) and
320,517 have support (309,405 seen wet, 13,294 seen flooded).

Study 3 compared 16,606 of them, in 300 random H3 cells, with the same place analysed on 3DEP
10 m terrain. Share confirmed: A 68.0%, B 55.9%, C 49.7%, D 58.2% (282 depressions). The
difference A minus D was +9.9 points with a 95% interval of -3.2 to +23.2: **inconclusive**.
Road dams were confirmed slightly more often than unflagged depressions, but the 10 m terrain
has the same embankments, so that comparison cannot tell a road-held pond from a natural one.
Details: [Evidence](evidence.md#study-3-observed-water-and-the-confidence-grade-lower-48).
