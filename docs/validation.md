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

A real-world worked example on public data will be added to this page.
