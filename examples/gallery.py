"""Build a small gallery of interactive maps from real Copernicus GLO-30 terrain.

    python examples/gallery.py [output_folder]        # default: maps/

For each place it downloads the 30 m DEM (public AWS open data, no account), finds the closed
depressions, and writes one interactive HTML map plus an index.html that links them. It needs an
internet connection and a few minutes. Open index.html in any browser.

GLO-30 is a surface model with a vertical error of a few metres, so these maps show large basins
and urban or forest artifacts, not street-level ponding. The descriptions say what to look for.
"""
import html
import sys
import tempfile
import time
from pathlib import Path

from h3lowpoints import analyze
from h3lowpoints.sources.copernicus import fetch_dem

PLACES = [
    ("netherlands", "Rotterdam and the Dutch delta", (3.4, 51.2, 4.9, 52.5), 8, 1.0,
     "Much of the Netherlands lies below sea level behind dikes and is pumped dry. They show up as "
     "large, deep depressions that spill at sea level, while the sea channels are gaps. Try the "
     "satellite basemap, and the share-of-cell metric at a coarse resolution."),
    ("death_valley", "Death Valley, California", (-117.4, 35.7, -116.4, 36.7), 8, 1.0,
     "A desert with closed basins, salt flats at 86 m below sea level, and steep mountain fronts."),
    ("alps", "Central Alps, Switzerland and Italy", (9.5, 46.0, 11.0, 47.0), 8, 1.0,
     "Glacial lakes and valley floors in steep terrain. Compare the depth and volume measures."),
    ("norway", "Norwegian fjords", (6.0, 60.0, 7.2, 61.0), 8, 1.0,
     "Fjords and sea are flat at 0 m in this data (valid elevation, no depressions), so they show "
     "as gaps between the cells of the surrounding mountains."),
    ("austin", "Austin, Texas", (-97.95, 30.1, -97.5, 30.5), 9, 1.0,
     "A city on limestone hills with reservoirs and quarries. Urban areas produce many small "
     "depressions in a surface model, which a lidar DEM would resolve properly."),
]


def build(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cards = []
    with tempfile.TemporaryDirectory() as tmp:
        for key, title, bbox, res, min_depth, blurb in PLACES:
            started = time.time()
            print(f"{title}: fetching ...", flush=True)
            dem = fetch_dem(bbox, Path(tmp) / f"{key}.tif")
            result = analyze(str(dem), res=res, min_depth=min_depth)
            result.to_html(out / f"{key}.html", title=title)
            m = result.meta
            cards.append((key, title, blurb, m["n_depressions"], len(result.cells), res))
            print(f"  {m['n_depressions']:,} depressions in {time.time() - started:.0f} s", flush=True)
    items = "".join(
        f'<li><a href="{k}.html"><b>{html.escape(t)}</b></a><br><span>{html.escape(b)}</span><br>'
        f"<small>{n:,} depressions &middot; {c:,} H3 cells at resolution {r}</small></li>"
        for k, t, b, n, c, r in cards)
    (out / "index.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>h3-lowpoints gallery</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#14213d}}
li{{margin:1.1rem 0}}span{{color:#566074}}small{{color:#7a8496}}a{{color:#1d4e89}}</style></head><body>
<h1>h3-lowpoints gallery</h1>
<p>Closed depressions found in Copernicus GLO-30 elevation data (30 m), shown on H3 cells.
Each map needs an internet connection for its basemap. A depression is a feature of the elevation
model, not a flood.</p><ul>{items}</ul></body></html>""", encoding="utf-8")
    return out / "index.html"


if __name__ == "__main__":
    print("written", build(sys.argv[1] if len(sys.argv) > 1 else "maps"))
