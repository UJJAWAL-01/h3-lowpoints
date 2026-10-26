"""Soil hydrologic groups for the conterminous US, from the USDA soil survey (gNATSGO).

    from h3lowpoints.sources.soils import fetch_soil_groups
    fetch_soil_groups((-95.6, 29.6, -95.2, 29.9), "soil.tif", cache_dir="soils")

A soil's hydrologic group says how easily rain soaks into it when wet: A (sand, gravel; soaks in
fast), B, C, D (clay, shallow soil over rock, a high water table; sheds most rain). Dual groups
(A/D, B/D, C/D) are wet soils: the first letter if artificially drained, D if not.

The groups come from the gridded National Soil Survey Geographic Database (gNATSGO, USDA NRCS):
a 10 m grid of soil map units (`mukey`) for the lower 48 states, and its map-unit table
(`muaggatt`), whose `hydgrpdcd` gives each unit's group (dominant condition). The library reads
the July 2020 edition published on Microsoft's Planetary Computer (CC0, no account: a read token
is issued anonymously by its public API). The grid is read by parts over the internet, from its
nearest overview not coarser than the resolution asked for.

Output codes (GROUPS): 1 A, 2 B, 3 C, 4 D, 5 A/D, 6 B/D, 7 C/D; 8 a map unit with no group
(urban land, water, rock outcrop and the like); 0 no data (outside the survey).
"""
import json
import math
import time
import urllib.parse

import numpy as np

from ._common import check_bbox, read_bytes, write_raster

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/soils/gnatsgo"
TABLE = "https://soils.blob.core.windows.net/gnatsgo/july2020/parquet/muaggatt.parquet/part.0.parquet"
COLLECTION = "gnatsgo-rasters"
GROUPS = {1: "A", 2: "B", 3: "C", 4: "D", 5: "A/D", 6: "B/D", 7: "C/D", 8: "none"}
CODES = {v: k for k, v in GROUPS.items()}
MUKEY_NODATA = 2147483647
NLCD_OFFSET = 15.0          # NLCD's 30 m pixel corners lie 15 m past multiples of 30 m
EDITION = "gNATSGO July 2020 (USDA NRCS), via Microsoft Planetary Computer"
_token = {"value": None, "until": 0.0}


def read_token(get=read_bytes):
    """A read token for the soil storage, reused until shortly before it expires."""
    if _token["value"] is None or time.time() > _token["until"]:
        answer = json.loads(get(TOKEN))
        _token["value"] = answer["token"]
        _token["until"] = time.time() + 30 * 60          # tokens last about an hour
    return _token["value"]


def group_table(cache_dir, get=read_bytes):
    """(sorted map-unit keys, their group codes) from the gNATSGO map-unit table.

    The table (12.5 MB) is downloaded once into `cache_dir`.
    """
    import pyarrow.parquet as pq

    from pathlib import Path

    local = Path(cache_dir) / "gnatsgo_muaggatt.parquet"
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(local.name + ".part")
        part.write_bytes(get(f"{TABLE}?{read_token(get)}"))
        part.replace(local)
    table = pq.read_table(local, columns=["mukey", "hydgrpdcd"]).to_pandas()
    table = table.drop_duplicates("mukey").sort_values("mukey")
    codes = table["hydgrpdcd"].astype(object).map(CODES).fillna(CODES["none"]).astype(np.uint8)
    return table["mukey"].to_numpy(dtype=np.int64), codes.to_numpy()


def tiles(bbox, get=read_bytes):
    """URLs of the gNATSGO map-unit tiles intersecting a lon/lat box."""
    params = {"collections": COLLECTION, "bbox": ",".join(str(v) for v in check_bbox(bbox)),
              "limit": 100}
    answer = json.loads(get(f"{STAC}?{urllib.parse.urlencode(params)}"))
    return [f["assets"]["mukey"]["href"] for f in answer.get("features", [])]


def to_groups(mukeys, keys, codes):
    """Group codes of map units (0 where the key is no data or unknown)."""
    mukeys = np.asarray(mukeys, dtype=np.int64)
    at = np.minimum(np.searchsorted(keys, mukeys), len(keys) - 1)
    found = (keys[at] == mukeys) & (mukeys != MUKEY_NODATA)
    return np.where(found, codes[at], 0).astype(np.uint8)


def fetch_soil_groups(bbox, out_path, *, cache_dir, like=None, resolution=30.0, get=read_bytes,
                      opener=None):
    """Write soil hydrologic group codes (GROUPS) for a lon/lat box to a GeoTIFF.

    By default the grid is NAD83 / Conus Albers (EPSG:5070) at `resolution` metres covering the
    box, with pixel edges where NLCD's are (15 m past multiples of 30 m, for 30 m). To combine
    the groups with an NLCD raster pixel for pixel, pass it as `like`: its grid (extent, size,
    CRS) is then used exactly, and `bbox` may be None (the NLCD service chooses its own edge
    pixels, so the same box does not give the same extent).
    Each pixel takes the map unit at its centre, read from the soil grid's nearest overview not
    coarser than the output's pixels. `opener(url)` opens a tile (for tests). Returns the path.
    """
    import rasterio
    from affine import Affine
    from rasterio.errors import WindowError
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    from ._common import gdal_env

    if like is not None:
        with rasterio.open(like) as ref:
            crs, transform, (height, width) = ref.crs, ref.transform, ref.shape
            resolution = abs(transform.a)
            bounds = ref.bounds
        bbox = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    else:
        bbox = check_bbox(bbox)
        crs = "EPSG:5070"
        left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
        offset = NLCD_OFFSET if resolution == 30.0 else 0.0
        left = math.floor((left - offset) / resolution) * resolution + offset
        top = math.ceil((top - offset) / resolution) * resolution + offset
        width = int(math.ceil((right - left) / resolution))
        height = int(math.ceil((top - bottom) / resolution))
        transform = from_origin(left, top, resolution, resolution)
    out_bounds = rasterio.transform.array_bounds(height, width, transform)
    keys, codes = group_table(cache_dir, get)
    mukey = np.full((height, width), MUKEY_NODATA, dtype=np.int64)
    found = tiles(bbox, get)
    if not found:
        raise ValueError("no gNATSGO soil tiles cover this box (it covers the lower 48 US states)")
    with gdal_env():
        for url in found:
            path = opener(url) if opener else f"/vsicurl/{url}?{read_token(get)}"
            with rasterio.open(path) as src:
                # the tile's part of the output, read at about the output's resolution: rasterio
                # then reads the overview that is not coarser (the full 10 m grid is not fetched)
                b = transform_bounds(crs, src.crs, out_bounds[0], out_bounds[1], out_bounds[2],
                                     out_bounds[3], densify_pts=21)
                try:
                    win = from_bounds(*b, src.transform).intersection(
                        Window(0, 0, src.width, src.height))
                except WindowError:                      # the tile only touches the box
                    continue
                if win.width < 1 or win.height < 1:
                    continue
                step = max(1.0, resolution / src.res[0])
                shape = (max(1, int(round(win.height / step))), max(1, int(round(win.width / step))))
                data = src.read(1, window=win, out_shape=shape, resampling=Resampling.nearest)
                wt = src.window_transform(win) @ Affine.scale(win.width / shape[1],
                                                              win.height / shape[0])
                part = np.full((height, width), MUKEY_NODATA, dtype=np.int64)
                reproject(data.astype(np.int64), part, src_transform=wt, src_crs=src.crs,
                          src_nodata=MUKEY_NODATA, dst_transform=transform, dst_crs=crs,
                          dst_nodata=MUKEY_NODATA, resampling=Resampling.nearest)
            take = (mukey == MUKEY_NODATA) & (part != MUKEY_NODATA)
            mukey[take] = part[take]
    groups = to_groups(mukey, keys, codes)
    if not groups.any():
        raise ValueError("the soil survey holds no data for this box")
    return write_raster(out_path, groups, transform, crs, 0, "uint8")
