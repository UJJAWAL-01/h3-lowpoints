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

**Which ground belongs to which depression.** A result made with `details` (`--details`; the
national dataset has it) holds the depression x cell table, `depression_cells`, which records
how much of each depression lies in each cell. Each depressed cell then takes the class of the
labelled depression holding the most water in that cell (its `volume_m3` there, then the lower
`id`). This is exact.

Without that table, a fixed approximation is used: each labelled depression claims the depressed
cells within its equivalent radius, √(area / π), of its lowest point, rounded to whole rings
(only the lowest point's cell when that radius is under half a ring spacing, which at resolution
10 is every depression under about 13,500 m²), and where claims overlap the depression holding
the most water overall wins. A depression's ground is a disc here, not its true outline, and a
small depression that straddles a cell border only claims the cell of its lowest point, so many
depressed cells stay unclaimed: in a check on New York City, 4,393 of the 10,326 depressed cells
near the points (43%), against 1 with the exact assignment. Prefer results with
`depression_cells`.

Depressed cells that no labelled depression claims form the class `unclassified` (ground of
depressions left out of `classes`, and with the approximation, ground it misses). The report
says which assignment was used and counts the cells claimed and left unclassified.

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
| `classes` | With `classes` only: `order`, `depressions_labelled`, `assignment` (`depression_cells` or `equivalent radius`) and its `rule`, `depressed_cells_claimed` and `depressed_cells_unclassified` (near the points). |
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

## Study: the susceptibility layer against flood reports (New York City and Houston)

Does the [susceptibility layer](susceptibility.md) tell apart the depressions where flooding is
reported? Two hypotheses, the data, the classes, every setting and the rule for calling each
result were written down on 27 October 2026, before any report or address was downloaded:

- **H1 (storms):** flood reports are more concentrated, per address, near depressed ground whose
  depression fills in the 24-hour storm expected once in 2 years than near depressed ground whose
  depression needs a rarer storm or never fills.
- **H2 (drainage):** flood reports are more concentrated, per address, near landlocked depressed
  ground than near drained depressed ground.

Each is measured by the lift ratio at 0 rings ([Comparing classes](#comparing-classes-of-depressions)):
`2-year` over `rarer or never` for H1, `landlocked` over `drained` for H2. A result is
**supported** if its whole 95% interval lies above 1, **contradicted** if below 1, and
**inconclusive** otherwise. All results are reported here, whatever they showed.

**Layer:** the national susceptibility layer of dataset version 0.3 (`fills_in_years`,
`landlocked`; highways measured in every state, `verify` clean), as published: curve numbers from
Annual NLCD 2024 land cover and the soil groups read exactly (see the second deviation). **Settings:** the defaults
(0 to 5 rings, blocks at resolution 6, 1,000 samples, seed 0), and the same with blocks at
resolution 7 as a sensitivity check. **Two deviations** from the written plan, both recorded in
the plan itself. First, made before any real report was run: ground is assigned to depressions
exactly from the `depression_cells` table, not by the approximation first planned, after a test
with random addresses as stand-in reports showed the approximation left 43% of the depressed
cells near the points unclassified. Second, made *after* the results on a first version of the
layer had been seen: a pre-release audit found that the soil groups had been read at an offset
that depended on each region's box (3% of pixels in a test region took a neighbouring soil), so
the national curve numbers were computed again with the exact reader; NLCD 2021's server was down
at the time, so the recomputation used the newer Annual NLCD 2024 (same classes and grid). The study was then run again,
unchanged, on the corrected layer, which is the one published. Both sets of results are given
below; every verdict is the same.

**Data** (all public, downloaded 27 October 2026):

| Study | Reports | Addresses (controls) | Area |
|---|---|---|---|
| New York City | 311 *Street Flooding (SJ)* requests from 2020 (`erm2-nwe9`), as in the worked example above: 21,221 used | NYC Address Points (`uf93-f8nk`): 967,866 | the five boroughs |
| Houston, Memorial Day flood (May 2015) | City of Houston 311 flood requests (`MemorialDayFlood311_SZ`): 1,309, of which 1,146 located, 1,144 used | City of Houston site addresses (`COH_SiteAddresses`) with status `COH` (permanent, validated): 648,002 inside the city | Houston city limits (Census 2020) |
| Houston, Tax Day flood (April 2016) | the same for `TaxDayFlood311_SZ`: 1,114, of which 1,004 located, 988 used | the same | the same |

The layer's own 24-hour storm depths (NOAA Atlas 14): New York about 86, 134 and 212 mm for
the 2-, 10- and 100-year storms; Houston about 129, 219 and 425 mm. Of the depressions whose
lowest point lies in each area (1,777 in New York, 3,736 in Houston), 56% and 79% fill in the
2-year storm, and 20% and 33% are drained.

**Results** (lift ratio at 0 rings, 95% interval; blocks at resolution 7 in brackets):

| Study | H1: 2-year over rarer or never | H2: landlocked over drained |
|---|---|---|
| New York City | **1.28** (1.04 to 1.64), supported [0.98 to 1.65] | **0.64** (0.46 to 1.02), inconclusive [0.43 to 1.03] |
| Houston, 2015 | **9.9** (3.7 to 44.0), supported [4.0 to 45.9] | **0.29** (0.12 to 0.78), contradicted [0.15 to 0.65] |
| Houston, 2016 | **2.12** (0.99 to 5.91), inconclusive [1.08 to 5.11] | **0.26** (0.14 to 0.52), contradicted [0.13 to 0.49] |

On the first version of the layer (before the second deviation) H1 gave 1.27 (1.03 to 1.63),
9.7 (3.6 to 42.5) and 2.07 (0.97 to 5.77), and H2 the same ratios and intervals as above: the
landlocked and drained flags do not depend on curve numbers. The corrected curve numbers gave
6.5% of depressions nationally a different `fills_in_years`.

The counts behind them (0 rings: reports and addresses in depressed cells of each class, and
each class's lift over addresses in general):

| Study | 2-year | rarer or never | landlocked | drained |
|---|---|---|---|---|
| New York City | 2,054 / 54,469, lift 1.72 | 1,481 / 50,156, lift 1.35 | 3,008 / 94,010, lift 1.46 | 527 / 10,615, lift 2.26 |
| Houston, 2015 | 202 / 57,691, lift 1.98 | 4 / 11,340, lift 0.20 | 70 / 44,250, lift 0.90 | 136 / 24,781, lift 3.11 |
| Houston, 2016 | 140 / 57,691, lift 1.59 | 13 / 11,340, lift 0.75 | 48 / 44,250, lift 0.71 | 105 / 24,781, lift 2.78 |

**H1: supported in two of three studies, and the third points the same way.** In every study,
reports gather more on ground of depressions that fill in a 2-year storm than on ground of those
that need a rarer one. In New York the difference is modest (1.28) and its interval only just
clears 1 (with smaller blocks it does not). In Houston, ground of rarely filling depressions
received almost no reports (4 and 13), so the ratios are large but imprecise. The step is
between the 2-year class and the rest: in New York the graded classes give lifts of 1.72 (2-year),
1.28 (10-year), 1.40 (100-year) and 1.36 (rarer or never), not a steady decline. The two Houston
studies share their addresses and the same depressions, so they are not independent tests.

**H2: contradicted in both Houston floods, and New York leans the same way.** Reports were three
to four times *more* concentrated near drained depressions than near landlocked ones. The
likely reason, which this study did not test: "drained" means a mapped channel or a highway is
within 30 m. In Houston 59% of the drained depressions have a stream or bayou at their lowest
point and another 29% a ditch or canal; in New York about half are drained only by a highway at
their spill point. In ordinary
rain these carry water away; in an extreme storm the bayous overflow their banks and highway
underpasses fill, so the low ground beside them floods first. The flag describes whether water
that collects can leave after the storm (how long it stands), which reports made during a storm
do not measure. **Landlocked is therefore not a sign of more reported flooding;** see
[Drained or landlocked](susceptibility.md#drained-or-landlocked).

**Limits.** Six tests were made without correction for multiple testing. Reports come from
residents who chose to report, located at an address. The Houston addresses are the city's
current list, years after the floods. Two of the three studies are one city. Storm drains, pumps
and sewers are in none of the layers, and New York's combined sewers drain much of its
"landlocked" ground. Association is not cause.

The same two hypotheses were run again in four other cities in the second study below; neither
replicated cleanly ([results](#study-1-in-four-more-cities)).

## Study 2: storms in time against flood reports (six cities)

Does following a real storm in time ([Storms in time](storms.md)) mark the places that report
flooding better than the storm total alone? The cities, storms, settings, measures and the rule
for calling each result were written down on 29 October 2026, before the model was run on any
real storm or any report was scored. Three deviations, all made during the build and before any
scoring, are listed under **Limits**. All results are reported here, whatever they showed.

**Storms and roles.** Settings were chosen on four storms in two **calibration** cities and then
fixed; the hypotheses were tested on six storms in four **held-out** cities.

| Role | Storms (by date) |
|---|---|
| Calibration | New York 2021-09-01 and 2023-09-29; Houston 2015-05-25 and 2016-04-18 |
| Held out | Austin 2015-05-25 and 2015-10-30; New Orleans 2024-04-10; Chicago 2023-07-02 and 2025-08-17; Baltimore 2015-06-27 |

**Data** (all public). Rain: MRMS hourly gauge-corrected radar rain (Iowa Environmental Mesonet
archive), 72 hours per storm, each depression taking the hour-by-hour rain of the H3
resolution-8 cell holding its lowest point. Terrain: USGS 3DEP 10 m (the main runs), 30 m, and
1 m lidar in New York and Chicago, over each city's 2024 limits plus 2 km, open water masked
(ESA WorldCover). Land cover and paving: Annual NLCD 2024; soil groups gNATSGO; soil
infiltration USDA Soil Data Access. Reports: each city's public 311 requests in street-flooding
categories during the storm (Houston: the city's flood-request layers), repeats at one place on
one day counted once. Where people live: Census 2020 block populations (TIGERweb).

**Score and measure.** Unit: H3 resolution-9 cells (about 0.1 km2) inside the city limits. A
cell's score for a storm is the predicted standing-water area at the peak: the sum over
depressions of the wet area at that depression's peak (from its measured levels) times the share
of its area lying in the cell. The measure is the **population-weighted AUC**: the chance that a
cell with a report scores higher than the cell of a randomly chosen resident (ties count half);
0.5 is no better than where people live. 95% intervals from a block bootstrap (blocks at
resolution 6, resampled per city, 1,000 samples, seed 0). Pooled values are the mean over a
group's storms. A **placebo** came first: cases drawn at random by population gave pooled AUCs
of 0.501 to 0.505 for every model (required: 0.45 to 0.55).

**Models.** *Totals only*: the storm total and curve numbers, no timing, no seepage, no drains
(this equals the susceptibility index). *Ten duration-aware candidates*: method A (curve numbers
in time) or B (Green-Ampt), each with seepage, and drains of none or the 1-, 2-, 5- or 10-year
1-hour Atlas 14 rate on catchments at least 20% paved. The candidate with the highest pooled AUC
on the calibration storms at 10 m is **the model** (ties within 0.005 go to A, then to fewer drains).

**Calibration** (not a test):

| Model | Pooled AUC (95% interval) |
|---|---|
| Totals only | 0.571 (0.545 to 0.597) |
| A, no drains | 0.570 (0.545 to 0.597) |
| B, no drains | 0.570 (0.544 to 0.597) |
| A with drains of 1, 2, 5, 10 years | 0.553, 0.554, 0.546, 0.532 |
| B with drains of 1, 2, 5, 10 years | 0.546, 0.542, 0.533, 0.521 |

The model chosen is **A without drains**. Every drain rule lowered the AUC, the more so the larger
the drain.

**Hypotheses** (held-out storms, 10 m, the model as chosen; supported if the whole interval is on
the stated side, contradicted if on the other):

- **H1 (duration):** pooled AUC of the model minus that of totals only, above 0.
  **-0.0006 (-0.0018 to 0.0003): inconclusive.**
- **H2 (filling marks flooding):** pooled AUC of the model above 0.5.
  **0.575 (0.549 to 0.600): supported.**

| Held-out storm | Reports | AUC (95% interval) | Reading | Totals only |
|---|---|---|---|---|
| Austin 2015-05-25 | 247 | 0.528 (0.496 to 0.574) | inconclusive | 0.528 |
| Austin 2015-10-30 | 211 | 0.627 (0.573 to 0.686) | supported | 0.627 |
| New Orleans 2024-04-10 | 90 | 0.610 (0.470 to 0.673) | inconclusive | 0.611 |
| Chicago 2023-07-02 | 579 | 0.517 (0.489 to 0.551) | inconclusive | 0.516 |
| Chicago 2025-08-17 | 417 | 0.511 (0.458 to 0.573) | inconclusive | 0.515 |
| Baltimore 2015-06-27 | 69 | 0.660 (0.601 to 0.734) | supported | 0.660 |

Both readings hold with all report categories (adding catch basins and basements: H1 -0.0000,
-0.0014 to 0.0010; H2 0.558, 0.535 to 0.584) and without either of the two storms that have one
missing radar hour (H2 0.565 and 0.559, intervals above 0.5).

**Reported beside them:**

- **Method A minus B** (held out): 0.0004 (-0.0002 to 0.0012). No difference; A stays the default.
- **Terrain, 10 m minus 30 m** (all ten storms): **+0.012 (0.003 to 0.022)**; 10 m was higher in
  8 of 10 storms. Finer terrain helps, a little.
- **Terrain, 1 m lidar minus 10 m** (the four New York and Chicago storms): -0.008 (-0.039 to
  0.019), inconclusive, going both ways storm by storm. The lidar (New York mostly 2013-14, Chicago mostly 2016)
  is years older than the storms, and at 1 m 78% to 93% of the depressions filled.
- **Flood-insurance claims** (OpenFEMA NFIP claims for rainfall, block groups, exposure = policies
  in force; storms with at least 100 claims): New York 2021 0.606 (0.528 to 0.680), New York 2023
  0.488 (0.381 to 0.582), Houston 2015 0.487 (0.436 to 0.537), Houston 2016 0.586 (0.529 to
  0.646), Austin 2015-10-30 0.606 (0.318 to 0.769). Only the last is a held-out storm.

### Study 1 in four more cities

Study 1's H1 and H2 were run again exactly as written (the published layer, lift ratio at 0 rings,
blocks at resolution 6) in the four held-out cities, with all their street-category reports of all
years, and 200,000 points drawn by population from Census 2020 blocks as controls (these cities
have no address lists like study 1's). The rule set in advance: **replicates** if supported in at
least 3 of 4 cities and contradicted in none; **fails** if contradicted in 2 or more; else **mixed**.

| City | Reports | H1: 2-year over rarer or never | H2: landlocked over drained |
|---|---|---|---|
| Austin | 7,542 | 1.08 (0.34 to 1.95), inconclusive | 0.44 (0.27 to 0.85), contradicted |
| New Orleans | 3,507 | 0.78 (0.56 to 0.97), contradicted | 1.71 (1.06 to 2.40), supported |
| Chicago | 82,141 | 0.81 (0.65 to 1.02), inconclusive | 1.05 (0.72 to 1.39), inconclusive |
| Baltimore | 6,650 | 1.96 (0.80 to 3.16), inconclusive | 0.93 (0.67 to 1.27), inconclusive |

**Both mixed.** The 2-year class does not draw more reports in these cities (New Orleans goes the
other way), and landlocked against drained goes opposite ways in Austin and New Orleans.

### What study 2 says

- Predicted standing water marks the places that report flooding better than where people live,
  but modestly (AUC about 0.58), and in Chicago hardly at all.
- Following the storm in time adds nothing measurable to *where*; the storm total ranks places
  the same. The model's value is in what the total cannot give: depth, timing, how long water
  stands (none of which was tested here).
- A uniform drain rule makes things worse. Real drains, sewers and pumps are not in the model;
  Chicago and New York have combined sewers and New Orleans is pumped.
- 10 m terrain helps a little over 30 m; 1 m lidar did not help over 10 m in two dense cities.

**Limits.** Ten readings without correction for multiple tests. Small samples in New Orleans (90
reports) and Baltimore (69). 311 reports reflect who chooses to report. Missing radar hours
(Austin 2015-11-01 01 UTC, Baltimore 2015-06-26 14 UTC) were run as no rain. Austin's block
population inside its 2024 limits is 2.8% above its Census count. **Deviations** (all before any
scoring): rain was read for the whole terrain box plus one ring of resolution-8 cells, not only
the city cells, so depressions near the edge get rain; land and soil inputs were averaged on
resolution-10 cells, finer cells taking their parent's value, so the three terrain resolutions
differ only in their terrain; a block group's claims score is the mean of its cells' scores, not
the sum. Association is not cause.
