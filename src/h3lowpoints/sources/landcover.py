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


# ------------------------------------------------------------------ water mask

WATER_CLASS = 80          # WorldCover "permanent water bodies"
SAMPLES = 3               # each elevation pixel is sampled at SAMPLES x SAMPLES evenly spaced points


def worldcover_vrt(bbox, path, *, source=None, cache_dir=None, workers=8):
    """A virtual raster of the WorldCover tiles over a lon/lat box (any size).

    Tiles that do not exist (open ocean) read as 0, no data. `cache_dir` downloads each tile
    once instead of reading it over the internet. Returns the path.
    """
    from concurrent.futures import ThreadPoolExecutor
    from xml.sax.saxutils import escape

    from ._common import download_once

    bbox = check_bbox(bbox)
    tiles = degree_tiles(bbox, size=3)
    west, south = min(lon for _, lon in tiles), min(lat for lat, _ in tiles)
    east, north = max(lon for _, lon in tiles) + 3, max(lat for lat, _ in tiles) + 3
    pixel = 1 / 12000

    def probe(tile):
        lat, lon = tile
        if cache_dir is not None and source is None:
            url = f"{WORLDCOVER}/{worldcover_tile_name(lat, lon)}.tif"
            loc = download_once(url, cache_dir)
        else:
            loc = _locate_worldcover(lat, lon, source)
        if loc is None:
            return None
        with rasterio.open(loc) as src:
            return loc, src.transform, src.width, src.height

    with gdal_env(), ThreadPoolExecutor(max_workers=workers) as pool:
        found = [info for info in pool.map(probe, tiles) if info]
    if not found:
        raise ValueError("No WorldCover tiles cover this box")
    sources = []
    for loc, tr, w, h in found:
        dx, dy = round((tr.c - west) / pixel), round((north - tr.f) / pixel)
        sources.append(
            f'    <SimpleSource><SourceFilename relativeToVRT="0">{escape(loc)}</SourceFilename>'
            f'<SourceBand>1</SourceBand><SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>'
            f'<DstRect xOff="{dx}" yOff="{dy}" xSize="{w}" ySize="{h}"/></SimpleSource>')
    width, height = round((east - west) / pixel), round((north - south) / pixel)
    wkt = rasterio.crs.CRS.from_epsg(4326).to_wkt()
    text = (f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
            f'  <SRS dataAxisToSRSAxisMapping="2,1">{escape(wkt)}</SRS>\n'
            f'  <GeoTransform>{float(west)!r}, {pixel!r}, 0, {float(north)!r}, 0, {-pixel!r}</GeoTransform>\n'
            f'  <VRTRasterBand dataType="Byte" band="1">\n    <NoDataValue>0</NoDataValue>\n'
            + "\n".join(sources) + '\n  </VRTRasterBand>\n</VRTDataset>\n')
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def build_water_mask(dem_path, out_path, *, source=None, cache_dir=None, block=2048, verbose=False):
    """A water mask on exactly the pixel grid of an elevation raster, from ESA WorldCover.

    A pixel is water (1) when at least 5 of SAMPLES x SAMPLES = 9 evenly spaced points inside it
    fall on WorldCover "permanent water bodies"; otherwise 0. Pass the result as `mask_path` to
    `analyze_tiled` so open water acts as a place water leaves instead of as terrain. Works for
    elevation rasters in any CRS. The file is written under a temporary name and renamed when
    complete, so an existing `out_path` is always a whole mask. Returns the path.
    """
    import os

    from affine import Affine
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.tif")
    n = SAMPLES
    with gdal_env(), rasterio.open(dem_path) as dem:
        west, south, east, north = transform_bounds(dem.crs, "EPSG:4326", *dem.bounds, densify_pts=21)
        pad = 0.01
        bbox = (max(-180.0, west - pad), max(-90.0, south - pad), min(180.0, east + pad),
                min(90.0, north + pad))
        vrt = worldcover_vrt(bbox, out.with_name(out.stem + ".worldcover.vrt"), source=source,
                             cache_dir=cache_dir)
        fine = dem.transform @ Affine.scale(1 / n)
        profile = {"driver": "GTiff", "height": dem.height, "width": dem.width, "count": 1,
                   "dtype": "uint8", "crs": dem.crs, "transform": dem.transform, "tiled": True,
                   "blockxsize": 512, "blockysize": 512, "compress": "deflate", "BIGTIFF": "IF_SAFER"}
        with rasterio.open(vrt) as wc, \
                WarpedVRT(wc, crs=dem.crs, transform=fine, width=dem.width * n,
                          height=dem.height * n, resampling=Resampling.nearest, nodata=0) as samples, \
                rasterio.open(tmp, "w", **profile) as dst:
            rows = range(0, dem.height, block)
            for i, r0 in enumerate(rows, 1):
                h = min(block, dem.height - r0)
                for c0 in range(0, dem.width, block):
                    w = min(block, dem.width - c0)
                    s = samples.read(1, window=Window(c0 * n, r0 * n, w * n, h * n))
                    water = (s == WATER_CLASS).reshape(h, n, w, n).sum(axis=(1, 3))
                    dst.write((water * 2 > n * n).astype("uint8"), 1, window=Window(c0, r0, w, h))
                if verbose:
                    print(f"water mask: row {i}/{len(rows)}", flush=True)
    os.replace(tmp, out)
    return out
