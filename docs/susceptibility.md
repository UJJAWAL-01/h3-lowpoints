# Susceptibility: how easily each depression fills

A depression fills when more rain runs into it than it can hold. This page describes how the
library compares the two for every depression of a result: the runoff from its catchment, the
rain depth that fills it, and, for design storms, how full each storm leaves it. The answer is a
**screening index**, transparent term by term and needing no training data. It is still not a
flood map ([Accuracy and limits](limitations.md#the-susceptibility-index)).

It needs a result computed with catchments (`catchments=True`, `--catchments`): each
depression's catchment, its volume, and the depression it overflows into (see
[Definition](definition.md#catchments-and-spill)).

## Quick start

```
h3lowpoints fetch-landcover --from nlcd --bbox -95.6 29.6 -95.2 29.9 --out imperv.tif
h3lowpoints build --bbox -95.6 29.6 -95.2 29.9 --out houston --catchments --format parquet
h3lowpoints index houston --impervious imperv.tif --rain atlas14 --cache-dir atlas14
```

```python
from h3lowpoints.susceptibility import susceptibility, write_susceptibility

table = susceptibility("houston", impervious="imperv.tif", rain="atlas14", cache_dir="atlas14")
write_susceptibility("houston", table)       # houston/susceptibility.parquet + meta.json
table.sort_values("rain_to_fill_upstream_mm").head()   # the depressions that fill first
```

Outside the US, give your own imperviousness raster (percent, any CRS) or one runoff coefficient
(`runoff_coef=0.5`, `--runoff-coef 0.5`), and your own rain: one depth in millimetres, or storms
by return period (`rain={10: 80.0, 100: 150.0}`, each a depth or a function of latitude and
longitude giving millimetres).

## Runoff

Of the rain falling on a catchment, a share runs off the surface and the rest soaks in or
evaporates: the **runoff coefficient**. It is taken from how much of the ground is paved or
built (imperviousness, percent):

> runoff coefficient = 0.05 + 0.009 × imperviousness

This is the **Simple Method** (Schueler 1987, *Controlling Urban Runoff*, Metropolitan Washington
Council of Governments), widely used in US stormwater design: bare soil and vegetation shed 5%,
fully paved ground 95%. One input, published, and easy to inspect.

The imperviousness of each H3 cell is the mean of the raster's pixels whose centre lies in the
cell (`cell_means`); NLCD (30 m, lower 48 states) is the usual US source
([Data sources](data-sources.md#nlcd-percent-impervious)). A depression's **runoff coefficient**
is the area-weighted mean over the cells of its catchment, and its **runoff area** is
`runoff_coef × catchment_area_m2`: a rain depth times the runoff area is the volume of water that
reaches the depression from its own catchment.

Worked example: a depression holding 500 m³, with a catchment of 10,000 m² that is 30%
impervious, has a runoff coefficient of 0.05 + 0.009 × 30 = 0.32 and a runoff area of 3,200 m².

## Rain to fill

- `rain_to_fill_mm`: the uniform rain depth that fills a depression from its **own catchment
  alone**, `1000 × volume_m3 / runoff_area_m2`. In the example, 500 / 3,200 m = 156 mm.
- `rain_to_fill_upstream_mm`: the same, also counting the **overflow of the depressions upstream**
  (those whose `spill_to` chain leads to it) that fill first and pass the rest on. It is never
  larger than `rain_to_fill_mm`, and equal to it when nothing spills in.

Neither needs rainfall data, so both are available everywhere, including where no design-storm
data exists. A small value means the depression fills in small storms.

`rain_to_fill_upstream_mm` is found by trying uniform rain depths that step up by 1%, each with
one pass down the spill links ([the cascade](#the-cascade)), between two bounds every answer lies
within: the volume over the runoff area of the depression and everything upstream (all the
upstream water arrives), and `rain_to_fill_mm` (none does). A depression's answer is
interpolated between the last depth that left it short and the first that filled it, so it is
exact to within 1%.

## Design storms

With storms (`rain`), each depression's own runoff is the rain on each cell of its catchment
times the cell's area and runoff coefficient, summed; overflow then passes down the spill links.

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

- Imperviousness must cover at least `min_coverage` (default 90%) of a catchment's area: the
  catchment's mean is then taken over the covered part. Below that, the runoff columns and
  everything computed from them are NaN.
- Rain must cover at least `min_coverage` of a catchment's runoff; otherwise the storm columns
  are NaN.
- A NaN propagates downstream: a depression receiving overflow from one whose runoff is unknown
  has unknown storm columns and `rain_to_fill_upstream_mm`.
- **Washington and Oregon** have no Atlas 14 estimates (checked in October 2026), so their storm
  columns are NaN; the rain-to-fill columns are available there.

## Drained or landlocked

Many depressions in an elevation model are drained in reality: a canal, a ditch or a stream runs
through them, or the rim they overflow at is a road embankment with a culvert under it that the
model cannot see. **Landlocked** depressions, where the water has no way out but to soak in or
evaporate, are the ones that matter for waterlogging in towns and on farms.
`drainage.drainage_flags` (`h3lowpoints drainage`) looks for signs of drainage in mapped lines:

```
h3lowpoints fetch-landcover --from nlcd-landcover --bbox -95.6 29.6 -95.2 29.9 --out classes.tif
h3lowpoints drainage houston --nhd --tiger --cache-dir tiger --land-cover classes.tif
```

- `stream_m`, `canal_m`: distance from the lowest point to the nearest mapped stream or river,
  and to the nearest canal, ditch, underground conduit or pipeline. In the US these come from the
  National Hydrography Dataset ([Data sources](data-sources.md#usgs-national-hydrography-dataset-nhd)):
  streams are feature types 460 and 558, canals 336, 420 and 428.
- `channel_share`: the share of the depression's area in H3 cells that a stream or canal line
  crosses (lines are followed in steps of a third of a cell's edge). It needs a result computed
  with `details=True`.
- `spill_road_m`: distance from the spill point to the nearest road (Census TIGER/Line in the US).
  It needs `catchments=True`.
- `land_cover`: the land cover class at the lowest point (NLCD classes in the US, 0 for no data),
  so residential and farm depressions can be picked out.
- `drained`: a stream or canal within `near_m` (default 30 m) of the lowest point, channels
  crossing cells that hold at least `min_share` (default half) of its area, or a road within
  `near_m` of the spill point. `landlocked` is the opposite.

Distances beyond `max_distance_m` (default 1 km) are left empty. A missing line file, or an
empty distance, never makes a depression drained: `landlocked` means **no sign of drainage in
the lines given**, not proof that there is none. The rule's default distances are not yet
calibrated (see [Limits](#limits)).

The drainage columns join the same susceptibility table as the index: computing either replaces
only its own columns and settings, in any order.

## The table and its checks

The columns are described in [Outputs](outputs.md#the-susceptibility-table), and the settings
are recorded in `meta.json` (`susceptibility`). `verify` checks a saved table: every depression
once; the coefficient, runoff area and rain to fill follow from their formulas;
`rain_to_fill_upstream_mm` lies between its two bounds; and every storm with a fill ratio of 1 or
more is no rarer than `fills_in_years`; the drainage distances lie in [0, `max_distance_m`],
`channel_share` in [0, 1], `drained` follows the rule and `landlocked` is its opposite
([Verification](verification.md)).

The test suite compares the cascade with a plain recursive version on random spill networks,
checks that water is conserved, compares `rain_to_fill_upstream_mm` with a bisection that reruns
the cascade for every depression, and checks two independent answers against each other: a
uniform storm of depth D fills exactly the depressions whose `rain_to_fill_upstream_mm` is at
most D (within the 1%).

**Evidence against observations: not yet measured.** Whether depressions that fill in frequent
storms are where flooding is reported is the next step of the [roadmap](roadmap.md), with the
[validation tools](validation.md). This page will report the result, whatever it is.

## Limits

- **No drains, pipes, pumps or infiltration timing.** Storm drains, culverts and pumps can empty
  a depression the index says fills. The runoff coefficient stands for all losses to the ground.
- **The Simple Method was made for typical storms.** In large storms, pervious ground sheds more
  than 5%, so rare storms are underestimated on fields and lawns; methods using soils (such as
  SCS curve numbers) are not part of this version.
- **Averages per H3 cell.** A cell's imperviousness applies to every part of a catchment in that
  cell, and rain is read at the cell's centre.
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
- **Roads are everywhere in cities.** On 30 m data in Houston, 53% of random points lie within
  30 m of some road, and spill points (48%) are no more often near a road than random points
  are; highways and ramps alone come closer (5.1% of spill points within 30 m against 3.4% of
  random points). Streams (19% of lowest points within 30 m against 2% of random points) and
  canals (10% against 3%) are far more telling. The road rule is therefore to be calibrated on
  test regions before it is used for a national layer.
- **Atlas 14 project boundaries.** Where two of NOAA's project areas meet, both grids have
  values; they typically differ by about 5% (at most 41% in the 100-year 24-hour grids), and the
  mean is used.
