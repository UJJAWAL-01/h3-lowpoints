# Susceptibility: how easily each depression fills

A depression fills when more rain runs into it than it can hold. This page describes how the
library compares the two for every depression of a result: the runoff from its catchment, the
rain depth that fills it, and, for design storms, how full each storm leaves it; and whether
mapped streams, canals or highways drain it. The answer is a **screening index**, transparent
term by term and needing no training data. It is still not a flood map
([Accuracy and limits](limitations.md#the-susceptibility-index)).

It needs a result computed with catchments (`catchments=True`, `--catchments`): each
depression's catchment, its volume, and the depression it overflows into (see
[Definition](definition.md#catchments-and-spill)).

## Quick start

```
h3lowpoints build --bbox -95.6 29.6 -95.2 29.9 --out houston --catchments --details --format parquet
h3lowpoints fetch-landcover --from curve-numbers --bbox -95.65 29.55 -95.15 29.95 --out cn.tif
h3lowpoints index houston --curve-numbers cn.tif --rain atlas14 --cache-dir atlas14
h3lowpoints fetch-landcover --from nlcd-landcover --bbox -95.65 29.55 -95.15 29.95 --out classes.tif
h3lowpoints drainage houston --nhd --tiger --cache-dir tiger --land-cover classes.tif
```

```python
from h3lowpoints.runoff import fetch_curve_numbers
from h3lowpoints.susceptibility import susceptibility, write_susceptibility

fetch_curve_numbers((-95.65, 29.55, -95.15, 29.95), "cn.tif", cache_dir="soils")
table = susceptibility("houston", curve_numbers="cn.tif", rain="atlas14", cache_dir="atlas14")
write_susceptibility("houston", table)       # houston/susceptibility.parquet + meta.json
table.sort_values("rain_to_fill_upstream_mm").head()   # the depressions that fill first
```

Give the curve numbers a box a little larger than the result's, so that catchments reaching past
its edge are covered. Outside the US, give your own curve-number raster (any CRS) or one curve
number (`curve_number=80`, `--curve-number 80`), and your own rain: one depth in millimetres, or
storms by return period (`rain={10: 80.0, 100: 150.0}`, each a depth or a function of latitude
and longitude giving millimetres).

## Runoff: curve numbers

How much of a storm runs off is set by the **SCS curve number** (CN, 0 to 100) of the NRCS
method *Urban Hydrology for Small Watersheds* (TR-55, 1986), the standard US method for design
storms. For a storm of P mm on ground of curve number CN:

> S = 25400 / CN − 254 (the most the ground can still hold, mm)
> Q = (P − 0.2 S)² / (P + 0.8 S) when P is more than 0.2 S, else 0

0.2 S is what the ground holds before any water runs off. A curve number of 98 (pavement) sheds
almost every millimetre; 61 (lawn on loam) holds the first 32 mm and then sheds a growing share.
Average soil moisture before the storm is assumed (TR-55's antecedent runoff condition II).

**Curve numbers in the US** (`runoff.fetch_curve_numbers`, `fetch-landcover --from
curve-numbers`) come from three 30 m layers on one grid:

- **land cover**, NLCD classes ([Data sources](data-sources.md#nlcd-land-cover));
- **imperviousness**, NLCD percent paved ([Data sources](data-sources.md#nlcd-percent-impervious));
- **soil hydrologic group**, from the USDA soil survey gNATSGO
  ([Data sources](data-sources.md#usda-soil-survey-gnatsgo-hydrologic-groups)): A (sand,
  gravel; water soaks in fast) to D (clay, shallow soil, a high water table; it sheds water).

Each pixel's curve number is TR-55's composite: its paved share at 98, the rest at the TR-55
value for its land cover on its soil group (`runoff.CN_TABLE`):

| NLCD class | TR-55 cover used for the unpaved ground | A | B | C | D |
|---|---|---|---|---|---|
| 21 to 24, developed | open space, good condition (lawns, parks) | 39 | 61 | 74 | 80 |
| 31, barren | newly graded areas, no vegetation | 77 | 86 | 91 | 94 |
| 41 to 43, forest | woods, fair condition | 36 | 60 | 73 | 79 |
| 51, 52, shrub | brush-weed-grass mixture, fair condition | 35 | 56 | 70 | 77 |
| 71 to 74, grassland; 81, pasture and hay | pasture, grassland or range, fair condition | 49 | 69 | 79 | 84 |
| 82, cultivated crops | row crops, straight row, good condition | 67 | 78 | 85 | 89 |
| 90, woody wetlands | woods, fair condition, on group D | 79 | 79 | 79 | 79 |
| 95, emergent wetlands | pasture, fair condition, on group D | 84 | 84 | 84 | 84 |
| 11, open water; 12, ice | as pavement | 98 | 98 | 98 | 98 |

Choices made and why: soils of a dual group (A/D, B/D, C/D: wet soils, the first letter if
artificially drained) are taken as **D**, since a wet soil in a depression is usually not
drained; map units without a group (urban land, pits, rock outcrop) and places the survey does
not cover are taken as D; wetlands as D whatever the map says, because their soils are
saturated; natural covers in fair condition, as nothing says better or worse.

Each H3 cell takes the mean curve number of the pixels whose centre lies in it (`cell_means`),
and a depression's `curve_number` is the **area-weighted mean over its catchment's cells**, as
TR-55 weights curve numbers over a watershed.

Worked example: a depression holding 500 m³ with a catchment of 10,000 m² of suburban yards on
clay loam (group C), 30% paved: CN = 0.3 × 98 + 0.7 × 74 = 81.2, so S = 58.8 mm and nothing runs
off until 11.8 mm of rain has fallen. 100 mm of rain sheds 52.9 mm; 30 mm sheds 4.3 mm.

## Rain to fill

- `rain_to_fill_mm`: the uniform rain that fills a depression from its **own catchment alone**:
  the storm whose runoff, spread over the catchment, equals its volume. With Q = volume /
  catchment area, the runoff equation turned round gives P = 0.2 S + (Q + √(Q² + 4QS)) / 2.
  In the example, Q = 50 mm and the rain to fill is 96.5 mm.
- `rain_to_fill_upstream_mm`: the same, also counting the **overflow of the depressions upstream**
  (those whose `spill_to` chain leads to it) that fill first and pass the rest on. It is never
  larger than `rain_to_fill_mm`, and equal to it when nothing spills in.

Neither needs rainfall data, so both are available everywhere, including where no design-storm
data exists. A small value means the depression fills in small storms.

`rain_to_fill_upstream_mm` is found by trying uniform rain depths that step up by 1%, each with
one pass down the spill links ([the cascade](#the-cascade)), from the smallest initial abstraction
(0.2 S) of any catchment to the largest own rain to fill. A depression's answer is interpolated
between the last depth that left it short and the first that filled it; the water reaching it
only grows with the rain, so the answer is exact to within 1%.

## Design storms

With storms (`rain`), each depression's own runoff is the runoff of its catchment's mean rain on
its curve number, times its catchment area; overflow then passes down the spill links.

- `fill_ratio_<N>yr`: the water reaching the depression in the 24-hour storm expected once in N
  years (its own runoff plus the overflow passed on to it), over its volume. 1 or more: it fills
  and overflows. By default for N = 2, 10 and 100 (`ratio_years`).
- `fills_in_years`: the smallest return period, among the storms given, whose storm fills it.
  `inf` when none does.
- With one uniform depth (`rain=50.0`, `--rain 50`), there is one column, `fill_ratio`.

`rain="atlas14"` (`--rain atlas14`) uses **NOAA Atlas 14** 24-hour storms of 1, 2, 5, 10, 25, 50,
100, 200, 500 and 1000 years, read from NOAA's published grids (about 800 m apart) at the centre
of each cell ([Data sources](data-sources.md#noaa-atlas-14-design-rainfall)). The 24-hour storm
is the usual design storm for how much water a basin must store. The method compares volumes and
has no pipes and no timing, so a storm enters it only through its total depth.

## The cascade

Every depression keeps the water reaching it up to its volume and passes the rest to the
depression it spills into, or off the map (`spill_to` 0, or a depression not in the table). So
the depressions spilling into one are settled before it: the cascade visits them in an order
where every depression comes before the one it spills into (spill links never loop). Water is
conserved: the runoff of all catchments equals the water stored plus the water leaving.

## Missing inputs and coverage

- Curve numbers must cover at least `min_coverage` (default 90%) of a catchment's area: the
  catchment's mean is then taken over the covered part. Below that, `curve_number` and
  everything computed from it are NaN. The same holds for the optional `impervious_pct`.
- Rain must cover at least `min_coverage` of a catchment's area; otherwise the storm columns are
  NaN.
- A NaN propagates downstream: a depression receiving overflow from one whose runoff is unknown
  has unknown storm columns and `rain_to_fill_upstream_mm`.
- **Washington and Oregon** have no Atlas 14 estimates (checked in October 2026), so their storm
  columns are NaN; the rain-to-fill columns are available there.

## Drained or landlocked

Many depressions in an elevation model are drained in reality: a canal, a ditch or a stream runs
through them, or the rim they overflow at is a highway embankment with a culvert under it that
the model cannot see. **Landlocked** depressions, where the water has no way out but to soak in
or evaporate, are the candidates for waterlogging in towns and on farms (how long water stands
was not measured). They are not where storm flooding is reported most: in New York and Houston, drained
depressions beside bayous, ditches and highways gathered more reports
([Evidence](#evidence-against-flood-reports)).
`drainage.drainage_flags` (`h3lowpoints drainage`) looks for signs of drainage in mapped lines:

- `stream_m`, `canal_m`: distance from the lowest point to the nearest mapped stream or river,
  and to the nearest canal, ditch, underground conduit or pipeline. In the US these come from the
  National Hydrography Dataset ([Data sources](data-sources.md#usgs-national-hydrography-dataset-nhd)):
  streams are feature types 460 and 558, canals 336, 420 and 428.
- `spill_road_m`: distance from the spill point to the nearest road given. With `--tiger`, the
  roads are highways and ramps only (Census TIGER/Line classes S1100 primary road, S1200
  secondary road, S1630 ramp; `MAJOR_ROADS`); `--all-roads` takes every road. It needs
  `catchments=True`.
- `channel_share`: the share of the depression's area in H3 cells that a stream or canal line
  crosses (lines are followed in steps of a third of a cell's edge). It needs `details=True`.
- `land_cover`: the land cover class at the lowest point (NLCD classes in the US, 0 for no data),
  so residential and farm depressions can be picked out.
- `drained`: a stream or canal within `near_m` (default 30 m) of the lowest point, or a road
  within `near_m` of the spill point. `channel_share` is reported but counts only if a minimum
  share is given (`min_share`, `--min-share`). `landlocked` is the opposite of `drained`.

**How the rule was chosen.** Before any comparison with flood reports, each sign was measured
against chance on three 1° test areas (built like the national layer): how often it occurs within
30 m of the depressions, as a multiple of how often it occurs within 30 m of random points on
land in the same area.

| Sign within 30 m | Houston (city) | North Dakota (prairie potholes) | California (Central Valley) |
|---|---|---|---|
| a stream, at the lowest point | 6.3× | 2.4× | 6.4× |
| a canal or ditch, at the lowest point | 3.4× | (almost none mapped) | 1.2× |
| any road, at the spill point | 1.1× | 1.9× | 2.1× |
| a highway or ramp, at the spill point | 2.0× | 3.0× | 6.6× |
| channels across cells holding half its area | 2.6× | 1.25× | 2.1× |

Streams and highways are telling everywhere; any road is not, in a city (in central Houston, 53%
of random points lie within 30 m of a road, and spill points no more often); the channel share
is weak where streams are few. The rule therefore keeps streams, canals and highways, and the
shares drained were 36% (Houston), 6% (North Dakota) and 50% (Central Valley).

Distances beyond `max_distance_m` (default 1 km) are left empty. A missing line file, or an
empty distance, never makes a depression drained: `landlocked` means **no sign of drainage in
the lines given**, not proof that there is none.

The drainage columns join the same susceptibility table as the index: computing either replaces
only its own columns and settings, in any order.

## The table and its checks

The columns are described in [Outputs](outputs.md#the-susceptibility-table), and the settings
are recorded in `meta.json` (`susceptibility`). `verify` checks a saved table: every depression
once; curve numbers in (0, 100] and `rain_to_fill_mm` the inverse of the runoff equation;
`rain_to_fill_upstream_mm` at most `rain_to_fill_mm` and at least the smallest initial
abstraction upstream; every storm with a fill ratio of 1 or more no rarer than `fills_in_years`;
the drainage distances in [0, `max_distance_m`], `channel_share` in [0, 1], `drained` following
the rule and `landlocked` its opposite ([Verification](verification.md)).

The test suite checks the runoff equation against TR-55 worked by hand and its inverse to
machine precision, the curve numbers against the table, the cascade against a plain recursive
version on random spill networks (water must be conserved), `rain_to_fill_upstream_mm` against a
bisection that reruns the cascade for every depression, and two independent answers against each
other: a uniform storm of depth D fills exactly the depressions whose `rain_to_fill_upstream_mm`
is at most D (within the 1%).

## Evidence against flood reports

A study fixed in advance compared the layer with 311 flood reports in New York City (2020 to
2026) and in Houston (the Memorial Day flood of 2015 and the Tax Day flood of 2016), per address
([full study](validation.md#study-the-susceptibility-layer-against-flood-reports-new-york-city-and-houston)):

- **How often a depression fills carries signal.** Reports gathered more on the ground of
  depressions that fill in a 2-year storm than on that of depressions needing a rarer storm:
  1.28 times as much in New York (95% interval 1.04 to 1.64), 9.9 times (3.7 to 44.0) and
  2.12 times (0.99 to 5.91) in the two Houston floods. Supported in two studies of three by the
  rule set beforehand; the third points the same way.
- **Landlocked depressions did not gather more reports; drained ones did.** Reports were 3 to 4
  times more concentrated near drained depressions in both Houston floods (ratios 0.29 and 0.26,
  intervals below 1), and New York leaned the same way (0.64, 0.46 to 1.02). Most of those
  depressions lie by bayous, ditches or highways, which overflow or fill in extreme storms.

Use `fills_in_years` (and the fill ratios) as the index of how readily a depression floods.
Read `landlocked` as *water that collects has no mapped way out*, which bears on how long it
stands, not on whether a storm floods the place; nothing here measured standing time.

## Limits

- **No drains, pipes, pumps or timing.** Storm drains, culverts and pumps can empty a depression
  the index says fills. A storm enters only through its total depth.
- **Average soil moisture.** Curve numbers assume average wetness before the storm. On frozen or
  saturated ground (spring snowmelt on the prairies) more runs off, and on very dry ground less.
  Snowmelt itself is not modelled.
- **One curve number per catchment.** The catchment's area-weighted mean curve number is used for
  all of it, as TR-55 does for small watersheds; where very different grounds share a catchment
  (pavement draining onto sand), this underestimates small storms.
- **Averages per H3 cell.** A cell's curve number applies to every part of a catchment in that
  cell, and rain is read at the cell's centre.
- **The soil survey is the July 2020 edition** of gNATSGO, the one published openly as a grid.
- **One pool per depression.** A depression fills as one pool: smaller pits inside it fill
  together with it (the fill curves describe its level, not separate sub-pits).
- **Spill links come from the terrain model.** A culvert under a road the model shows as a dam
  sends water on in reality but not in the model.
- **Clipped results.** A table computed before clipping counts water from depressions the clip
  later drops (`meta["susceptibility"]["clipped_after"]`); one computed after clipping misses the
  water from depressions beyond the boundary. Near a boundary, compute before clipping where you
  can.
- **Drainage flags are only as good as the maps.** Storm drains and sewers are mostly not
  mapped, so in cities many depressions flagged landlocked are drained by inlets. NHD maps
  ditches unevenly from county to county.
- **Atlas 14 project boundaries.** Where two of NOAA's project areas meet, both grids have
  values; they typically differ by about 5% (at most 41% in the 100-year 24-hour grids), and the
  mean is used.
