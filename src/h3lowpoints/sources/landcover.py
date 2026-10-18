"""Land cover, an input to the planned waterlogging layer: how much rain runs off the surface.

    from h3lowpoints.sources.landcover import fetch_worldcover, fetch_impervious
    fetch_worldcover((-97.8, 30.2, -97.7, 30.3), "cover.tif")       # global, 10 m classes
    fetch_impervious((-97.8, 30.2, -97.7, 30.3), "imperv.tif")      # US, 30 m, percent

- ESA WorldCover 2021 is global, 10 m, in 3 x 3 degree tiles named by their south-west corner,
  with 11 classes (WORLDCOVER_CLASSES). Licence CC BY 4.0: credit "ESA WorldCover project 2021".
- NLCD impervious surface (MRLC) is the percentage of each 30 m pixel that is paved or built, for
  the conterminous US, in the Albers equal-area projection. Public domain. The service labels 0
  as "no data", but 0 means "0% impervious" (a field or a park), so that label is dropped and
  values above 100 are treated as no data instead.
"""
import urllib.error
import urllib.parse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.warp import transform_bounds

from ._common import (bbox_area_km2, check_bbox, degree_tiles, gdal_env, mosaic, read_bytes,
                      remote, snap_outwards, url_exists, write_raster)

WORLDCOVER = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
WORLDCOVER_CLASSES = {
    10: "tree cover", 20: "shrubland", 30: "grassland", 40: "cropland", 50: "built-up",
    60: "bare or sparse vegetation", 70: "snow and ice", 80: "permanent water bodies",
    90: "herbaceous wetland", 95: "mangroves", 100: "moss and lichen",
}
MRLC_WCS = "https://www.mrlc.gov/geoserver/mrlc_download/wcs"
NLCD_YEARS = (2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021)
IMPERVIOUS_NODATA = 255


def worldcover_tile_name(lat, lon):
    """WorldCover name of the 3-degree tile whose south-west corner is (lat, lon)."""
    return (f"ESA_WorldCover_10m_2021_v200_{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
            f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_Map")


def _locate_worldcover(lat, lon, source):
    name = worldcover_tile_name(lat, lon)
    if source is None:
        url = f"{WORLDCOVER}/{name}.tif"
        return remote(url) if url_exists(url) else None
    path = Path(source) / f"{name}.tif"
    return str(path) if path.exists() else None


def fetch_worldcover(bbox, out_path, *, source=None, max_tiles=4):
    """Write ESA WorldCover classes for `bbox` = (west, south, east, north) to a GeoTIFF.

    Returns the path: WGS 84, uint8 class codes (see WORLDCOVER_CLASSES), 0 = no data.
    """
    bbox = check_bbox(bbox)
    tiles = degree_tiles(bbox, size=3)
    if len(tiles) > max_tiles:
        raise ValueError(f"bbox touches {len(tiles)} WorldCover tiles (limit {max_tiles}); "
                         "raise max_tiles or fetch it in pieces")
    with gdal_env():
        found = [loc for loc in (_locate_worldcover(*t, source) for t in tiles) if loc]
        if not found:
            raise ValueError("No WorldCover tiles cover this box (open sea?)")
        pixel = 1 / 12000
        datasets = [rasterio.open(loc) for loc in found]
        try:
            array, transform = mosaic(datasets, snap_outwards(bbox, pixel), (pixel, pixel), 0, "uint8")
        finally:
            for d in datasets:
                d.close()
    return write_raster(out_path, array, transform, "EPSG:4326", 0, "uint8")


def impervious_request(bbox, year=2021):
    """The MRLC WCS URL that returns NLCD impervious percent for a lon/lat box."""
    if year not in NLCD_YEARS:
        raise ValueError(f"NLCD impervious exists for {', '.join(map(str, NLCD_YEARS))}; got {year}")
    left, bottom, right, top = transform_bounds("EPSG:4326", "EPSG:5070", *check_bbox(bbox),
                                                densify_pts=21)
    params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
              ("coverageId", f"mrlc_download__NLCD_{year}_Impervious_L48"),
              ("subset", f"X({_floor30(left)},{_ceil30(right)})"),
              ("subset", f"Y({_floor30(bottom)},{_ceil30(top)})"),
              ("format", "image/geotiff")]
    return f"{MRLC_WCS}?{urllib.parse.urlencode(params)}"


def _floor30(v):
    return int(np.floor(v / 30.0) * 30)


def _ceil30(v):
    return int(np.ceil(v / 30.0) * 30)


def clean_impervious(array):
    """Percent impervious with no-data as 255: values 0..100 are kept, anything else is no data."""
    a = np.asarray(array)
    return np.where((a >= 0) & (a <= 100), a, IMPERVIOUS_NODATA).astype("uint8")


def fetch_impervious(bbox, out_path, *, year=2021, max_area_km2=20_000.0, data=None):
    """Write NLCD percent impervious (0-100, 255 = no data) for a lon/lat box to a GeoTIFF.

    Conterminous US only. `data` can supply the service's GeoTIFF bytes (for tests or a cache).
    Returns the path, in NAD83 / Conus Albers (EPSG:5070) at 30 m.
    """
    bbox = check_bbox(bbox)
    area = bbox_area_km2(bbox)
    if area > max_area_km2:
        raise ValueError(f"bbox is about {area:,.0f} km2 (limit {max_area_km2:,.0f}); fetch it in pieces")
    if data is None:
        try:
            data = read_bytes(impervious_request(bbox, year))
        except urllib.error.HTTPError as exc:
            raise ValueError(f"The NLCD service refused this box (HTTP {exc.code}). NLCD impervious "
                             "covers the conterminous US only; elsewhere use fetch_worldcover") from exc
    raw = data
    if not raw.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        text = raw[:300].decode("utf-8", errors="replace")
        raise ValueError(f"The NLCD service did not return a GeoTIFF (outside the lower 48 US?): {text}")
    with MemoryFile(raw) as mem, mem.open() as src:
        array = clean_impervious(src.read(1))
        transform, crs = src.transform, src.crs
    if not np.any(array != IMPERVIOUS_NODATA):
        raise ValueError("NLCD impervious holds no data for this box (outside the lower 48 US?)")
    return write_raster(out_path, array, transform, crs, IMPERVIOUS_NODATA, "uint8")
