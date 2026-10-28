"""Land cover, an input to the planned waterlogging layer: how much rain runs off the surface.

    from h3lowpoints.sources.landcover import fetch_worldcover, fetch_impervious
    fetch_worldcover((-97.8, 30.2, -97.7, 30.3), "cover.tif")       # global, 10 m classes
    fetch_impervious((-97.8, 30.2, -97.7, 30.3), "imperv.tif")      # US, 30 m, percent
    fetch_land_cover((-97.8, 30.2, -97.7, 30.3), "classes.tif")     # US, 30 m, NLCD classes

- ESA WorldCover 2021 is global, 10 m, in 3 x 3 degree tiles named by their south-west corner,
  with 11 classes (WORLDCOVER_CLASSES). Licence CC BY 4.0: credit "ESA WorldCover project 2021".
- NLCD (US Geological Survey) is the conterminous US at 30 m: land cover classes (NLCD_CLASSES)
  and percent impervious. By default it is Annual NLCD Collection 1.1 (one map for every year
  from 1985 to 2024; default 2024), read by ranges from cloud-optimized mosaics that Source
  Cooperative copies unchanged from the USGS bucket (s3://usgs-landcover, which charges whoever
  downloads). `source="mrlc"` asks the MRLC web service for the older NLCD editions (NLCD_YEARS,
  default 2021) instead. Both are on the same Albers grid (30 m pixels whose edges sit 15 m off
  multiples of 30 m); Annual NLCD names its datum WGS 84, the older editions NAD83, and PROJ
  converts between the two without a shift.
- NLCD impervious surface is the percentage of each 30 m pixel that is paved or built, for
  the conterminous US, in the Albers equal-area projection. Public domain. The service labels 0
  as "no data", but 0 means "0% impervious" (a field or a park), so that label is dropped and
  values above 100 are treated as no data instead.
"""
import contextlib
import urllib.error
import urllib.parse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from . import _common
from ._common import (bbox_area_km2, check_bbox, degree_tiles, gdal_env, mosaic, read_bytes,
                      remote, snap_outwards, url_exists, write_raster)

HTML_RETRY_DELAYS = (5, 15, 45, 120)       # seconds before asking again after an HTML error page

WORLDCOVER = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
WORLDCOVER_CLASSES = {
    10: "tree cover", 20: "shrubland", 30: "grassland", 40: "cropland", 50: "built-up",
    60: "bare or sparse vegetation", 70: "snow and ice", 80: "permanent water bodies",
    90: "herbaceous wetland", 95: "mangroves", 100: "moss and lichen",
}
MRLC_WCS = "https://www.mrlc.gov/geoserver/mrlc_download/wcs"
NLCD_CLASSES = {
    11: "open water", 12: "perennial ice and snow", 21: "developed, open space",
    22: "developed, low intensity", 23: "developed, medium intensity",
    24: "developed, high intensity", 31: "barren land", 41: "deciduous forest",
    42: "evergreen forest", 43: "mixed forest", 51: "dwarf scrub", 52: "shrub and scrub",
    71: "grassland and herbaceous", 72: "sedge and herbaceous", 73: "lichens", 74: "moss",
    81: "pasture and hay", 82: "cultivated crops", 90: "woody wetlands",
    95: "emergent herbaceous wetlands",
}
NLCD_YEARS = (2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021)       # the MRLC editions
ANNUAL_NLCD = "https://data.source.coop/kylebarron/usgs-landcover/annual-nlcd/c1/v1/cu/mosaic"
ANNUAL_NLCD_YEARS = range(1985, 2025)
SOURCES = {"annual": 2024, "mrlc": 2021}          # where NLCD is read from, and its default year
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
    return _nlcd_request(bbox, year, "Impervious")


def land_cover_request(bbox, year=2021):
    """The MRLC WCS URL that returns NLCD land cover classes for a lon/lat box."""
    return _nlcd_request(bbox, year, "Land_Cover")


def annual_nlcd_url(product, year=2024):
    """The Annual NLCD CONUS mosaic of `product` ("LndCov" classes, "FctImp" percent impervious)."""
    if year not in ANNUAL_NLCD_YEARS:
        raise ValueError(f"Annual NLCD exists for {ANNUAL_NLCD_YEARS[0]} to {ANNUAL_NLCD_YEARS[-1]}; got {year}")
    return f"{ANNUAL_NLCD}/Annual_NLCD_{product}_{year}_CU_C1V1.tif"


def _nlcd_request(bbox, year, product):
    if year not in NLCD_YEARS:
        raise ValueError(f"NLCD exists for {', '.join(map(str, NLCD_YEARS))}; got {year}")
    left, bottom, right, top = transform_bounds("EPSG:4326", "EPSG:5070", *check_bbox(bbox),
                                                densify_pts=21)
    params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
              ("coverageId", f"mrlc_download__NLCD_{year}_{product}_L48"),
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


def fetch_impervious(bbox, out_path, *, year=None, source="annual", max_area_km2=20_000.0, data=None):
    """Write NLCD percent impervious (0-100, 255 = no data) for a lon/lat box to a GeoTIFF.

    Conterminous US only, at 30 m in Conus Albers. `source` is "annual" (Annual NLCD, default year
    2024) or "mrlc" (the MRLC service, default 2021); with "mrlc", `data` can supply the service's
    GeoTIFF bytes (for tests or a cache). Returns the path.
    """
    array, transform, crs = _nlcd(bbox, "FctImp", "Impervious", year, source, max_area_km2, data)
    array = clean_impervious(array)
    if not np.any(array != IMPERVIOUS_NODATA):
        raise ValueError("NLCD impervious holds no data for this box (outside the lower 48 US?)")
    return write_raster(out_path, array, transform, crs, IMPERVIOUS_NODATA, "uint8")


def fetch_land_cover(bbox, out_path, *, year=None, source="annual", max_area_km2=20_000.0, data=None):
    """Write NLCD land cover classes (NLCD_CLASSES; 0 = no data) for a lon/lat box to a GeoTIFF.

    Conterminous US only, at 30 m in Conus Albers. `source` is "annual" (Annual NLCD, default year
    2024) or "mrlc" (the MRLC service, default 2021); with "mrlc", `data` can supply the service's
    GeoTIFF bytes (for tests or a cache). Returns the path.
    """
    array, transform, crs = _nlcd(bbox, "LndCov", "Land_Cover", year, source, max_area_km2, data)
    array = np.where(np.isin(array, list(NLCD_CLASSES)), array, 0).astype("uint8")
    if not np.any(array):
        raise ValueError("NLCD land cover holds no data for this box (outside the lower 48 US?)")
    return write_raster(out_path, array, transform, crs, 0, "uint8")


def _nlcd(bbox, annual_product, mrlc_product, year, source, max_area_km2, data):
    """(array, transform, crs) of NLCD for a lon/lat box, from Annual NLCD or the MRLC service."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}; got {source!r}")
    year = SOURCES[source] if year is None else year
    bbox = check_bbox(bbox)
    area = bbox_area_km2(bbox)
    if area > max_area_km2:
        raise ValueError(f"bbox is about {area:,.0f} km2 (limit {max_area_km2:,.0f}); fetch it in pieces")
    if source == "annual":
        return _annual_window(bbox, annual_nlcd_url(annual_product, year))
    url = _nlcd_request(bbox, year, mrlc_product)
    if data is None:
        # Outside its coverage the service answers with a GeoTIFF of no data or an XML exception
        # (HTTP 404). An HTML page is its web server failing for a moment (seen in October 2026
        # under eight parallel requests): asked again later, it sends the GeoTIFF.
        for wait in (*HTML_RETRY_DELAYS, None):
            try:
                data = read_bytes(url)
            except urllib.error.HTTPError as exc:
                raise ValueError(f"The NLCD service refused this box (HTTP {exc.code}). NLCD covers the "
                                 "conterminous US only; elsewhere use fetch_worldcover") from exc
            if not data.lstrip()[:20].lower().startswith((b"<!doctype html", b"<html")):
                break
            if wait is None:
                raise OSError(f"The NLCD service kept answering with an error page: {data[:200]!r}")
            _common._sleep(wait)
    if not data.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        text = data[:300].decode("utf-8", errors="replace")
        raise ValueError(f"The NLCD service did not return a GeoTIFF (outside the lower 48 US?): {text}")
    with MemoryFile(data) as mem, mem.open() as src:
        return src.read(1), src.transform, src.crs


def _annual_window(bbox, url):
    """The whole pixels of an Annual NLCD mosaic that cover a lon/lat box (no resampling)."""
    eps = 1e-6
    with gdal_env(), rasterio.open(remote(url)) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
        inv = ~src.transform
        col0, row0 = inv @ (left, top)
        col1, row1 = inv @ (right, bottom)
        col0, row0 = int(np.floor(col0 + eps)), int(np.floor(row0 + eps))
        col1, row1 = int(np.ceil(col1 - eps)), int(np.ceil(row1 - eps))
        window = Window(col0, row0, col1 - col0, row1 - row0)
        # outside the mosaic (beyond the lower 48) the window is filled with the mosaic's no data
        array = src.read(1, window=window, boundless=True, fill_value=src.nodata)
        return array, src.window_transform(window), src.crs


# ------------------------------------------------------------------ water mask

WATER_CLASS = 80          # WorldCover "permanent water bodies"
SAMPLES = 3               # each elevation pixel is sampled at SAMPLES x SAMPLES evenly spaced points
# how a result's meta.json describes the mask
WATER_RULE = ("ESA WorldCover 2021 v200: a pixel is water when at least 5 of 9 evenly spaced points "
              "in it are class 80 (permanent water bodies), or when at least 5 fall where WorldCover "
              "has no data and the pixel's elevation is at or below 0 m (open sea)")


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


def _majority(samples, value):
    """Per pixel: are at least 5 of its 3 x 3 samples equal to `value`?"""
    h, w = samples.shape[0] // SAMPLES, samples.shape[1] // SAMPLES
    hits = (samples == value).reshape(h, SAMPLES, w, SAMPLES).sum(axis=(1, 3))
    return hits * 2 > SAMPLES * SAMPLES


def _water(samples, sea=None):
    """1 where at least 5 of a pixel's 3 x 3 samples are permanent water, or where `sea` is
    True (open sea, see `build_water_mask`)."""
    water = _majority(samples, WATER_CLASS)
    if sea is not None:
        water |= sea
    return water.astype("uint8")


def _sample_geographic(wc, dem_transform, r0, r1, c0, c1):
    """WorldCover classes at the 3 x 3 sample points of pixels r0:r1, c0:c1 of a lon/lat grid.

    Each point is looked up in the WorldCover pixel that contains it (WorldCover is WGS 84 and
    the grid is treated as WGS 84 too: datum differences are about a metre, far below a pixel).
    """
    from rasterio.windows import Window

    k = (np.arange(SAMPLES) + 0.5) / SAMPLES
    xs = dem_transform.c + (np.arange(c0, c1)[:, None] + k).ravel() * dem_transform.a
    ys = dem_transform.f + (np.arange(r0, r1)[:, None] + k).ravel() * dem_transform.e
    t = wc.transform
    cols = np.floor((xs - t.c) / t.a).astype(np.int64)
    rows = np.floor((ys - t.f) / t.e).astype(np.int64)
    rmin, cmin = int(rows.min()), int(cols.min())
    window = Window(cmin, rmin, int(cols.max()) - cmin + 1, int(rows.max()) - rmin + 1)
    block = wc.read(1, window=window, boundless=True, fill_value=0)
    return block[np.ix_(rows - rmin, cols - cmin)]


def _sample_warped(samples, r0, r1, c0, c1):
    """The same for any CRS, through a WarpedVRT on a grid three times finer than the DEM."""
    from rasterio.windows import Window

    n = SAMPLES
    return samples.read(1, window=Window(c0 * n, r0 * n, (c1 - c0) * n, (r1 - r0) * n))


def build_water_mask(dem_path, out_path, *, source=None, cache_dir=None, tile=4096, hours=None,
                     verbose=False):
    """A water mask on exactly the pixel grid of an elevation raster, from ESA WorldCover.

    A pixel is water (1) when at least 5 of SAMPLES x SAMPLES = 9 evenly spaced points inside it
    fall on WorldCover "permanent water bodies", or when it is open sea: at least 5 of its points
    fall where WorldCover has no data (WorldCover maps land and the water near it; beyond lies
    the open sea) and its own elevation is at or below 0 m. Elevation models such as USGS 3DEP
    store that sea as flat ground at 0 m, which would otherwise be analysed as land. The height
    condition keeps any gap in WorldCover over dry land from being masked; heights are read only
    where WorldCover has no data, so inland the mask costs no extra reading. Otherwise 0. Pass the result as `mask_path` to
    `analyze_tiled` so open water acts as a place water leaves instead of as terrain.

    out_path ending in `.tif`: one GeoTIFF, written under a temporary name and renamed when
        complete. Suits areas up to a few hundred million pixels.
    out_path ending in `.vrt`: built tile by tile (`tile` pixels square), one small GeoTIFF per
        tile that holds water (in the folder `<name>_tiles`); the `.vrt` that joins them is
        written last. Tiles already built are kept, so an interrupted build continues where it
        stopped, and `hours` stops it at the end of a tile by raising `Paused`. For regions.
    Elevation rasters in longitude/latitude are sampled directly (fast); other CRSs through a
    warped view of WorldCover. Returns the path.
    """
    import os
    import time
    from xml.sax.saxutils import escape

    from affine import Affine
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window

    from ..store import Paused

    out = Path(out_path)
    if out.suffix.lower() not in (".tif", ".vrt"):
        raise ValueError("out_path must end in .tif (one file) or .vrt (built tile by tile)")
    out.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if hours is None else time.monotonic() + hours * 3600
    n = SAMPLES
    with gdal_env(), rasterio.open(dem_path) as dem:
        west, south, east, north = transform_bounds(dem.crs, "EPSG:4326", *dem.bounds, densify_pts=21)
        pad = 0.01
        bbox = (max(-180.0, west - pad), max(-90.0, south - pad), min(180.0, east + pad),
                min(90.0, north + pad))
        vrt = worldcover_vrt(bbox, out.with_name(out.stem + ".worldcover.vrt"), source=source,
                             cache_dir=cache_dir)
        profile = {"driver": "GTiff", "count": 1, "dtype": "uint8", "crs": dem.crs, "tiled": True,
                   "blockxsize": 512, "blockysize": 512, "compress": "deflate", "BIGTIFF": "IF_SAFER"}
        boxes = [(r0, min(r0 + tile, dem.height), c0, min(c0 + tile, dem.width))
                 for r0 in range(0, dem.height, tile) for c0 in range(0, dem.width, tile)]
        warp = None if dem.crs.is_geographic else dict(
            crs=dem.crs, transform=dem.transform @ Affine.scale(1 / n), width=dem.width * n,
            height=dem.height * n, resampling=Resampling.nearest, nodata=0)
        with rasterio.open(vrt) as wc, \
                (WarpedVRT(wc, **warp) if warp else contextlib.nullcontext(None)) as warped:
            def water(r0, r1, c0, c1):                 # in strips of rows, to bound memory
                strips = []
                for r in range(r0, r1, 1024):
                    s = min(r + 1024, r1)
                    samples = (_sample_geographic(wc, dem.transform, r, s, c0, c1) if warped is None
                               else _sample_warped(warped, r, s, c0, c1))
                    outside = _majority(samples, 0)
                    sea = None
                    if outside.any():                    # open sea: off WorldCover, at or below 0 m
                        heights = dem.read(1, window=Window(c0, r, c1 - c0, s - r)).astype("float64")
                        valid = np.isfinite(heights)
                        if dem.nodata is not None:
                            valid &= heights != dem.nodata
                        sea = outside & valid & (heights <= 0)
                    strips.append(_water(samples, sea))
                return np.vstack(strips)

            if out.suffix.lower() == ".tif":
                tmp = out.with_name(out.stem + ".part.tif")
                with rasterio.open(tmp, "w", height=dem.height, width=dem.width,
                                   transform=dem.transform, **profile) as dst:
                    for i, (r0, r1, c0, c1) in enumerate(boxes, 1):
                        dst.write(water(r0, r1, c0, c1), 1, window=Window(c0, r0, c1 - c0, r1 - r0))
                        if verbose:
                            print(f"water mask: tile {i}/{len(boxes)}", flush=True)
                os.replace(tmp, out)
                return out

            folder = out.with_name(out.stem + "_tiles")
            folder.mkdir(exist_ok=True)
            done = 0
            for i, (r0, r1, c0, c1) in enumerate(boxes, 1):
                path, none = folder / f"w_{i:06d}.tif", folder / f"w_{i:06d}.none"
                if path.exists() or none.exists():
                    done += 1
                    continue
                arr = water(r0, r1, c0, c1)
                if arr.any():
                    tmp = folder / f"tmp_w_{i:06d}.tif"
                    window = Window(c0, r0, c1 - c0, r1 - r0)
                    with rasterio.open(tmp, "w", height=r1 - r0, width=c1 - c0,
                                       transform=dem.window_transform(window), **profile) as dst:
                        dst.write(arr, 1)
                    os.replace(tmp, path)
                else:
                    none.touch()                     # no water here: nothing to store
                done += 1
                if verbose:
                    print(f"water mask: tile {i}/{len(boxes)}", flush=True)
                if deadline is not None and time.monotonic() > deadline and done < len(boxes):
                    raise Paused(f"stopped at the time limit while building the water mask: {done} of "
                                 f"{len(boxes)} tiles done. Run the same command again to continue.",
                                 done, len(boxes))
        sources = []
        for i, (r0, r1, c0, c1) in enumerate(boxes, 1):
            path = folder / f"w_{i:06d}.tif"
            if path.exists():
                sources.append(
                    f'    <SimpleSource><SourceFilename relativeToVRT="1">{escape(folder.name)}/{path.name}'
                    f'</SourceFilename><SourceBand>1</SourceBand>'
                    f'<SrcRect xOff="0" yOff="0" xSize="{c1 - c0}" ySize="{r1 - r0}"/>'
                    f'<DstRect xOff="{c0}" yOff="{r0}" xSize="{c1 - c0}" ySize="{r1 - r0}"/></SimpleSource>')
        g = dem.transform
        text = (f'<VRTDataset rasterXSize="{dem.width}" rasterYSize="{dem.height}">\n'
                f'  <SRS>{escape(dem.crs.to_wkt())}</SRS>\n'
                f'  <GeoTransform>{g.c!r}, {g.a!r}, {g.b!r}, {g.f!r}, {g.d!r}, {g.e!r}</GeoTransform>\n'
                f'  <VRTRasterBand dataType="Byte" band="1">\n' + "\n".join(sources) +
                '\n  </VRTRasterBand>\n</VRTDataset>\n')
    tmp = out.with_name(out.stem + ".part.vrt")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)                             # last: its presence means the mask is whole
    return out
