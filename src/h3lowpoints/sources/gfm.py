"""Copernicus Global Flood Monitoring (GFM): radar flood maps from Sentinel-1, 2015 onwards.

    from h3lowpoints.sources import gfm
    scenes = gfm.search((-95.8, 29.5, -95.0, 30.1), "2017-08-25", "2017-09-05",
                        min_flooded_pixels=25_000)
    codes, transform, crs = gfm.read_scene(scenes.iloc[0], (-95.8, 29.5, -95.0, 30.1))

GFM maps flooded land on every Sentinel-1 image worldwide at 20 m (Copernicus Emergency Management
Service, run by EODC). The archive is open through EODC's STAC catalogue (stac.eodc.eu, no
account). Each catalogue item is one Sentinel-1 scene cut to one 300 km Equi7 tile, as
cloud-optimised GeoTIFFs.

`read_scene` turns three of its layers into one coded map:

    1    flooded                        (ensemble flood extent = 1)
    0    observed, not flooded
    255  not observed: outside the image, in GFM's exclusion mask (cities, dense forest, radar
         shadow, sand, ...), or normally water (GFM reference water, permanent or seasonal)

so places the radar could not judge are never counted as dry.

Licence: the CEMS Early Warning Data Store terms (rev. 11, 2023) cover GFM and allow reuse and
modification with the acknowledgement in `CREDIT` (docs/data-sources.md has the details). The
`flooded_pixels` number of an item belongs to its whole Sentinel-1 scene: every tile of that
scene carries the same number.
"""
import json
import time
import urllib.request

import numpy as np
import pandas as pd

from ._common import USER_AGENT, check_bbox

STAC = "https://stac.eodc.eu/api/v1"
COLLECTION = "GFM"
CREDIT = "Contains modified Copernicus Emergency Management Service information {year}"
FLOODED, DRY, NOT_SEEN = 1, 0, 255
NODATA = 255
LAYERS = {"flood": "ensemble_flood_extent", "exclusion": "exclusion_mask",
          "reference": "reference_water_mask"}
CATALOGUE_COLUMNS = ["item", "datetime", "tile", "flooded_pixels", "floodable_pixels",
                     "anomaly_detected", "version", "geometry", "flood", "exclusion", "reference",
                     "advisory"]


def _post(url, body, timeout=180, tries=4):
    data = json.dumps(body).encode()
    for attempt in range(tries):
        request = urllib.request.Request(url, data=data, headers={
            "User-Agent": USER_AGENT, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)


def _rows(features):
    from shapely.geometry import shape

    for f in features:
        p, a = f["properties"], f["assets"]
        yield {"item": f["id"], "datetime": p["datetime"], "tile": p.get("Equi7Tile"),
               "flooded_pixels": p.get("flooded_pixels"),
               "floodable_pixels": p.get("floodable_pixels"),
               "anomaly_detected": p.get("anomaly_detected"),
               "version": p.get("processing:version"), "geometry": shape(f["geometry"]),
               **{k: a.get(v, {}).get("href") for k, v in LAYERS.items()},
               "advisory": a.get("advisory_flags", {}).get("href")}


def search(bbox, start, end, *, min_flooded_pixels=None, page=500, post=_post):
    """GFM catalogue items touching a lon/lat box between two dates (inclusive), as a table.

    start, end: dates or times ("2017-08-25" or "2017-08-25T12:00:00Z").
    min_flooded_pixels: keep only items whose scene has at least this many flooded 20 m pixels
        (filtered by the catalogue, from metadata only).
    Columns: item, datetime, tile, flooded_pixels, floodable_pixels, anomaly_detected, version,
    geometry (the tile's outline, lon/lat), and the URLs of the flood, exclusion and reference
    layers and of the advisory flags (0 none, 1 low regional backscatter: snow, ice or dryness,
    2 rough water: wind, 3 both; not used by `read_scene`). Sorted by item id.
    """
    west, south, east, north = check_bbox(bbox)
    t0 = start if "T" in str(start) else f"{start}T00:00:00Z"
    t1 = end if "T" in str(end) else f"{end}T23:59:59Z"
    body = {"collections": [COLLECTION], "bbox": [west, south, east, north],
            "datetime": f"{t0}/{t1}", "limit": page}
    if min_flooded_pixels is not None:
        body["query"] = {"flooded_pixels": {"gte": int(min_flooded_pixels)}}
    url, rows = f"{STAC}/search", []
    while True:
        answer = post(url, body)
        rows.extend(_rows(answer.get("features", [])))
        nxt = next((link for link in answer.get("links", []) if link.get("rel") == "next"), None)
        if nxt is None or not answer.get("features"):
            break
        url = nxt["href"]
        if nxt.get("body"):
            body = {**body, **nxt["body"]} if nxt.get("merge") else nxt["body"]
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in CATALOGUE_COLUMNS})
    out = pd.DataFrame(rows, columns=CATALOGUE_COLUMNS).drop_duplicates("item")
    return out.sort_values("item").reset_index(drop=True)


def missing_layers(item, advisory=False):
    """The layers (flood, exclusion, reference, and advisory when asked for) an item has no
    file for. Such an item cannot be judged: without its exclusion mask, masked ground would
    count as dry."""
    names = list(LAYERS) + (["advisory"] if advisory else [])
    return [k for k in names if not isinstance(item.get(k), str) or not item.get(k)]


def code(flood, exclusion, reference, advisory=None):
    """The coded map (1 flooded, 0 observed dry, 255 not observed) from the three layers. With
    `advisory` (GFM's advisory flags), pixels GFM flags (1 snow, ice or dryness; 2 wind; 3 both)
    are not observed either."""
    seen = ((flood != NODATA) & (exclusion == 0) & (reference == 0))
    if advisory is not None:
        seen &= advisory == 0
    return np.where(seen, np.where(flood == 1, FLOODED, DRY), NOT_SEEN).astype("uint8")


def read_scene(item, bbox=None, *, crs=None, opener=None, advisory=False):
    """One catalogue item as a coded map (see the module doc) on GFM's own 20 m grid.

    item: a row of `search` (or a dict with `flood`, `exclusion` and `reference` URLs or paths).
    bbox: read only the part inside this box, given in lon/lat, or in `crs` when given. None
        reads the whole tile.
    advisory: also treat pixels under GFM's advisory flags (snow, ice, dry soil, wind) as not
        observed; the item then needs its `advisory` URL.
    Returns (codes, transform, crs); codes is None when the box misses the tile.
    """
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window

    from ._common import gdal_env

    opener = opener or rasterio.open
    if missing_layers(item, advisory):
        raise ValueError(f"GFM item {item.get('item', '')} has no file for "
                         f"{', '.join(missing_layers(item, advisory))}: it cannot be judged")
    names = list(LAYERS) + (["advisory"] if advisory else [])
    with gdal_env():
        srcs = {k: opener(item[k]) for k in names}
        try:
            ref = srcs["flood"]
            if bbox is None:
                win = Window(0, 0, ref.width, ref.height)
            else:
                b = transform_bounds(crs or "EPSG:4326", ref.crs, *bbox, densify_pts=21)
                win = covering_window(b, ref.transform, ref.width, ref.height)
            if win is None or win.width <= 0 or win.height <= 0:
                return None, None, ref.crs
            arrays = {}
            for k, s in srcs.items():
                if s.shape != ref.shape or s.transform != ref.transform:
                    raise ValueError(f"GFM layer {k} of {item.get('item', '')} is not on the "
                                     "flood layer's grid")
                arrays[k] = s.read(1, window=win)
            return (code(arrays["flood"], arrays["exclusion"], arrays["reference"],
                         arrays.get("advisory")), ref.window_transform(win), ref.crs)
        finally:
            for s in srcs.values():
                s.close()


def covering_window(bounds, transform, width, height):
    """The window of a raster that covers `bounds` (in its CRS) with a one-pixel margin, cut to
    the raster; None when they do not meet. Rounded outward, so no pixel whose centre lies in
    the bounds is ever left out (callers decide by pixel centre what is inside)."""
    import math

    from rasterio.windows import Window, from_bounds

    w = from_bounds(*bounds, transform)
    c0 = max(math.floor(w.col_off) - 1, 0)
    r0 = max(math.floor(w.row_off) - 1, 0)
    c1 = min(math.ceil(w.col_off + w.width) + 1, width)
    r1 = min(math.ceil(w.row_off + w.height) + 1, height)
    if c1 <= c0 or r1 <= r0:
        return None
    return Window(c0, r0, c1 - c0, r1 - r0)


def write_flood_map(item, bbox, out_path):
    """Write one item's coded map inside a lon/lat box to a GeoTIFF (nodata 255). Returns the
    path, or None when the box misses the item's tile."""
    import rasterio

    codes, transform, crs = read_scene(item, bbox)
    if codes is None:
        return None
    with rasterio.open(out_path, "w", driver="GTiff", height=codes.shape[0], width=codes.shape[1],
                       count=1, dtype="uint8", crs=crs, transform=transform, nodata=NOT_SEEN,
                       compress="deflate") as dst:
        dst.write(codes, 1)
    return out_path


def credit(datetimes):
    """The acknowledgement CEMS asks for, with the years of the scenes used."""
    years = sorted({str(d)[:4] for d in datetimes})
    span = years[0] if len(years) == 1 else f"{years[0]}-{years[-1]}"
    return CREDIT.format(year=span)
