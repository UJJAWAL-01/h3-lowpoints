"""JRC Global Surface Water: where satellites saw water from 1984 to 2021, read for any box.

    from h3lowpoints.sources import jrc
    occurrence, transform = jrc.read_layer("occurrence", (-95.2, 29.9, -95.05, 30.05))

The European Commission's Joint Research Centre mapped open water in every Landsat image from
March 1984 to December 2021 at 30 m (Pekel et al. 2016, doi:10.1038/nature20584; version 1.4). The
files are cut into 10 x 10 degree tiles of 40,000 x 40,000 pixels of 0.00025 degrees in WGS 84,
from 80 N to 60 S, and are read here straight from the JRC open-data server by ranges: only the
256 x 256 blocks a box needs travel, so a small box costs a few megabytes. A folder of local tiles
(`source=`) can be used instead, with the server's file names.

Layers and their values (as stored in the files, checked against the server on 2026-11-11):

- `occurrence`: per cent of clear observations with water, 0 to 100. 0 also where water was never
  seen.
- `recurrence`: per cent of years in which water came back, 0 to 100.
- `extent`: 1 where water was seen at least once, else 0.
- `yearly` (with `year`, 1984 to 2021): 1 not water, 2 seasonal water, 3 permanent water, and
  0 for "no data" **and** for every pixel outside `extent`. The files are masked to the
  maximum water extent before export, so a 0 outside the extent says nothing about views.
- `monthly_recurrence` (with `month`, 1 to 12): per cent of years with water in that calendar
  month, 0 to 100, inside the extent.

Licence: CC BY 4.0 (copyright notice on the JRC server). Credit: "Source: EC JRC/Google" and the
paper above. Where no tile exists (open ocean, south of 60 S) the result is 0.
"""
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from ._common import check_bbox, gdal_env, url_exists

BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GSWE"
PIXEL = 0.00025                 # degrees (0.9 arc-seconds, about 28 m at the equator)
TILE = 40000                    # pixels per tile side (10 degrees)
WEST, NORTH = -180.0, 80.0      # corner of the tile grid
SOUTH = -60.0
YEARS = tuple(range(1984, 2022))
MONTHS = tuple(range(1, 13))
LAYERS = ("occurrence", "recurrence", "extent", "yearly", "monthly_recurrence")
NOT_WATER, SEASONAL, PERMANENT = 1, 2, 3


def _check(layer, year, month):
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {', '.join(LAYERS)}, not {layer!r}")
    if layer == "yearly" and year not in YEARS:
        raise ValueError(f"the yearly layer needs a year from {YEARS[0]} to {YEARS[-1]}")
    if layer == "monthly_recurrence" and month not in MONTHS:
        raise ValueError("the monthly_recurrence layer needs a month from 1 to 12")


def file_name(layer, row_off, col_off, *, year=None, month=None):
    """The server's name for one tile of a layer (offsets in pixels from 180 W, 80 N)."""
    _check(layer, year, month)
    key = f"{row_off:010d}-{col_off:010d}.tif"
    if layer == "yearly":
        return f"yearlyClassification{year}-{key}"
    if layer == "monthly_recurrence":
        return key
    return f"{layer}-{key}"


def tile_url(layer, row_off, col_off, *, year=None, month=None, base=BASE_URL):
    name = file_name(layer, row_off, col_off, year=year, month=month)
    if layer == "yearly":
        folder = f"YearlyClassification/LATEST/tiles/yearlyClassification{year}"
    elif layer == "monthly_recurrence":
        folder = f"MonthlyRecurrence/LATEST/tiles/monthlyRecurrence{month}"
    else:
        folder = f"Aggregated/LATEST/{layer}/tiles"
    return f"{base}/{folder}/{name}"


def grid_window(bbox):
    """(row0, col0, rows, cols) of the global pixel grid covering a box, grown to whole pixels."""
    west, south, east, north = check_bbox(bbox)
    if north <= SOUTH or south >= NORTH:
        raise ValueError(f"the box is outside the data ({SOUTH:g} to {NORTH:g} degrees latitude)")
    north, south = min(north, NORTH), max(south, SOUTH)
    eps = 1e-9
    col0 = math.floor((west - WEST) / PIXEL + eps)
    col1 = math.ceil((east - WEST) / PIXEL - eps)
    row0 = math.floor((NORTH - north) / PIXEL + eps)
    row1 = math.ceil((NORTH - south) / PIXEL - eps)
    return row0, col0, row1 - row0, col1 - col0


def grid_transform(row0, col0):
    """Affine transform of a window of the global grid whose top-left pixel is (row0, col0)."""
    return from_origin(WEST + col0 * PIXEL, NORTH - row0 * PIXEL, PIXEL, PIXEL)


def tiles_for_window(row0, col0, rows, cols):
    """(row_off, col_off) of every tile a window of the global grid touches."""
    r_first, r_last = row0 // TILE, (row0 + rows - 1) // TILE
    c_first, c_last = col0 // TILE, (col0 + cols - 1) // TILE
    return [(r * TILE, c * TILE) for r in range(r_first, r_last + 1)
            for c in range(c_first, c_last + 1)]


@lru_cache(maxsize=4096)
def _exists(url):
    return url_exists(url)


def _locate(layer, row_off, col_off, year, month, source):
    """Path or URL of a tile, or None if it does not exist."""
    if source is None:
        url = tile_url(layer, row_off, col_off, year=year, month=month)
        return "/vsicurl/" + url if _exists(url) else None
    # local tiles are laid out as on the server (`local_path`); a flat folder of server file
    # names also works, except for monthly recurrence, whose 12 months share their file names
    path = local_path(source, layer, row_off, col_off, year=year, month=month)
    if path.exists():
        return str(path)
    if layer != "monthly_recurrence":
        flat = Path(source) / file_name(layer, row_off, col_off, year=year, month=month)
        if flat.exists():
            return str(flat)
    return None


def local_path(source, layer, row_off, col_off, *, year=None, month=None):
    """Where a tile belongs in a local copy of the record: the server's path under `source`."""
    url = tile_url(layer, row_off, col_off, year=year, month=month)
    return Path(source) / url[len(BASE_URL) + 1:]


def read_window(layer, row0, col0, rows, cols, *, year=None, month=None, source=None):
    """A window of the global grid for one layer, as an array (0 where no tile exists)."""
    _check(layer, year, month)
    dtype = "int16" if layer == "monthly_recurrence" else "uint8"
    out = np.zeros((rows, cols), dtype=dtype)
    with gdal_env():
        for row_off, col_off in tiles_for_window(row0, col0, rows, cols):
            loc = _locate(layer, row_off, col_off, year, month, source)
            if loc is None:
                continue
            r0, c0 = max(row0, row_off), max(col0, col_off)
            r1 = min(row0 + rows, row_off + TILE)
            c1 = min(col0 + cols, col_off + TILE)
            with rasterio.open(loc) as src:
                part = src.read(1, window=Window(c0 - col_off, r0 - row_off, c1 - c0, r1 - r0))
            out[r0 - row0:r1 - row0, c0 - col0:c1 - col0] = part
    return out


def read_layer(layer, bbox, *, year=None, month=None, source=None):
    """(array, transform) of one layer over `bbox` = (west, south, east, north) in degrees.

    year: 1984 to 2021, for the `yearly` layer. month: 1 to 12, for `monthly_recurrence`.
    source: None to read from the JRC server, or a folder of tiles with the server's names.
    """
    row0, col0, rows, cols = grid_window(bbox)
    arr = read_window(layer, row0, col0, rows, cols, year=year, month=month, source=source)
    return arr, grid_transform(row0, col0)
