# The explorer

The explorer is a web map of every closed depression on land in the lower 48 US states, with a
rain simulator. It runs entirely in your browser: there is no account and no server of our own.
The map data is read from public files (see [Data and files](#data-and-files)), and the place
search runs on your device, so nothing you type is sent anywhere.

It is for exploring and learning. It is **not a flood map, forecast or warning**, and not advice
of any kind. See [Accuracy and limits](limitations.md) and the disclaimer in the app.

## What the map shows

The map is drawn on [H3](concepts.md) hexagons. The level (H3 resolution) follows the zoom, or you
choose it with the Detail buttons.

| Levels | Each hexagon shows |
|---|---|
| 4, 5, 6 (country to region) | The share of the hexagon's area that lies in depressions, and the number of depressions whose lowest point is in it |
| 8, 9 (city) | The share of the hexagon in depressions; a click opens the depression with the most area in it |
| 10 (street) | How deep water stands at the hexagon's deepest point when its depression is full |

Depth when full is measured **down from the depression's rim**, the level at which water spills
out: it is never measured from sea level. Elevations come from the USGS 3DEP 30 m bare-earth
model (NAVD 88), whose vertical error is about 0.5 to 1 m; depressions shallower than 0.5 m are
not in the dataset. How depressions are found: [Definition](definition.md).

## A depression

Click a hexagon to open its depression. The panel gives, in plain words first:

| Item | Meaning |
|---|---|
| Holds | Volume of water when full (m³ and gallons) |
| Depth and area when full | At the lowest point; the area under water when full |
| Rim and lowest ground | Both above sea level (NAVD 88); depth = rim minus lowest ground |
| Storm that fills it | The smallest NOAA Atlas 14 24-hour storm (1 to 1,000 years) that fills it, counting water from upstream |
| Rain that fills it from its own land alone | Without water from depressions upstream |
| Land draining into it, curve number | Its catchment, and how much rain runs off it (SCS curve number, 0-100) |
| Where it overflows | The overflow path, depression to depression, drawn straight between lowest points (blue arrows); depressions overflowing into it (grey dotted lines) |
| Nearest major road | From its lowest point, US Census TIGER/Line 2024 primary and secondary roads |

Badges:

- **Road cut or underpass**: its lowest point is within 30 m of a major road and it is at least
  2.5 times longer than wide, lying within 25 degrees of the road. **On a major road**: within 30 m
  only. Storm drains usually empty these, but drains are not in the data.
- **Likely drained**: a mapped stream, ditch or canal within 30 m of its lowest point, or a major
  road within 30 m of where it overflows ([Susceptibility](susceptibility.md)).
- **Landlocked**: no sign of drainage in mapped lines (not proof that none exists).
- **Shallow**: less than 1 m deep, within the elevation model's error.

**Show: All depressions / Hide likely drained.** The second hides the likely drained and the road
features at city and street scale (the country summaries count every depression). Hidden
depressions still take part in the rain simulator's flow; they are only left out of the map and
its counts.

## 3D views

| View | What it draws |
|---|---|
| Flat | The map from above (the default; best on phones) |
| Columns | Each hexagon rises as a column: at street scale its height is its depth when full times the factor you choose; at city and country scale 1% of the hexagon in depressions is drawn as a fixed height (levels 4, 5, 6, 8, 9: 1,500, 600, 230, 30, 12 m), shares above 25% drawn at 25% |
| Wells | At street scale each depression sinks below the map as a hollow you look into, its hexagons stepping down to their depth when full, times the factor |

The factor (×1 true scale, ×5, ×10) is stated on the map. Columns and Wells each keep their own
factor; both start at ×10. Colour in 3D is depth, from sand (shallow) to brown (deep); blue is kept
for water. Away from street scale, Wells takes you to the nearest depression at least 1 m deep and
5,000 m² in area. Drag with the right mouse button or two fingers to tilt and turn; the compass
sets north up and flat again.

## The rain simulator

Open it with the raindrop button or "Run a storm here". It opens with this place's 10-year
24-hour storm and runs it at once.

### Choosing a storm

| Choice | Options |
|---|---|
| How rare | The N-year storm, N = 1, 2, 5, 10, 25, 50, 100, 200, 500 or 1,000: each depression gets NOAA Atlas 14's depth at its own location |
| How much | A depth of rain (mm or inches, up to 1,000 mm, the limit of the water-from-neighbours curves), the same everywhere |
| Duration | 1, 2, 3, 6, 12, 24 or 48 hours |
| Ground before the storm | Dry, average or wet: the NRCS curve numbers for dry, average and wet conditions (ARC I, II, III) |

Quick picks give this place's 2-, 10- and 100-year 24-hour storms. For a storm by amount the bar
says how rare it is at the centre of the view, by the same rule as the library on the same Atlas
14 tables ("more common than the 1-year storm" and "rarer than the 1,000-year storm" at the ends).
NOAA Atlas 14 does not cover Washington and Oregon: there, storms by rarity are not known and the
app says so; storms by amount still run.

### How it computes

The simulator uses the same equations as the h3-lowpoints library, in your browser:

1. **Runoff.** Each depression's catchment sheds the SCS curve-number runoff of the rain fallen so
   far (land cover Annual NLCD 2024, soils USDA gNATSGO).
2. **Fill and spill.** Each depression keeps what fits and passes the rest along its overflow link
   to the next one, in order from upstream to downstream. Water arriving from neighbouring areas is
   included (precomputed for each depression for rain of 0 to 1,000 mm).
3. **Depth and area.** The water level and the area under water at any volume come from the
   depression's measured levels (its volume and area 0.1, 0.25, 0.5, 1, 2 and 5 m below full).

Each storm is computed in **16 exact steps** (one sixteenth of its rain each). In the app's checks
these agree with the library to within 1e-6 for every step, storm kind, ground and test area
([Verification](verification.md) describes the library's own checks).

Definitions in the result:

| Term | Meaning |
|---|---|
| Full | Holds its volume when full |
| Overflowing | Receives more water than it holds, so water passes on |
| Holds some water | Holds any water at all |
| Ground under water, water held | Summed over the depressions whose lowest point is in the view |
| Of those full, likely drained | How many of the full ones are likely drained or road features (drains may keep them dry) |
| Dry and wet ground | The same storm on dry and on wet ground, at its end (counts only) |
| Fill earliest | The earliest step, then the largest (a tiny hollow filling first says little) |

"In this view" is the ground on screen; tilted, it is the ground in view up to three times the
size of the flat view, not out to the horizon.

### Time

What a depression holds depends only on the rain fallen so far, so the state at any hour of the
storm is the state at the rain fallen by then. The hours follow **NOAA Atlas 14's temporal
distributions**: the median (50%) curve of all storms of the NOAA region at that place, for the
published duration nearest in log time (6, 12, 24 or 96 hours; a 1- to 3-hour storm uses the
6-hour curve and a 48-hour storm the 24-hour one, stretched to its length), as the library does.
Where NOAA has no region (Washington, Oregon, some coasts and borders), the timeline is rain
fallen, not time.

For a selected depression the gauge gives:

- the exact share of the storm's rain at which it is full (found by bisection on the same
  simulation, to 1/16 of the storm divided by 65,536), as a depth of rain and as a time in a
  typical storm;
- the range of that time over NOAA's four groups of storms (by the quarter of the storm with the
  most rain), without saying which group gives which end;
- the water surface above sea level (lowest ground plus the depth of water).

Times are the **earliest it can fill**: rain counts as reaching a depression as it falls, with no
travel time over the ground (as in the library), and nothing drains or soaks away.

The region is looked up per H3 resolution-5 cell. For 1.6% of 20,000 sampled depressions, near
region borders, this gives a different region from the lowest point itself; then the time to half
the rain of a 24-hour storm differs by 0.8 h (median), 5.8 h at worst.

### Playing a storm

Play runs from no rain to the whole storm in about 6.4 seconds; the slider moves through the hours
(or the rain). Between two exact steps the map blends the water smoothly; **every number shown is
an exact step** (the last one reached). Falling rain over the map is decoration only, heavier for
more rain per hour; it is not shown when your device asks for reduced motion, and then play jumps
to the end.

Water on the map: Flat, blue over each hexagon (street scale: water depth; city scale: share of the
hexagon under water), grey beneath showing how deep each fills when full, hatched where not known;
Columns, water rising inside see-through columns; Wells, water rising inside the wells. Thin blue
lines appear from each depression in view that overflows, at the step its overflow starts (up to
2,000, the largest first). Hovering a street-level hexagon gives the water depth there now.

### What it leaves out

- Storm drains, sewers, culverts and pumps: in cities they carry much of the water away.
- Travel time over the ground; seepage; evaporation; snow and frozen ground; rivers in flood; tides.
- Features smaller than the 30 m elevation model can see.
- How the rain is spread in space beyond NOAA Atlas 14's own map (storms by amount are uniform).

## Do this in Python

A depression's panel, and the storm result for a view less than about 20 km across, have a button
**Do this in Python**: the h3-lowpoints code (library 0.5.1) that recomputes what the panel shows
from public data, for a box 0.05° (about 5 km) wider than the depression or the view on each side.
It downloads USGS 3DEP elevation, ESA WorldCover, Annual NLCD 2024, USDA gNATSGO soils and NOAA
Atlas 14 for the box, builds the water mask, finds the depressions with their catchments, works out
their curve numbers and the rain that fills them, and, for a storm, runs it with
`storms.storm_response` and counts the depressions whose lowest point's H3 cell lies in the view.
It runs on your computer, not in the explorer.

`app/tools/python_check.py` runs these exact snippets and compares them with the map (dataset 0.3),
in Houston, New York City and North Dakota:

| What | Result (2 November 2026) |
|---|---|
| 12 depressions: depth, area, volume, catchment, curve number, own-land rain, storm that fills it | equal, within 1 part in 10 million |
| 3 storms by amount: depressions in view, full, overflowing; water held | counts equal; water within 1 part in a million |
| 3 storms by rarity: the same | counts equal; water held within 0.04% to 0.12%: the map takes each depression's storm depth at its lowest point, the library averages it over the catchment |

Elsewhere a box run can differ slightly next to open water (dataset 0.3 was built before the
library's water-mask fix of 0.5.1) or when the land draining into a depression reaches beyond the
box ([Accuracy and limits](limitations.md)).

## Evidence

Every panel ends with its source and one line on what the studies found for what it shows: a
depression (flood reports against the 2-year storm and against landlocked or drained), an area
(the 30 m data against 1 m lidar) or a storm (predicted standing water against reports in ten
storms). About has the full table, negative results included. The text comes from
[Evidence](evidence.md); `tests/test_docs.py` fails if any number in it is not on that page.

## Links

Everything you see is in the link (after `#`), so Copy link shares it exactly:

| Part | Meaning |
|---|---|
| `map=zoom/lat/lon[/bearing/pitch]` | The view |
| `d=region.id` | The selected depression (H3 resolution-4 region and dataset id) |
| `v=columns` or `v=wells`, `x=1` or `x=5` | The 3D view and its factor (×10 when absent) |
| `show=undrained` | Hide likely drained |
| `s=r.10.24` or `s=a.130.6` | A storm: by rarity (years, hours) or by amount (mm, hours) |
| `g=dry` or `g=wet`, `t=0` to `t=16` | Ground before the storm (average when absent); the step (the end when absent) |

A link part that cannot be read is ignored with a short note.

**Diagnostics.** Opening the explorer with `?debug=1` shows a small panel with the frame rate,
the time the map and the simulator take per frame, long pauses, the screen and the graphics chip,
so that slowness on a device can be reported with numbers. It stays on the device; Copy puts the
numbers on the clipboard.

## Data and files

| File | What | From |
|---|---|---|
| `packs.bin`, `index.json` | 4,576 packs (format 2), one per H3 resolution-4 region: depressions, their resolution-10 cells, spill links across packs, water from neighbouring areas | The national dataset v0.3 |
| `rarity.bin`, `rarity_index.json` | NOAA Atlas 14 depths for 1 to 48 hours and 1 to 1,000 years per resolution-7 cell | NOAA Atlas 14 |
| `extras.bin`, `extras_index.json` | Rim and lowest ground (NAVD 88), distance to the nearest major road and road class, per depression | Dataset v0.3, Census TIGER/Line 2024 |
| `data/overview_*.bin`, `data/places.json` | Country summaries at levels 4-6; the place search | Dataset v0.3; Census 2024 Gazetteer |
| `data/temporal.json` | NOAA Atlas 14 temporal distributions: 29 regions, the region at each resolution-5 cell, median curves for 6, 12, 24, 96 h | NOAA Atlas 14 |

The first three are hosted on Hugging Face (`h3-lowpoints/explorer-data`, pinned to one commit) with
Zenodo as the fallback ([doi.org/10.5281/zenodo.23896025](https://doi.org/10.5281/zenodo.23896025));
the app reads one pack at a time with HTTP range requests. The others ship with the app. The tools
that build and check every file are in `app/tools/` in the repository.

## How it is checked

Every check below runs in a real browser (headless Chrome) against the same data:

| Check | Tool |
|---|---|
| Simulator = library on test areas (every storm kind, ground, all 16 steps) | `app/tools/check_engine_browser.mjs`, `check_storm.py` |
| Result panel = engine, engine = library for whole views, rarity readout = library rule | `app/tools/storm_test.mjs` with `storm_cases.py` |
| Timing curves = library, region lookup, exact fill share = library, fill times | `storm_test.mjs` with `timing_cases.py` |
| Elevations and road classes = dataset; the filter hides exactly the drained and road features | `app/tools/extras_test.mjs` |
| 3D heights = depth × factor; views keep the selection and storm | `app/tools/view3d_test.mjs` |
| Levels while zooming; overview columns only at overview levels | `app/tools/zoom_test.mjs` |
| Abuse while a storm plays (spam, map moves, views, phone, soak) | `app/tools/storm_stress.mjs` |
| Evidence text = the evidence page (every number) | `tests/test_docs.py` |
| "Do this in Python" snippets = the map | `app/tools/python_check.py` |
| The app in general: errors, links, keyboard, accessibility (axe) | `app/tools/ui_test.mjs`, `stress_test.mjs` |

Speed and smoothness measured in headless Chrome use software graphics and are not real-device
numbers; use `?debug=1` on a real device.
