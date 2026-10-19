# The map

Every result can be turned into an interactive 3D web map: one HTML file you open in a browser,
send to someone, or put on any static web host.

```python
res.to_html("map.html", title="Central Austin")
```

```
h3lowpoints analyze dem.tif --map map.html
h3lowpoints map results_folder --out map.html --title "Central Austin"
python examples/gallery.py        # five real places and an index page, in maps/
```

## What you see

- **Hexagons** are the H3 cells that hold depressed ground. Cells with no depression are not
  drawn. Colour and height both show the chosen measure.
- **The side panel** shows the title, the data used (file, size, pixel size, analysis
  resolution), headline figures (number of depressions, area depressed, volume held, deepest
  depression) and the controls.
- **Orange dots** (optional) mark the lowest points of the deepest depressions.
- **Hovering** a cell shows its id, deepest depth, area, volume, number of lowest points, share of
  the covered area that is depressed, and the data coverage when below 100%. Hovering a dot shows
  the depression's depth, area and volume, and whether it touches the data edge, is likely a
  building artifact, or has a drain nearby.

## Controls

| Control | Does |
|---|---|
| H3 resolution | The analysis resolution and up to five coarser roll-ups. Each option shows its number of cells. |
| Colour and height show | The measure: deepest depression, volume held, area depressed, depressions in cell (lowest points), share of cell depressed, share of cell covered by the data. Only measures the data has are offered. |
| Height | Column height, from flat (0) to tall. The tallest column is at most 12% of the mapped area's diagonal, so heights stay readable at any zoom. |
| Mark each depression's lowest point | Shows the orange dots. The deepest depressions appear first; zooming in shows more (about 150 at a regional view, about two and a half times as many per zoom level, up to 20,000). |
| Basemap | Light grey, dark grey, satellite, or none. |
| − / + | Folds the panel away. |

Drag to pan, scroll to zoom, right-drag (or Ctrl-drag) to tilt and rotate.

### How values become colours and heights

- Depth, counts and shares use a **linear** scale; volume and area use a **logarithmic** scale,
  because they span many orders of magnitude.
- The scale runs from the 2nd to the 98th percentile of the cells shown, so a few extreme cells
  do not wash out the rest; linear scales start at 0, and shares run from 0% up to at most 100%.
  The legend shows the range; the top value is marked `≥` because the extremes are drawn at the
  top colour.
- At resolutions 0 to 3 (cells of 12,000 km² and more) the map switches from deepest depth to
  share of the cell depressed, because one deepest point says little about so large an area.

## Shareable links

Settings can be put after `#` in the address, so a link opens exactly the view you mean:

```
map.html#res=7&metric=volume&base=satellite&points=1&height=70&panel=0
```

| Key | Values |
|---|---|
| `res` | an H3 resolution present in the map |
| `metric` | `depth`, `volume`, `area`, `count`, `share`, `coverage` |
| `base` | `light`, `dark`, `satellite`, `none` |
| `points` | `1` to show lowest points |
| `height` | 0 to 100 |
| `panel` | `0` to start with the panel folded |

## Size limits

The map embeds its data, so very large results are thinned:

- A resolution with more than **120,000 cells** is not drawn; coarser roll-ups are used instead,
  and the subtitle says how many resolutions were left out (`max_cells` raises the limit).
- At most **20,000 lowest points** (the deepest) are embedded (`max_points`).
- Up to **six resolutions** are offered (`n_levels`).

A state-sized result therefore opens at a coarser resolution. Explore the finest cells of a large
region by mapping a smaller box, or in a GIS with the saved tables.

## Requirements and privacy

The page needs an internet connection to load two pinned libraries, deck.gl 9.0.38 and h3-js 4.1.0
(from the unpkg CDN), and the basemap tiles (Esri). No server, account or key is needed; the data
stays inside the file and is not sent anywhere. Opened offline, the page says the map library
could not be loaded. Basemap credits are shown in the corner as the tile providers require.

## Notes

- Coarser levels are exact roll-ups of the finest: deepest depth, summed area, volume and counts.
  Maps made from a `Result` (`to_html`, `analyze --map`) also measure coverage for every coarser
  level. Maps made from saved files (`h3lowpoints map`) have no access to the elevation data, so
  their coarser levels show coverage only at the analysis resolution.
- Colour ramps run from light to dark blue (dark basemap: dark to bright), readable by people with
  the common forms of colour blindness and in greyscale.
- The map shows depressions in the terrain model, not floods. The panel's "How to read this map"
  says so, along with a caveat matched to the data's resolution.
