"""USGS 3D Elevation Program (3DEP): bare-earth elevation for the United States, public domain.

    from h3lowpoints.sources.usgs import fetch_3dep, fetch_lidar
    fetch_3dep((-97.9, 30.1, -97.6, 30.4), "austin_30m.tif")          # ~30 m, seamless
    fetch_3dep((-97.9, 30.1, -97.6, 30.4), "austin_10m.tif", resolution=10)
    fetch_lidar((-97.76, 30.26, -97.73, 30.29), "downtown_1m.tif")     # 1 m lidar

Unlike Copernicus GLO-30, 3DEP is a *bare-earth* model: buildings and trees are removed, so a
depression is a feature of the ground, not of a roof or a canopy.

- The seamless products (1 and 1/3 arc-second, about 30 m and 10 m) come in 1 x 1 degree tiles
  named by their north-west corner (n31w098 covers 30-31 N, 98-97 W), with a 6-pixel overlap,
  in NAD83 geographic coordinates. Only the windows a box needs are read, over HTTP.
- 1 m lidar comes in 10 x 10 km tiles in UTM, one set per survey project. Projects overlap and
  differ in date; the newest survey wins wherever they overlap. Tiles in another UTM zone are
  reprojected into the zone of the survey covering the box's centre.
"""
import json
import math
import urllib.parse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from ._common import (bbox_area_km2, check_bbox, degree_tiles, gdal_env, mosaic, read_text,
                      remote, snap_outwards, url_exists, write_raster)

STAGED = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"
PRODUCTS = {30: ("1", 1 / 3600), 10: ("13", 1 / 10800)}      # resolution: (product code, pixel)
NODATA = -999999.0
TNM_API = "https://tnmaccess.nationalmap.gov/api/v1/products"
LIDAR_DATASET = "Digital Elevation Model (DEM) 1 meter"
LIDAR_NODATA = -9999.0


# ------------------------------------------------------------------ seamless 30 m and 10 m

def tile_name(lat, lon):
    """3DEP name of the 1-degree tile whose south-west corner is (lat, lon): named by its NW corner."""
    north, west = lat + 1, lon
    return f"{'n' if north >= 0 else 's'}{abs(north):02d}{'w' if west < 0 else 'e'}{abs(west):03d}"


def tile_url(lat, lon, resolution=30):
    code, _ = PRODUCTS[resolution]
    name = tile_name(lat, lon)
    return f"{STAGED}/{code}/TIFF/current/{name}/USGS_{code}_{name}.tif"


def _locate(lat, lon, resolution, source):
    if source is None:
        url = tile_url(lat, lon, resolution)
        return remote(url) if url_exists(url) else None
    code, _ = PRODUCTS[resolution]
    path = Path(source) / f"USGS_{code}_{tile_name(lat, lon)}.tif"
    return str(path) if path.exists() else None


def fetch_3dep(bbox, out_path, *, resolution=30, source=None, max_tiles=16):
    """Write 3DEP bare-earth elevation for `bbox` = (west, south, east, north) to a GeoTIFF.

    resolution: 30 (1 arc-second) or 10 (1/3 arc-second, nine times the pixels).
    source: None for the public USGS bucket, or a folder with tiles named like USGS_1_n31w098.tif.
    max_tiles: refuse boxes touching more 1-degree tiles than this.
    Returns the path. The raster is NAD83 geographic, float32, nodata -999999; tiles that do not
    exist (open sea) are left as nodata, which the analysis treats as an outlet.
    """
    if resolution not in PRODUCTS:
        raise ValueError(f"resolution must be 30 or 10 (metres), got {resolution!r}")
    bbox = check_bbox(bbox)
    tiles = degree_tiles(bbox)
    if len(tiles) > max_tiles:
        raise ValueError(f"bbox touches {len(tiles)} tiles (limit {max_tiles}); "
                         "raise max_tiles or fetch it in pieces")
    with gdal_env():
        found = [(t, loc) for t in tiles if (loc := _locate(*t, resolution, source))]
        if not found:
            raise ValueError(f"No 3DEP tiles cover this box (outside the US, or open sea). "
                             f"{len(tiles)} tile(s) checked")
        _, pixel = PRODUCTS[resolution]
        array, transform, crs = _own_cell_mosaic(found, snap_outwards(bbox, pixel), pixel)
    return write_raster(out_path, array, transform, crs, NODATA, "float32")


def _own_cell_mosaic(found, bounds, pixel):
    """Mosaic where every pixel comes from the tile whose own 1-degree cell holds it.

    The 6-pixel overlaps of neighbouring 3DEP tiles are produced separately and can differ by
    centimetres, so "first tile wins" would let a neighbour's overlap replace a tile's own value
    near a seam. All tiles share one pixel grid, so this is exact copying, with no resampling.
    """
    west, south, east, north = bounds
    width, height = round((east - west) / pixel), round((north - south) / pixel)
    out = np.full((height, width), NODATA, dtype="float32")
    crs = None
    for (lat, lon), loc in found:
        cw, cs = max(west, lon), max(south, lat)
        ce, cn = min(east, lon + 1), min(north, lat + 1)
        if ce <= cw or cn <= cs:
            continue
        with rasterio.open(loc) as src:
            crs = crs or src.crs
            window = src.window(cw, cs, ce, cn).round_offsets().round_lengths()
            block = src.read(1, window=window, boundless=True, fill_value=NODATA)
            if src.nodata is not None and src.nodata != NODATA:
                block[block == src.nodata] = NODATA
        row0, col0 = round((north - cn) / pixel), round((cw - west) / pixel)
        rows, cols = min(block.shape[0], height - row0), min(block.shape[1], width - col0)
        out[row0:row0 + rows, col0:col0 + cols] = block[:rows, :cols]
    return out, rasterio.transform.from_origin(west, north, pixel, pixel), crs


# ------------------------------------------------------------------ 1 m lidar

def find_lidar_tiles(bbox, page_size=200):
    """1 m lidar tiles from the USGS catalogue that touch `bbox`, newest survey first.

    Each item is a dict with at least title, publicationDate, downloadURL and boundingBox.
    """
    west, south, east, north = check_bbox(bbox)
    items, offset = [], 0
    while True:
        query = urllib.parse.urlencode({
            "datasets": LIDAR_DATASET, "bbox": f"{west},{south},{east},{north}",
            "prodFormats": "GeoTIFF", "max": page_size, "offset": offset, "outputFormat": "JSON"})
        page = json.loads(read_text(f"{TNM_API}?{query}"))
        batch = page.get("items", [])
        items.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page.get("total", 0)):
            break
    return sort_newest_first(items)


def sort_newest_first(items):
    """Newest survey first; ties broken by title so the order is reproducible."""
    return sorted(items, key=lambda it: (it.get("publicationDate") or "", it.get("title") or ""),
                  reverse=True)


def _covers(item, lon, lat):
    b = item.get("boundingBox") or {}
    return b.get("minX", 1e9) <= lon <= b.get("maxX", -1e9) and b.get("minY", 1e9) <= lat <= b.get("maxY", -1e9)


def fetch_lidar(bbox, out_path, *, items=None, max_area_km2=100.0):
    """Write 1 m lidar elevation for `bbox` = (west, south, east, north), in degrees, to a GeoTIFF.

    items: the catalogue entries to use (default: ask the USGS catalogue). Each needs title,
        publicationDate, downloadURL (a URL or a local path) and boundingBox. Useful for tests or
        to pin one survey.
    max_area_km2: refuse bigger boxes; 100 km2 is 100 million pixels (400 MB in memory).
    Returns the path. The raster is in the UTM zone of the survey covering the box's centre,
    float32, nodata -9999, on that survey's 1 m pixel grid.
    """
    bbox = check_bbox(bbox)
    area = bbox_area_km2(bbox)
    if area > max_area_km2:
        raise ValueError(f"bbox is about {area:,.0f} km2 (limit {max_area_km2:,.0f} km2 of 1 m data); "
                         "raise max_area_km2 or fetch it in pieces")
    items = sort_newest_first(items) if items is not None else find_lidar_tiles(bbox)
    if not items:
        raise ValueError("No 1 m lidar covers this box. 3DEP 1 m covers most, not all, of the US; "
                         "try fetch_3dep for 30 m or 10 m elevation")
    west, south, east, north = bbox
    centre = ((west + east) / 2, (south + north) / 2)
    with gdal_env():
        opened = [rasterio.open(remote(it["downloadURL"])) for it in items]
        warped = []
        try:
            anchor = next((d for d, it in zip(opened, items) if _covers(it, *centre)), opened[0])
            crs, res = anchor.crs, anchor.res
            for d in opened:
                if d.crs != crs:
                    warped.append(WarpedVRT(d, crs=crs, resampling=Resampling.bilinear))
            vrts = iter(warped)
            datasets = [d if d.crs == crs else next(vrts) for d in opened]
            left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
            # whole pixels of the anchor survey's grid, so its values are copied, not resampled
            x0, y0 = anchor.transform.c, anchor.transform.f
            bounds = (x0 + math.floor((left - x0) / res[0]) * res[0],
                      y0 + math.floor((bottom - y0) / res[1]) * res[1],
                      x0 + math.ceil((right - x0) / res[0]) * res[0],
                      y0 + math.ceil((top - y0) / res[1]) * res[1])
            array, transform = mosaic(datasets, bounds, res, LIDAR_NODATA, "float32")
        finally:
            for d in warped + opened:
                d.close()
    if not np.any(array != LIDAR_NODATA):
        raise ValueError("The lidar tiles found hold no data inside this box")
    return write_raster(out_path, array, transform, crs, LIDAR_NODATA, "float32")
