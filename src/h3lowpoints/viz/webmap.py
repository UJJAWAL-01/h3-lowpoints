"""A self-contained interactive web map of an analysis.

    res.to_html("map.html")                         # from a Result
    h3lowpoints map results_folder --out map.html   # from saved results

The page is one HTML file with the data inside it. It draws the H3 cells in 3D over a basemap,
lets you switch between the analysis resolution and every coarser roll-up, change what colour
and height show, and mark each depression's lowest point. Opening it needs an internet
connection for the deck.gl library and the basemap tiles, but no server and no account.
"""
import html
import json
from pathlib import Path

import h3
import numpy as np
import pandas as pd

from ..h3agg.aggregate import rollup
from ..pipeline import add_coverage

_TEMPLATE = Path(__file__).with_name("map_template.html")

CAVEATS = {
    "fine": "Fine DEM: street-scale depressions are resolved, but lidar artifacts (notably under "
            "buildings) also appear as depressions.",
    "medium": "Medium-resolution DEM: only large depressions are resolved, and its vertical error "
              "can be as large as the shallower ones.",
    "coarse": "Coarse DEM: this shows large basins only. Its vertical error is often larger than "
              "a street-scale depression, so small ones are noise.",
}


def _human(value, unit):
    """Short text such as '1.2 km2' or '34.5 thousand m3' for a magnitude."""
    if unit == "area":      # square metres in, square kilometres or hectares out
        return f"{value / 1e6:,.2f} km²" if value >= 1e6 else f"{value / 1e4:,.1f} ha"
    if value >= 1e9:
        return f"{value / 1e9:,.2f} bn m³"
    if value >= 1e6:
        return f"{value / 1e6:,.2f} million m³"
    return f"{value:,.0f} m³"


def _levels(cells, res, max_cells, n_levels, footprint):
    """The analysis resolution (if it is small enough to draw) and its roll-ups, finest first."""
    out, skipped = [], 0
    for r in range(res, -1, -1):
        table = cells if r == res else add_coverage(rollup(cells, r), footprint)
        if len(table) > max_cells:
            skipped += 1
            continue
        out.append((r, table))
        if len(out) == n_levels:
            break
    return out, skipped


def _level_json(r, table):
    columns = ["h", "depth", "area", "volume"]
    rows = np.column_stack([
        table["max_depth_m"].round(3), table["depress_area_m2"].round(1),
        table["depress_volume_m3"].round(1)]).tolist()
    ids = table["h3"].tolist()
    if "covered_area_m2" in table:
        columns += ["covered", "covered_frac"]
        extra = np.column_stack([table["covered_area_m2"].round(1), table["coverage"].round(4)]).tolist()
        rows = [a + b for a, b in zip(rows, extra)]
    return {"res": int(r), "cols": columns, "rows": [[i, *row] for i, row in zip(ids, rows)],
            "edge_m": h3.average_hexagon_edge_length(int(r), unit="m")}


def _points_json(depressions, max_points):
    top = depressions.nlargest(max_points, "max_depth_m")
    flag = lambda name: top[name].astype(int) if name in top else np.zeros(len(top), dtype=int)  # noqa: E731
    cols = ["lon", "lat", "depth", "area", "volume", "edge", "artifact", "drain"]
    rows = np.column_stack([
        top["lon"].round(6), top["lat"].round(6), top["max_depth_m"].round(3),
        top["area_m2"].round(1), top["volume_m3"].round(1), flag("edge"),
        flag("likely_building_artifact"), flag("drain_at_core")]).tolist()
    return {"cols": cols, "rows": rows}


def _bounds(table):
    centres = np.array([h3.cell_to_latlng(c) for c in table["h3"]])
    edge_deg = h3.average_hexagon_edge_length(h3.get_resolution(table["h3"].iloc[0]), unit="km") / 111.0
    south, west = centres.min(axis=0) - edge_deg
    north, east = centres.max(axis=0) + edge_deg
    return [float(west), float(south), float(east), float(north)]


def build_map(cells, depressions, meta, path, *, footprint=None, title=None, max_cells=120_000,
              max_points=20_000, n_levels=6):
    """Write the interactive map for an analysis to `path` and return the path.

    cells, depressions, meta: the three parts of a `Result`.
    footprint: the Result's footprint; with it the coarser resolutions get coverage too.
    max_cells: the finest resolutions with more cells than this are left out of the page (it
        would be slow to draw them); the coarser roll-ups are used instead.
    max_points: the deepest this many depressions can be marked individually.
    """
    if len(cells) == 0:
        raise ValueError("There are no depressions to draw")
    res = h3.get_resolution(cells["h3"].iloc[0])
    levels, skipped = _levels(cells, res, max_cells, n_levels, footprint)
    if not levels:
        raise ValueError(f"No resolution has at most {max_cells} cells; raise max_cells")
    total_area = float(depressions["area_m2"].sum())
    stats = [
        ["depressions", f"{len(depressions):,}"],
        ["area depressed", _human(total_area, "area")],
        ["volume held", _human(float(depressions["volume_m3"].sum()), "volume")],
        ["deepest", f"{float(depressions['max_depth_m'].max()):.1f} m"],
    ]
    source = Path(str(meta.get("dem", "DEM"))).name
    size = meta.get("cell_size_m")
    sub = [source]
    if meta.get("shape"):
        sub.append(f"{meta['shape'][1]:,} × {meta['shape'][0]:,} pixels")
    if size:
        sub.append(f"{size:.3g} m cells")
    sub.append(f"analysed at H3 resolution {res}")
    if skipped:
        sub.append(f"finest {skipped} resolution(s) not drawn: too many cells")
    payload = {
        "title": title or "Closed depressions on H3 cells", "subtitle": " · ".join(sub),
        "stats": stats, "caveat": CAVEATS.get(meta.get("resolution_tier"), ""),
        "bounds": _bounds(cells),
        "levels": [_level_json(r, t) for r, t in levels],
        "points": _points_json(depressions, max_points),
    }
    # every "<" becomes < (valid JSON), so no text in the data can open or close an HTML tag
    data = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    page = _TEMPLATE.read_text(encoding="utf-8").replace(
        "__TITLE__", html.escape(payload["title"])).replace("__DATA__", data)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def map_from_dir(directory, path, **kwargs):
    """Build the map from a folder written by `Result.save` (depressions.csv, cells.csv, meta.json)."""
    folder = Path(directory)
    missing = [n for n in ("depressions.csv", "cells.csv", "meta.json") if not (folder / n).exists()]
    if missing:
        raise ValueError(f"{folder} is missing {', '.join(missing)}; was it written by Result.save?")
    return build_map(pd.read_csv(folder / "cells.csv"), pd.read_csv(folder / "depressions.csv"),
                     json.loads((folder / "meta.json").read_text()), path, **kwargs)
