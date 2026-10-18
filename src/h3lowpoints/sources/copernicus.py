"""Copernicus GLO-30: a global 30 m surface model, read from the public AWS open-data bucket.

    from h3lowpoints.sources.copernicus import fetch_dem
    fetch_dem((-97.9, 30.1, -97.6, 30.4), "austin_glo30.tif")

The product is cut into 1 x 1 degree tiles in WGS 84 (EPSG:4326), with heights in metres above
the EGM2008 geoid. Things worth knowing before you trust a result:

- It is a *surface* model: tree canopy and buildings are included, and its vertical error is
  a few metres. It resolves large basins, not street-level depressions.
- Tiles get narrower in longitude towards the poles (3600 columns at the equator, 1800 at 60
  degrees). A request spanning such bands is resampled to the finest columns; a warning says so.
- Tiles that are entirely open ocean do not exist. They are left as nodata in the result, which
  the analysis treats as an outlet. Ocean inside an existing tile is plain 0 m.
- Lakes and rivers are flattened in the source data but are not marked. Pass your own mask to
  `analyze` if open water should not count as terrain.

Data licence: free to use with the attribution given by the Copernicus programme (see
https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model).
"""
import math
import urllib.request
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge

BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
NODATA = -32768.0
_PIXEL = 1.0 / 3600.0


def tile_name(lat, lon):
    """Name of the tile whose south-west corner is (lat, lon), both whole degrees."""
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def tile_url(lat, lon, base=BASE_URL):
    name = tile_name(lat, lon)
    return f"{base}/{name}/{name}.tif"


def tiles_for_bbox(west, south, east, north):
    """South-west corners (lat, lon) of every 1 degree tile that a bounding box touches."""
    if not (west < east and south < north):
        raise ValueError("bbox must be (west, south, east, north) with west < east and south < north;"
                         " a box crossing the antimeridian must be split in two")
    if west < -180 or east > 180 or south < -90 or north > 90:
        raise ValueError("bbox is outside the valid range (lon -180..180, lat -90..90)")
    lon0, lon1 = math.floor(west), math.ceil(east) - 1
    lat0, lat1 = math.floor(south), math.ceil(north) - 1
    return [(lat, lon) for lat in range(lat0, lat1 + 1) for lon in range(lon0, lon1 + 1)]


@lru_cache(maxsize=1)
def available_tiles():
    """Names of every tile that exists in the bucket (about 26,000)."""
    with urllib.request.urlopen(f"{BASE_URL}/tileList.txt", timeout=60) as response:
        return frozenset(line.strip() for line in response.read().decode().splitlines()
                         if line.strip())


def _locate(lat, lon, source):
    """Path or URL of a tile, or None if it does not exist."""
    name = tile_name(lat, lon)
    if source is None:
        return "/vsicurl/" + tile_url(lat, lon) if name in available_tiles() else None
    path = Path(source) / f"{name}.tif"
    return str(path) if path.exists() else None


def fetch_dem(bbox, out_path, *, source=None, max_tiles=16):
    """Write the GLO-30 DEM for `bbox` = (west, south, east, north), in degrees, to a GeoTIFF.

    source: None to read from the public AWS bucket, or a folder holding tiles named like
        Copernicus_DSM_COG_10_N30_00_W098_00_DEM.tif (a local copy).
    max_tiles: refuse requests touching more tiles than this (each is about 50 MB of 32-bit
        floats in memory), so a typo cannot start a continent-sized download.
    Returns the output path. The raster is WGS 84, float32, with nodata -32768.
    """
    tiles = tiles_for_bbox(*bbox)
    if len(tiles) > max_tiles:
        raise ValueError(f"bbox touches {len(tiles)} tiles (limit {max_tiles}); "
                         "raise max_tiles or fetch it in pieces")
    found = [(t, _locate(*t, source)) for t in tiles]
    present = [(t, loc) for t, loc in found if loc]
    if not present:
        raise ValueError("No GLO-30 tiles cover this box (open ocean?). "
                         f"{len(tiles)} tile(s) checked")

    west, south, east, north = bbox
    # Snap outwards to the 1/3600 degree pixel grid; the small tolerance stops float noise in a box
    # that already lies on the grid from adding a spurious extra pixel.
    eps = 1e-6
    snapped = (math.floor(west / _PIXEL + eps) * _PIXEL, math.floor(south / _PIXEL + eps) * _PIXEL,
               math.ceil(east / _PIXEL - eps) * _PIXEL, math.ceil(north / _PIXEL - eps) * _PIXEL)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3",
                      GDAL_HTTP_RETRY_DELAY="1", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        datasets = [rasterio.open(loc) for _, loc in present]
        try:
            xres = sorted({round(d.res[0], 9) for d in datasets})
            if len(xres) > 1:
                warnings.warn(
                    "The tiles have different longitude resolutions (they narrow towards the "
                    f"poles): {xres}. The result uses the finest and repeats pixels in the rest.",
                    UserWarning, stacklevel=2)
            mosaic, transform = merge(
                datasets, bounds=snapped, res=(min(xres), _PIXEL), nodata=NODATA,
                dtype="float32", resampling=Resampling.nearest)
        finally:
            for d in datasets:
                d.close()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
            out_path, "w", driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], count=1,
            dtype="float32", crs="EPSG:4326", transform=transform, nodata=NODATA,
            compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256) as dst:
        dst.write(mosaic[0].astype(np.float32), 1)
    return out_path
