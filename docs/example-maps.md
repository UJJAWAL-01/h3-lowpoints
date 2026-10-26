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
basemap. Each is one file of 2 to 6 MB.

> **Preview, 26 October 2026: not a flood map.** These maps use the first version of the
> runoff calculation (the Simple Method, 0.05 + 0.009 × percent paved), which treats fields and
> lawns as shedding only 5% of the rain. That is why almost every North Dakota pothole shows as
> never filling, although such basins fill in most springs. The next step replaces it with SCS
> curve numbers from soil and land cover, and the maps will be remade. Storm drains and pipes
> are not mapped, so some city depressions shown as landlocked are drained. See
> [Accuracy and limits](limitations.md#the-susceptibility-index).
