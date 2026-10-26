# Example maps

Three interactive maps of 1° test areas, made while building the
[susceptibility index](susceptibility.md). They preview what the explorer web app will show:
every depression at least 0.5 m deep on 30 m USGS 3DEP terrain, drawn on its H3 cells.

- **Grey:** drained, meaning a mapped stream or canal within 30 m of its lowest point, or a
  highway or ramp within 30 m of where it overflows.
- **Coloured:** landlocked, coloured by the rarest NOAA Atlas 14 24-hour storm needed to fill it
  (counting overflow from depressions upstream), from dark red (a 1-year storm) to blue (not
  even the 1000-year storm).

Hover over a depression for its numbers, click a legend entry to hide or show that class, and
switch the basemap to satellite to compare with the ground.

| Map | Area | Depressions | What it shows |
|---|---|---|---|
| [Houston, Texas](examples/houston.html) | -95.9 to -94.9, 29.4° to 30.4° N | 15,984 | A large, mostly paved city: many street and yard depressions fill in frequent storms. |
| [North Dakota prairie potholes](examples/potholes.html) | -99.5 to -98.5, 47° to 48° N | 21,827 | Farmland full of closed basins, almost all landlocked. |
| [California Central Valley](examples/canals.html) | -120.6 to -119.6, 36.4° to 37.4° N | 7,557 | Irrigated farmland crossed by canals and streams, half of it drained. |

The maps open in any browser; they need an internet connection for the map library and the
basemap. Each is one file of 2.4 to 6.2 MB.

How often the landlocked and drained depressions together fill, in the 24-hour storms of NOAA
Atlas 14 (counting overflow from upstream):

| Area | Median curve number | Fill in a 2-year storm | 10-year | 100-year | Not even the 1000-year | Drained |
|---|---|---|---|---|---|---|
| Houston | 86 | 75% | 90% | 97% | 0.8% | 36% |
| North Dakota potholes | 76 | 10% | 26% | 57% | 21% | 6% |
| California Central Valley | 78 | 36% | 51% | 67% | 22% | 50% |

> **Preview, 26 October 2026: not a flood map.** Runoff comes from SCS curve numbers (NLCD land
> cover and paving, USDA soil groups) with average soil moisture before the storm; snowmelt and
> frozen ground, which fill the prairie potholes most springs, are not modelled. Storm drains and
> pipes are not mapped, so some city depressions shown as landlocked are drained. See
> [Susceptibility](susceptibility.md) and [Accuracy and limits](limitations.md#the-susceptibility-index).
