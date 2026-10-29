# Storms in time: how long it rains

The [susceptibility index](susceptibility.md) compares storm totals with what each depression
holds, so 300 mm in 6 hours and 300 mm in 3 days give the same answer. In reality the length of a
storm matters in three ways. This page describes the model that follows a storm in time steps,
where every number in it comes from, and what it cannot do.

| | What it says | Where the numbers come from |
|---|---|---|
| How rare the storm is | `storm_years`: years between storms of this depth and duration | NOAA Atlas 14, 19 durations from 5 minutes to 60 days |
| How the rain falls | the share of the rain fallen by each moment | NOAA Atlas 14 temporal distributions, by region |
| How much runs off | runoff in each step | SCS curve numbers (method A) or Green-Ampt infiltration (method B) |
| How a depression empties | seepage through its floor; storm drains, if assumed | USDA soil survey; an assumption you set |

Nothing in the model was tuned to observations. Which runoff method and which drain assumption
fit real storms best is the question of the validation study that follows (phase 5.4); until it
reports, both methods are offered and drains are off by default.

## Quick start

```python
from h3lowpoints.sources.soils import fetch_soil_infiltration
from h3lowpoints.storms import storm_response, write_storm

soil = fetch_soil_infiltration(None, "soil", cache_dir="cache", like="cn_land_cover.tif")
t = storm_response("houston", years=100, hours=6, curve_numbers="cn.tif", soil=soil,
                   impervious="cn_impervious.tif", cache_dir="atlas14")
write_storm("houston", t, "100yr_6h")
```

```
h3lowpoints fetch-landcover --bbox -95.6 29.6 -95.2 29.9 --out soil.tif --from soil-infiltration --like cn_land_cover.tif
h3lowpoints storm houston --years 100 --hours 6 --curve-numbers cn.tif --soil soil --impervious cn_impervious.tif --cache-dir atlas14
```

The result must have been computed with catchments (`catchments=True`, `--catchments`). The
curve-number and imperviousness rasters are those of [Susceptibility](susceptibility.md#runoff-curve-numbers).

## How rare a storm is

NOAA Atlas 14 publishes, for every place it covers, the depth of the storm expected once in 1,
2, 5, 10, 25, 50, 100, 200, 500 and 1000 years, for 19 durations from 5 minutes to 60 days. For
a duration between two published ones the depth is interpolated linearly in log(depth) against
log(duration); for a depth between two return periods, the return period is interpolated
linearly in log(years). A storm smaller than the 1-year storm gets `0`, one larger than the
1000-year storm `inf`: the model does not extrapolate beyond what NOAA estimated.

Checked against NOAA's point estimates at 12 cities, leaving each published value out and
predicting it from its neighbours: depth between durations is off by 1.2% at the median (3.8%
at the 90th percentile, 11.8% at worst); return periods by 3.3% at the median (9.4%, worst 46%).
Details in [Data sources](data-sources.md#noaa-atlas-14-design-rainfall).

So "12 inches in 6 hours in Houston" gets its return period from the 6-hour grids at the
lowest point of each depression. Return periods are NOAA's average recurrence intervals from
partial-duration series, as in the rest of the library.

## How the rain falls

Rain does not fall evenly: most storms bring most of their rain in a short burst. NOAA Atlas 14
publishes, for 6, 12, 24 and 96-hour storms in each of 30 regions of the lower 48 states, how the
rain of observed heavy storms was spread over their duration: for storms with most rain in the
first, second, third or fourth quarter, and for all storms together, as nine curves (10% of
storms had at least this much by then ... 90%). The default is the curve NOAA gives for all
storms at the median (`case="all"`, `percent=50`), in the region of each depression's lowest
point. A storm of another length takes the curve of the published duration nearest to it in log
time, stretched to its length (an assumption: NOAA publishes these four only). Uniform rain
(`pattern="uniform"`) and a curve of your own are also possible, and `simulate` takes one curve
per depression, as real radar rain will need.

## How much runs off

Two methods, both implemented, to be compared on real storms.

**A. Curve numbers in time** (`method="curve-number"`). The SCS runoff of the rain fallen so far,
`Q(P(t))` with the curve number of the catchment ([Susceptibility](susceptibility.md#runoff-curve-numbers)),
taken step by step. The total over the storm is exactly that of the index; what changes is when
the water arrives. The curve-number method does not know rain intensity: the same total gives
the same runoff whether it falls in 1 hour or in 24 (tested).

**B. Green-Ampt infiltration** (`method="green-ampt"`). The paved share of the catchment sheds
all its rain. The rest takes in water as fast as the Green-Ampt equation allows: the
infiltration capacity is `f = Ks (1 + ψ Δθ / F)`, where `Ks` is the saturated conductivity, `ψ`
the wetting-front suction, `Δθ` the water the soil can still take in and `F` what it has taken
in so far. Each step the library solves the exact Green-Ampt equation for water standing on the
ground through the step (`F2 - F1 - ψΔθ ln((F2 + ψΔθ)/(F1 + ψΔθ)) = Ks Δt`), and the soil takes
the smaller of that and the rain. Rain faster than the soil can take runs off, so a short intense
storm sheds more than a long gentle one of the same total (tested). The soil values come from
the USDA soil survey ([Data sources](data-sources.md#usda-soil-survey-how-fast-water-soaks-in)):
`Ks` is the least conductive layer in the top 50 cm, `ψ` the Rawls et al. (1983) value for the
surface texture. Each catchment takes the area-weighted mean of each value over its H3 cells,
and `ψΔθ` is the product of the means (a simplification).

## Dry, average or wet ground

`condition="average"` (default), `"dry"` or `"wet"`:

- Method A: the curve numbers of antecedent runoff condition II, I or III, from NRCS's table
  10-1 (National Engineering Handbook Part 630, chapter 10, 2004), linear between its rows (CN 74
  is 55 when dry and 88 when wet, NRCS's own example). NRCS describes ARC I and III as the low
  and high ends of the spread of runoff seen on the same ground (about the 10% and 90% curve
  numbers), which earlier practice tied to the rain of the 5 days before; they are not exact
  states of soil moisture.
- Method B: the water already in the soil: field capacity (1/3 bar) for average, the wilting
  point (15 bar) for dry, saturation for wet (then the soil takes in water only at `Ks`). These
  three states are an assumption of this library, chosen as the textbook bounds; they are not
  calibrated.

## How a depression fills and empties

### The shape of a depression

The model needs the water depth and wet area at any volume, but a depression is described by its
full volume `V`, maximum depth `H` and area `A`. It is taken as a bowl whose volume grows with
depth as `V(h) = V (h/H)^k`, with `k = A H / V` so that the wet area is exactly `A` when full.
Power laws like this are used for natural prairie depressions (Hayashi and van der Kamp, 2000,
Journal of Hydrology 237); for urban depressions they are an assumption. With `details=True` a
result holds the measured volume at 0.1 to 5 m below full, which allows a check (below).

### Steps

The storm is cut into equal steps of about 5 minutes (`step_minutes`), and the run continues for
72 hours after the rain stops (`after_hours`). In each step, depressions are taken in spill order
(each before the one it spills into), and for each:

1. its catchment's runoff of the step arrives, with the overflow its upstream neighbours passed on
   in the same step (no travel time over the ground: an assumption, mostly fair for small
   catchments);
2. water seeps through the floor at the soil's conductivity at the lowest point (times the
   unpaved share there) over the wet area, with a unit hydraulic gradient (an assumption: the
   pressure of deeper water is ignored, so seepage is if anything underestimated);
3. if drains are assumed, water leaves at up to their rate;
4. what is beyond its volume overflows, to the depression it spills into or off the map.

Depressions start empty, and nothing evaporates. Water is conserved exactly: for every
depression, inflow = overflow + seepage + drained + water left (tested to 1e-9 and checked by
`verify` on every saved storm table).

### Storm drains: an assumption you set

No national map of storm drains, pipes or pumps exists, yet in cities they carry away much of the
water. `drains={"years": N, "hours": T}` assumes that catchments at least `min_impervious` percent
paved (default 20) are drained, and that the drains carry away rain up to the intensity of the
N-year, T-hour Atlas 14 storm there (drainage systems are usually designed for a storm of some
return period; the right N and T, and whether such a rule works at all, are to be found on
calibration cities and tested on others in phase 5.4). Without it, no drains: the results are then
for ground that drains only into the soil.

### What is reported

Per depression: the storm's depth and return period, the runoff, all water reaching it, its peak
volume, depth and fill, when it filled, and where its water went (overflow, seepage, drains, left
at the end), and **`ponding_hours`**: hours with water at least `ponding_depth_m` (default 0.1 m)
deep at its lowest point. 0.1 m is a convention, not a calibrated threshold. A depression still
that deep when the run ends has `inf`: with no evaporation and slow soils, many landlocked
depressions hold water far longer than any run. See [Outputs](outputs.md#storm-tables).

## Examples

To be filled from the example runs (phase 5.1).

## Limits

- Every limit of the [susceptibility index](limitations.md#the-susceptibility-index) applies:
  terrain at 30 m, no pipes or pumps unless assumed, curve numbers and soil from national maps.
- Runoff reaches a depression in the step it falls; flow over the ground takes time, which
  matters for large catchments and short storms.
- One storm depth per catchment (its mean), one rain pattern per depression (at its lowest point).
- Depressions start empty, nothing evaporates, ponds do not freeze, and there is no groundwater.
- The bowl shape, the seepage gradient, the three soil-water states, the fallback soil values,
  the drain rule and the 0.1 m ponding depth are assumptions, each stated where it is used.
- The soil survey grid (July 2020) and the horizon data (current edition) are not the same
  edition; units without usable data take the fallback, and the share is reported.
- Green-Ampt sees the soil's conductivity, not its water table. NRCS puts many soils in group D
  for a high water table although they conduct water fairly well (around Houston, the median
  catchment conductivity is 8.7 mm/h in the example below); there method B may take in more water
  than ground with a high water table can, and the curve numbers (which use the groups) may be closer.
- Without drains, only seepage empties a depression, so in cities many are still full at the end
  of the run (`ponding_hours` inf). That describes ground drained by its soil alone, not a city
  street with inlets: use a drain rule, and read ponding hours with it in mind.
