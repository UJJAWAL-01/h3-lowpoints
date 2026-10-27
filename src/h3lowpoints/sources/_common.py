"""Shared parts of the data readers: boxes, tiles, existence checks, mosaics and output files.

The readers use only the standard library and rasterio, so a plain install can fetch data.
"""
import contextlib
import http.client
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge

USER_AGENT = "h3-lowpoints (https://github.com/UJJAWAL-01/h3-lowpoints)"


def check_bbox(bbox):
    """(west, south, east, north) as floats, or ValueError with a plain explanation."""
    try:
        west, south, east, north = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise ValueError("bbox must be four numbers: (west, south, east, north) in degrees") from None
    if not (west < east and south < north):
        raise ValueError("bbox must be (west, south, east, north) with west < east and south < north;"
                         " a box crossing the antimeridian must be split in two")
    if west < -180 or east > 180 or south < -90 or north > 90:
        raise ValueError("bbox is outside the valid range (lon -180..180, lat -90..90)")
    return west, south, east, north


def degree_tiles(bbox, size=1):
    """South-west corners (lat, lon) of every `size`-degree tile a box touches, on a grid of `size`."""
    west, south, east, north = check_bbox(bbox)
    lon0 = math.floor(west / size) * size
    lat0 = math.floor(south / size) * size
    lon1 = math.ceil(east / size) * size - size
    lat1 = math.ceil(north / size) * size - size
    return [(lat, lon) for lat in range(int(lat0), int(lat1) + 1, size)
            for lon in range(int(lon0), int(lon1) + 1, size)]


def bbox_area_km2(bbox):
    """Approximate area of a lon/lat box in square kilometres."""
    west, south, east, north = check_bbox(bbox)
    mid = math.radians((south + north) / 2)
    return (east - west) * 111.32 * math.cos(mid) * (north - south) * 110.57


# Requests that fail for a passing reason (a dropped or reset connection, a timeout, a busy
# server) are tried again after these waits, about 4 minutes in all: one dropped connection in
# the thousands of requests a national build makes must not stop it. A 404 is never retried.
RETRY_DELAYS = (1, 2, 4, 8, 16, 32, 64, 128)
_sleep = time.sleep


def _passing(exc):
    """Is this failure worth trying again?"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429, 500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError,
                            http.client.HTTPException))


def with_retries(call):
    """call(), tried again after each of RETRY_DELAYS when it fails for a passing reason."""
    for delay in (*RETRY_DELAYS, None):
        try:
            return call()
        except Exception as exc:
            if delay is None or not _passing(exc):
                raise
            _sleep(delay)


def url_exists(url, timeout=30):
    """True if the server has the file, False if it answers 404. Any other failure raises.

    Only a 404 means "no such tile" (for example open ocean). A network error, a timeout or any
    other status is raised (after the retries of `with_retries`), so a broken connection is never
    mistaken for missing data.
    """
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})

    def head():
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    try:
        return with_retries(head)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise OSError(f"{url} answered HTTP {exc.code}") from exc


def read_bytes(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    def get():
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    return with_retries(get)


def read_text(url, timeout=60):
    return read_bytes(url, timeout=timeout).decode("utf-8", errors="replace")


def remote(url):
    """A rasterio path that reads a remote file by ranges (only the parts needed)."""
    return url if not url.startswith(("http://", "https://")) else "/vsicurl/" + url


# GDAL's block cache defaults to 5% of the machine's memory (over 1 GB on many laptops); 256 MB
# holds every block a read needs. rasterio passes GDAL_CACHEMAX to GDAL in bytes: given as 256 it
# meant 256 bytes, so blocks were fetched again and again (a soil read took 15 s instead of 2 s).
GDAL_CACHE_MB = 256
# A failed remote read is retried with a delay that starts at 1 s and at least doubles: ten retries ride out
# at least 17 minutes of lost connection, which matters in a streamed run of many hours. Missing files
# (HTTP 404) are not retried.
HTTP_RETRIES = 10


@contextlib.contextmanager
def gdal_env():
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY=str(HTTP_RETRIES),
                      GDAL_HTTP_RETRY_DELAY="1", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                      GDAL_HTTP_USERAGENT=USER_AGENT, GDAL_CACHEMAX=GDAL_CACHE_MB * 1024 * 1024):
        yield


def snap_outwards(bbox, pixel):
    """Grow a box to whole pixels of a grid anchored at 0 (tolerant of float noise)."""
    west, south, east, north = bbox
    eps = 1e-6
    return (math.floor(west / pixel + eps) * pixel, math.floor(south / pixel + eps) * pixel,
            math.ceil(east / pixel - eps) * pixel, math.ceil(north / pixel - eps) * pixel)


def mosaic(datasets, bounds, res, nodata, dtype, resampling=Resampling.nearest):
    """Merge open datasets over `bounds`; earlier datasets win where they overlap."""
    array, transform = merge(datasets, bounds=bounds, res=res, nodata=nodata, dtype=dtype,
                             resampling=resampling, method="first")
    return array[0], transform


def write_raster(path, array, transform, crs, nodata, dtype):
    """A tiled, compressed single-band GeoTIFF; creates the folder."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    floats = np.dtype(dtype).kind == "f"
    with rasterio.open(out, "w", driver="GTiff", height=array.shape[0], width=array.shape[1],
                       count=1, dtype=dtype, crs=crs, transform=transform, nodata=nodata,
                       compress="deflate", predictor=3 if floats else 2, tiled=True,
                       blockxsize=256, blockysize=256) as dst:
        dst.write(array.astype(dtype), 1)
    return out


def download_once(url, cache_dir):
    """Local copy of `url` in `cache_dir` (downloaded once, resumable), or None if it does not exist."""
    target = Path(cache_dir) / url.rsplit("/", 1)[-1]
    if target.exists() and target.stat().st_size > 0:
        return str(target)
    if not url_exists(url):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    def fetch():                            # a retry starts the file again from the beginning
        with urllib.request.urlopen(request, timeout=120) as response, open(partial, "wb") as fh:
            while chunk := response.read(1 << 20):
                fh.write(chunk)
    with_retries(fetch)
    partial.replace(target)                 # only a complete file gets the real name
    return str(target)
