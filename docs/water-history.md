# Seen wet and seen flooded: observed water in each depression

A depression is a feature of the terrain: it says where water *could* collect. This page covers
two tables that say where satellites *saw* water, one row per depression:

- **water history** (`h3lowpoints.water`): how often Landsat saw water in the depression from
  1984 to 2021, from the JRC Global Surface Water record;
- **radar flood events** (`h3lowpoints.floods`): whether the depression was flooded in Sentinel-1
  radar flood maps (Copernicus GFM, 2015 onwards), or in flood maps of your own.

Both are observations, not predictions. They are joined to depressions by the depressions'
own outlines, at any scale from a city to the lower 48 states. Every rule below was written
down before the national data was looked at (the phase 7 study rules, with their dated
deviations), and every threshold is a named constant in the code.

## Quick start

```python
from h3lowpoints import analyze
from h3lowpoints.water import water_history
from h3lowpoints.floods import flood_events
from h3lowpoints.sources import gfm

box = (-95.8, 29.5, -95.0, 30.1)                      # Houston
res = analyze("houston_30m.tif", min_depth=0.5)
history = water_history(res)                           # one row per depression
scenes = gfm.search(box, "2017-08-25", "2017-09-05", min_flooded_pixels=25_000)
summary, seen = flood_events(res, scenes, advisory=True)
```

For a region too big to hold in memory, pass hooks to `analyze_tiled`; the tables are the same
as on the whole region and are saved next to the depression table:

```python
from h3lowpoints import analyze_tiled
from h3lowpoints.water import WaterHook
from h3lowpoints.floods import FloodHook

res = analyze_tiled("state.vrt", mask_path="water.vrt", min_depth=0.5, out_dir="state",
                    format="parquet",
                    hooks=[WaterHook(source="jrc_tiles"), FloodHook(scenes, advisory=True)])
res.tables["water_history"], res.tables["flood_events"], res.tables["flood_seen"]
```

## Water history (JRC Global Surface Water, 1984-2021)

### The record

The European Commission's Joint Research Centre mapped open water in every Landsat image from
March 1984 to December 2021, at 30 m, worldwide from 80° N to 60° S (Pekel et al. 2016,
version 1.4; see [Data sources](data-sources.md#jrc-global-surface-water)). The library reads
its yearly classification (per year: not water, seasonal water, permanent water, or no data),
its occurrence (per cent of clear views with water), its maximum water extent and its monthly
recurrence. Windows are read straight from the JRC server, so a small region costs a few
megabytes; a national run uses a local copy of the tiles (`source=`).

### Which pixels belong to a depression

- **outline** (`water_history(result)`, and `WaterHook` in tiled runs): the JRC pixels whose
  centres fall inside the depression's own pixels. This is the method used for the national
  tables.
- **cells** (`water_history_by_cells`): from a saved result's depression × cell table
  (`details=True`), each H3 cell's JRC values weighted by the depression's area in the cell.
  It needs no pixel labels, so it works on saved results, but it is an approximation: water
  anywhere in a cell is spread over the whole cell. On three test regions the two methods
  agreed for 92% of depressions, below the 95% the study asked for, so national work uses the
  outline method.

### Clear years

Landsat does not see through cloud, and some years have few images. A year only counts when
the satellite saw the place, but the yearly files hold 0 both for "no data" and for every pixel
outside the maximum water extent, so a single pixel cannot say whether it was seen. The views
are therefore judged per **area**:

- the area is the depression's H3 cell at resolution 5 (else 4, else 3) at its lowest point,
  the first one holding pixels inside the water extent; `views_res` records which, and 0 means
  none did (views unknown);
- a year is **clear** when the share of the area's extent pixels holding data is at least half
  its median over the 38 years, and at least 10%. The median is used, not a fixed share,
  because pixels first mapped as water after 2014 hold 0 in every earlier year;
- in a clear year every pixel of the depression counts as observed, and a 0 counts as not water.

### The rules

| Rule | Value (constant in `h3lowpoints.water`) |
|---|---|
| A **wet year**: a clear year with water (seasonal or permanent) on at least 10% of the depression's pixels, or on at least 1 ha, and in either case on at least 2 pixels | `WET_SHARE` 0.10, `WET_MIN_AREA_M2` 10,000, `WET_MIN_PIXELS` 2 |
| A **permanent year**: a wet year with permanent water on at least half of the pixels | `PERMANENT_SHARE` 0.5 |
| Fewer JRC pixels than this: too small to tell | `MIN_PIXELS` 3 |
| Fewer clear years than this: too few views | `MIN_CLEAR_YEARS` 20 |
| Clear-year share of the median, and its floor | `CLEAR_SHARE` 0.5, `CLEAR_FLOOR` 0.1 |
| Clear years needed for a trend | `TREND_MIN_YEARS` 10 |

Each depression gets one class, the first that applies:

| `water_class` | Meaning |
|---|---|
| `too_small` | fewer than 3 JRC pixels: the record cannot tell |
| `too_few_views` | fewer than 20 clear years |
| `permanent` | permanent years in at least 80% of the clear years (a pond or lake) |
| `seasonal` | wet in at least half of the clear years |
| `some_years` | wet in at least one clear year |
| `never_seen_wet` | never wet in a clear year (including when the views are unknown, flagged by `views_res` = 0) |

"Seen wet" in the confidence grade and in the study means `permanent`, `seasonal` or
`some_years`. The table's columns are listed in [Outputs](outputs.md#the-water-history-table).

### What the record cannot see

- **Short floods.** Landsat passes about every 16 days and is blind under cloud, which is when
  floods happen. Water that drains within days is mostly missed. **"Never seen wet" is not
  "never wet".**
- **Small places.** Pixels are 30 m (about 0.09 ha), so depressions under three pixels are
  `too_small`, and street sags are below what it can see.
- **Water under trees or in cities** is often missed by optical satellites.
- **Lakes and ponds are not depressions here.** Open water is masked out of the terrain before
  depressions are found (the water mask), so permanent water mostly shows as the edge of a
  depression next to it; nationally only 201 depressions are `permanent`.
- The record ends in December 2021.

## Radar flood events (Copernicus GFM, and your own maps)

### The maps

The Copernicus Global Flood Monitoring service maps flooded land on every Sentinel-1 radar image
worldwide at 20 m, since 2015, and radar sees through cloud
([Data sources](data-sources.md#copernicus-global-flood-monitoring-gfm)). `gfm.search` lists the
catalogue items over a box and a period; each item is one Sentinel-1 scene cut to one 300 km
tile. `gfm.read_scene` turns an item's layers into one coded map:

| Code | Meaning |
|---|---|
| 1 | flooded |
| 0 | observed, not flooded |
| 255 | not observed: outside the image, in GFM's exclusion mask (cities, dense forest, radar shadow, sand), normally water (GFM reference water), or, with `advisory=True`, under one of GFM's advisory flags |

Places the radar could not judge are never counted as dry.

**Advisory flags.** GFM marks pixels where its result is doubtful: low backscatter over a
region (snow, ice or very dry soil) and rough water (wind). In development regions in North
Dakota and the Texas High Plains most radar "floods" fell under the first flag, in dry or frozen
months, so the study counts flagged pixels as not observed (deviation 3). Use `advisory=True`
for the same rule. An item without an advisory file cannot be judged under it and is skipped
and listed.

### The rules

For each map, the depression is laid on the map's grid by nearest pixel and its pixels are
counted:

- **observed**: the map's observed pixels (code 0 or 1) cover at least 50% of the depression's
  area (`OBSERVED_SHARE`);
- **flooded**: observed, with flooded pixels at least 10% of its observed pixels
  (`FLOODED_SHARE`) and at least 2 (`MIN_FLOODED_PIXELS`).

`flood_events` returns two tables: a summary per depression (maps that reach it, observed it and
flooded it, the first and last flood, the largest flooded share and the flooded scenes) and the
long table `seen`, one row per observed depression and map, for scoring maps one by one. Both are
described in [Outputs](outputs.md#the-flood-event-tables).

**Your own flood maps.** Pass a dict `{name: path}` of rasters coded 1 flooded, 0 observed and
dry, anything else not observed. `gfm.write_flood_map` writes a GFM scene in that form, which also
suits `h3lowpoints validate --flood-map` ([Validation](validation.md#flood-maps)).

### What radar floods are, and are not

- **Most radar-mapped floods are river floods**, not water ponding in a closed depression. A
  depression flooded in a scene may have been under a river's flood, not filled by its own rain.
- One radar image is one moment: a flood that came and went between passes is missed.
- Radar misses water under trees and between buildings (GFM excludes most cities).
- Wet soil, snow, and calm sand can look like water; the advisory flags catch some of this.

## In the US national tables

The national run (lower 48 states, USGS 3DEP 30 m, 10,527,850 depressions after clipping;
dataset 0.4 in preparation) used the outline method on the whole JRC record and a fixed random
sample of 400 GFM items (2015-2025, scenes with at least 25,000 flooded pixels), of which 394 had
every layer, advisory flags as not observed.

| Water class | Depressions |
|---|---|
| `never_seen_wet` | 6,543,740 |
| `too_small` | 3,592,658 |
| `some_years` | 214,005 |
| `seasonal` | 95,199 |
| `too_few_views` | 82,047 |
| `permanent` | 201 |

Of the depressions, 8,502,323 lie where at least one sampled radar map reaches, 1,265,597 were
observed in at least one, and 13,294 were flooded in at least one. How these observations relate
to the fill index is Study 3 in [Evidence](evidence.md#study-3-observed-water-and-the-confidence-grade-lower-48).

## Credits and licences

- JRC Global Surface Water: CC BY 4.0. "Source: EC JRC/Google", Pekel et al. 2016,
  doi:10.1038/nature20584.
- GFM: Copernicus Emergency Management Service, free and open with acknowledgement: "Contains
  modified Copernicus Emergency Management Service information [years]" (`gfm.credit` writes it
  with the years used).

Each table's settings, rules and credit are kept in `meta.json` under `tables`
([Outputs](outputs.md#metajson)).
