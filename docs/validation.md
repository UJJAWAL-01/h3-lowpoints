# Validation against observations

Do the places where water problems were actually observed (flood reports, 311 calls, insurance
claims, high-water marks, photos, anything with coordinates) fall in or near depressions more
often than chance would put them there? `validate` answers that question for any result and any
set of observations, anywhere, with an uncertainty that respects clustering in space.

> **Association, not cause.** A lift above 1 says observations and depressions go together in
> this place. It does not say the depressions caused the observations, and a lift near 1 does not
> prove they play no part: drains, pumps, soil and where people report all stand in between. See
> [What this cannot tell you](#what-this-cannot-tell-you).

## Quick start

```python
from h3lowpoints import validate

v = validate("us48_30m_water",             # any saved result, or a Result in memory
             "reports.csv",                 # observations: lon/lat columns, or a vector file
             controls="addresses.csv",      # where observations could have come from
             boundary="city.gpkg")          # the area studied
print(v.summary())
v.save("validation.json", points="points.csv")
```

```
h3lowpoints validate us48_30m_water --observations reports.csv --controls addresses.csv \
    --boundary city.gpkg --out validation.json --points points.csv
```

This is the printed summary of a check run on the national dataset with 5,000 *random*
"observations" in a box around Houston, Texas, so the true lift is 1 by construction:

```
Observations near depressions: H3 resolution 10, rings about 131 m apart
5,000 observations, 50,000 controls (random points in the boundary); 95% intervals from 1,000 samples of 161 blocks (H3 resolution 6)

rings  ~metres  observations  controls   lift  95% interval
    0        0         17.3%     17.3%   1.00  0.93 to 1.06
    1      131         41.5%     41.0%   1.01  0.98 to 1.05
    2      263         60.9%     60.7%   1.00  0.98 to 1.03
    3      394         74.5%     74.3%   1.00  0.99 to 1.02
    4      526         83.2%     83.2%   1.00  0.99 to 1.01
    5      657         88.8%     88.9%   1.00  0.99 to 1.01
```

Real observations would show a lift above 1 at small k if they gather on depressed ground. The
command then prints the warnings (here: random controls are a weak baseline) and where it wrote
the report.

## The question, precisely

For each distance k = 0, 1, ... `max_rings`, counted in H3 rings at the result's resolution:

- **observation share(k)**: the share of observations whose cell is within k rings of a cell
  holding depressed ground (k = 0: the observation's own cell holds depressed ground);
- **control share(k)**: the same share for the control points;
- **lift(k)** = observation share(k) / control share(k).

A lift of 2 at k = 0 means observations sit on depressed ground twice as often as the controls
do. A lift of 1 means no more often than the controls. One ring is one cell spacing: about 131 m
at resolution 10, 350 m at resolution 9 (the report gives `ring_spacing_m`). The distance is
measured between cells, so "within 0 rings" means "in a cell with depressed ground", which at
resolution 10 is a hexagon of about 15,000 m².

## Inputs

**The result.** A saved folder (from `analyze`, `build`, `clip` or `Result.save`, CSV or
Parquet) or a `Result` in memory. Only the cells near the points are kept in memory, so the
national dataset works: 5,000 observations and 50,000 controls in a city-sized area took 20
seconds and 0.7 GB against the full 34-million-cell table (measured on a laptop).

**Observations.** A CSV with `lon`/`lat`, `longitude`/`latitude` or `lng`/`lat` columns in
degrees (an `id` column, if present, is carried through), a vector file in any CRS (GeoPackage,
GeoJSON, Shapefile), a DataFrame or GeoDataFrame, or an array of longitude, latitude. Polygons
and lines are represented by a point guaranteed to lie on them. Rows without coordinates are
dropped and counted.

**Controls: the choice that matters most.** Controls stand for where observations *could* have
come from. Reports of flooding come from where people live, drive and report; if the controls
are spread evenly over the land, the lift mixes the effect of depressions with the effect of
where people are (towns sit in valleys and on flats, which are also where depressions are). Good
controls come from the same population as the observations:

| Observations | Good controls |
|---|---|
| 311 or resident reports | address points, or building centres, of the same city |
| Insurance claims | all insured properties (claimed or not) |
| Street flooding reports | points along the street network |
| High-water marks | survey locations, or points along the surveyed rivers and roads |

Building footprints can be given directly: each polygon becomes a point inside it. Without
controls, `validate` places random points evenly by area over the `boundary` (`n_random`,
default 10 per observation and at least 1,000) and warns that this baseline is weak.

**The boundary.** The area studied. Points outside it are left out (and counted), because a
point outside the analysed area would wrongly count as far from any depression. Without a
boundary, a result made by `build` uses its box; a result clipped with `clip` should be given the
same boundary again. Choose the area the observations really cover: random controls over a
whole state, against reports from one city, measure the city, not the depressions.

## Uncertainty: the block bootstrap

Observations cluster: one storm floods one neighbourhood, and its reports are not independent
of each other. Treating every point as independent makes intervals far too narrow. Instead, the
area is cut into **blocks**, the H3 cells at `block_res` (default 6, about 36 km² each), and
each bootstrap sample draws as many blocks as there are, with replacement, each bringing all its
observations and controls. The lift is recomputed for every sample (`n_boot`, default 1000), and
the interval is the middle `ci` (default 95%) of those values.

Blocks must be larger than the clusters. In simulations with observations in tight clusters
(about 400 m across) and no real effect, the 95% intervals covered the true lift of 1 in 85% of
runs with resolution-6 blocks, and in 60% when each cell was its own block. With observations
not clustered, coverage was 88 to 92%. The intervals are therefore approximate, slightly narrow
rather than wide; treat a lower bound just above 1 with caution. Use a coarser `block_res` when
observations come in large clusters (a whole district flooded at once), and a finer one when
the area holds too few blocks (the report warns below 20 blocks with observations).

## Depth classes

`by_depth` splits the counts by the depth of the nearest depressed ground (the deepest of the
nearest cells, using each cell's `max_depth_m`), in classes set by `depth_classes` (default edges
0.5, 1 and 2 m: 0-0.5 m, 0.5-1 m, 1-2 m and 2 m or more). If deeper ground goes with a higher
lift, the association has a dose-response shape, which is more convincing than a single number.
The classes add up to the overall counts. The class is the depth at that place, not the depth of
the whole depression: the cell table does not record which depression a cell belongs to.

## Comparing classes of depressions

Depth is one way to split depressed ground. `classes` gives any other: a label for each
depression, such as how often it fills (from the [susceptibility layer](susceptibility.md)) or
whether it is landlocked. The question then becomes *are observations more concentrated near
depressions of one class than near those of another?*

```python
import pandas as pd
from h3lowpoints import validate

s = pd.read_parquet("us48_30m_v3/susceptibility.parquet", columns=["id", "landlocked"])
labels = pd.Series(s["landlocked"].map({True: "landlocked", False: "drained"}).to_numpy(), index=s["id"])
v = validate("us48_30m_v3", "reports.csv", controls="addresses.csv", boundary="city.gpkg",
             classes=labels, class_order=["landlocked", "drained"])
print(v.class_ratios.query("k == 0"))
```

**Which ground belongs to which depression.** The cell table does not record which depression a
cell belongs to, so a fixed rule assigns it: each labelled depression claims the depressed cells
within its equivalent radius, √(area / π), of its lowest point, rounded to whole rings (only the
lowest point's cell when that radius is under half a ring spacing, which at resolution 10 is
every depression under about 13,500 m²). Where claims overlap, the depression holding the most
water (`volume_m3`, then the lower `id`) wins. Depressed cells that no labelled depression claims
form the class `unclassified`: ground of depressions left out of `classes`, or the far parts of
long, irregular ones. The report counts the cells claimed and the cells left unclassified. A
depression's ground is a disc here, not its true outline, so the classes of a big, irregular
depression's edges are approximate; at 0 rings, where the comparison matters most, a point's
class is the class of the depression its own cell belongs to.

**The class of a point** is the class of its nearest depressed ground (the deepest of the
nearest cells, as for `by_depth`; equal depths go to the earlier class in `class_order`).
`by_class` gives the ring table for each class, and the classes add up to the overall counts.

**Comparing two classes.** For classes A and B, the **lift ratio** at distance k is
lift(A) / lift(B) = (observations near A / controls near A) / (observations near B / controls
near B): how many times more observations there are per control near A than near B. A ratio of 1
means no difference. Its interval comes from the same block bootstrap samples as the lifts, so
it accounts for clustering, and for A and B sharing observations' blocks. `class_ratios` gives
every pair in `class_order` (each class over each later one) at every k; `undefined_share` is the
share of samples where a class had no control nearby, so the ratio could not be computed.

Comparing classes answers a sharper question than the overall lift: both classes are depressed
ground, so the many things depressions share (low, flat, older neighbourhoods) cancel out to a
large extent, and what remains is the difference the label makes. It is still association: the
labels themselves come from models with their own errors.

**Suspect depressions.** Depressions flagged `deeper_than_wide` are almost always errors in the
elevation data. By default the ground around each one is left out: every cell within √area of its
lowest point's cell (at least one ring). This is an approximation for the same reason as above;
`exclude_suspect=False` (`--keep-suspect`) keeps them. The report counts what was left out.

## The report

`Validation.report` (written by `save` as JSON, with undefined values as `null`) holds:

| Field | Content |
|---|---|
| `result`, `res`, `ring_spacing_m` | The result used, its H3 resolution and the distance between neighbouring cell centres in metres. |
| `observations`, `controls` | `n` used, `outside_area` and `missing_coordinates` left out; for controls also `source` (`given` or random). |
| `area` | What decided which points were inside (`boundary`, the result's box, or `null`). |
| `max_rings`, `depth_classes_m` | The settings. |
| `depression_cells_near_points` | Depressed cells within reach of the points (after exclusion). |
| `excluded` | `deeper_than_wide` depressions and the cells left out around them (`null` when kept). |
| `rings` | One entry per k: `k`, `distance_m`, `obs_within`, `controls_within`, `obs_share`, `control_share`, `lift`, `lift_low`, `lift_high`. |
| `by_depth` | The same per `depth_class` and k. |
| `classes` | With `classes` only: `order`, `depressions_labelled`, `depressed_cells_claimed`, `depressed_cells_unclassified` (near the points), and the `rule` that assigned ground to depressions. |
| `by_class` | With `classes` only: the ring entries per `class` and k. |
| `class_ratios` | With `classes` only: one entry per pair and k: `class_a`, `class_b`, `k`, `ratio`, `ratio_low`, `ratio_high`, `undefined_share`. |
| `bootstrap` | `block_res`, `n_blocks`, `n_blocks_with_observations`, `n_boot`, `ci`, `seed`, and `undefined_share`: per k, the share of samples where the lift could not be computed (no control near depressed ground). |
| `warnings` | Every caveat raised, also issued as `UserWarning`s and printed by the command. |
| `package_version` | The library version. |

`lift` is `null` when no control is within k rings (the ratio is undefined).

**The point table** (`Validation.points`, written by `save(points=...)`) has one row per point
used:

| Column | Content |
|---|---|
| `point_id` | The input's `id`, or its row number. |
| `kind` | `observation` or `control`. |
| `lon`, `lat` | Position in degrees. |
| `h3` | Its cell at the result's resolution. |
| `rings` | Rings to the nearest depressed cell; empty when farther than `max_rings`. |
| `depth_m` | Depth of that nearest depressed ground (the deepest of the nearest cells). |
| `block` | Its bootstrap block (H3 cell at `block_res`). |
| `class` | With `classes` only: the class of that nearest depressed ground; empty when farther than `max_rings`. |

## What this cannot tell you

- **Cause.** Depressions sit where the land is flat and low, which is also where rivers, old
  wetlands, dense older neighbourhoods and combined sewers are. Any of these can produce the
  association.
- **Reporting bias.** Observations record where someone noticed, cared and reported, not where
  water collected. Controls drawn from the same population remove part of this, not all of it.
- **Position error.** Reports geocoded to an address or a street centreline can be tens of metres
  from where the water was; look at the lift at one or two rings, not only at zero.
- **Many distances.** Each k is a separate comparison on the same points; do not pick the most
  favourable one after the fact.
- **The terrain model.** A depression is a feature of the elevation data, with its errors and
  without drains or culverts ([Accuracy and limits](limitations.md)). A low lift can mean the
  drainage works.

## Worked example: New York City street flooding

An example of the method on public data, run against the published national dataset (version
0.1; version 0.2 has the same depressions in New York, so the results hold for it). It is
an illustration of how to use and read the tool, not a study of New York: nothing was tuned to
the city, and every setting below was fixed before any result was seen.

**Data** (all from [NYC Open Data](https://opendata.cityofnewyork.us), downloaded 22 October
2026):

| Role | Dataset | Rows |
|---|---|---|
| Observations | 311 service requests (`erm2-nwe9`) with descriptor *Street Flooding (SJ)*, January 2020 to July 2026 | 21,472 (248 without coordinates) |
| Secondary observations | the same, descriptor *Catch Basin Clogged/Flooding (Use Comments) (SC)* | 44,362 (313 without coordinates) |
| Controls | NYC Address Points (`uf93-f8nk`): every address in the city, so the controls stand for where residents who could report live | 967,871 |
| Boundary | Borough Boundaries (`gthc-hcne`) | 5 boroughs |

**Settings fixed in advance:** the defaults (0 to 5 rings, blocks at resolution 6, 1,000
samples, seed 0) for the main run; one sensitivity run with blocks at resolution 7, because the
city holds only about 40 resolution-6 blocks; and the catch-basin requests as a second set of
observations, expected to show a weaker association because many concern drain maintenance
rather than standing water.

**To reproduce:**

```
B=https://data.cityofnewyork.us/resource
curl -o street_flooding.csv "$B/erm2-nwe9.csv?\$select=unique_key,created_date,latitude,longitude&\$where=descriptor='Street%20Flooding%20(SJ)'&\$limit=100000"
curl -o address_points_raw.csv "$B/uf93-f8nk.csv?\$select=addresspointid,the_geom&\$limit=2000000&\$order=addresspointid"
curl -o boroughs.geojson "https://data.cityofnewyork.us/api/geospatial/gthc-hcne?method=export&format=GeoJSON"
```

The address points come as text such as `POINT (-73.95 40.74)`; one pandas step turns them into
`lon`/`lat` columns:

```python
import pandas as pd
a = pd.read_csv("address_points_raw.csv")
xy = a["the_geom"].str.extract(r"POINT \(([-\d.]+) ([-\d.]+)\)").astype(float)
pd.DataFrame({"id": a["addresspointid"], "lon": xy[0], "lat": xy[1]}).to_csv("address_points.csv", index=False)
```

```
h3lowpoints validate us48_30m_water --observations street_flooding.csv --controls address_points.csv --boundary boroughs.geojson --out main.json --points main_points.csv
```

Each run took about 40 seconds on a laptop against the full national dataset.

**Main result** (21,221 reports and 967,866 addresses inside the boundary; 3 reports and 5
addresses fell just outside it):

```
rings  ~metres  observations  controls   lift  95% interval
    0        0         16.7%     10.8%   1.54  1.31 to 1.79
    1      131         31.1%     27.0%   1.15  1.02 to 1.27
    2      263         45.7%     42.6%   1.07  0.99 to 1.16
    3      394         57.3%     55.9%   1.02  0.96 to 1.08
    4      526         66.1%     67.0%   0.99  0.94 to 1.02
    5      657         74.0%     76.0%   0.97  0.93 to 1.00
```

**Sensitivity** (blocks at resolution 7, 209 blocks): the same lifts, with intervals of 1.38 to
1.72 at 0 rings and 1.06 to 1.25 at 1 ring. **Catch-basin requests** (44,045 inside): lift 1.31
(1.16 to 1.45) at 0 rings and 1.04 (0.96 to 1.11) at 1 ring, weaker, as expected.

**By depth** (0 rings; the depth is the deepest depressed ground in the report's cell, which
can be under the national layer's 0.5 m minimum where a cell holds only the shallow rim of a
deeper depression):

| Depth class | Reports | Addresses | Lift | 95% interval |
|---|---|---|---|---|
| 0-0.5 m | 1,019 | 33,047 | 1.41 | 1.12 to 1.77 |
| 0.5-1 m | 1,143 | 31,502 | 1.65 | 1.40 to 1.91 |
| 1-2 m | 762 | 24,859 | 1.40 | 1.01 to 1.90 |
| >= 2 m | 611 | 15,218 | 1.83 | 1.23 to 2.43 |

**Reading it.** Street-flooding reports come from addresses in depressed cells about one and a
half times as often as addresses in general do, and the excess is confined to the depressed cell
and its immediate neighbours: by two to three rings (260 to 400 m) the lift is 1. That is the
pattern expected if water collects in the low spots themselves, and it holds with smaller blocks.
It is also modest: 83% of the reports are *not* in depressed cells, so most street flooding in
the city happens where the 30 m terrain model shows no depression (blocked drains, sewers at
capacity, depressions too small for 30 m data). There is no clear dose-response: the deepest
class has the highest lift, but the classes' intervals overlap. Reports are located at
an address or an intersection, and come from the residents who chose to report. They cluster in
time as well as space: the ten busiest days (among them 1 and 2 September 2021, the remnants of
Hurricane Ida) hold 16% of all reports. The city's combined sewers and pumps are not in the
terrain model at all.
