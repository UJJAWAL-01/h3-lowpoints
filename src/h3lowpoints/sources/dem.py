from pathlib import Path

import rasterio
import requests
from rasterio.merge import merge

TNM = "https://tnmaccess.nationalmap.gov/api/v1/products"
DATASET = "Digital Elevation Model (DEM) 1 meter"


def find_tiles(bbox, max_items=100):
    """Query the USGS National Map for 1 m DEM tiles inside bbox (lon/lat)."""
    r = requests.get(
        TNM,
        params={
            "datasets": DATASET,
            "bbox": ",".join(map(str, bbox)),
            "prodFormats": "GeoTIFF",
            "max": max_items,
            "outputFormat": "JSON",
        },
        timeout=60,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    # newest first, so the most recent lidar wins where projects overlap
    return sorted(items, key=lambda i: i.get("publicationDate", ""), reverse=True)


def download(items, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for it in items:
        url = it["downloadURL"]
        dest = outdir / Path(url).name
        if not dest.exists():
            print("downloading", dest.name)
            with requests.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        f.write(chunk)
        paths.append(dest)
    return paths


def mosaic(paths, out_path):
    srcs = [rasterio.open(p) for p in paths]
    crs = {str(s.crs) for s in srcs}
    if len(crs) != 1:
        raise ValueError(f"Tiles use different CRS: {crs}")
    arr, transform = merge(srcs)
    meta = srcs[0].meta.copy()
    meta.update(
        driver="GTiff",
        height=arr.shape[1],
        width=arr.shape[2],
        transform=transform,
        compress="deflate",
    )
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(arr)
    for s in srcs:
        s.close()
    return out_path


def crop_mosaic(paths, out_path, bbox_lonlat):
    """Crop to bbox_lonlat (minx, miny, maxx, maxy in lon/lat) and stitch tiles on a 1 m grid."""
    import math

    import numpy as np
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window

    NOD = -9999.0
    srcs = [rasterio.open(p) for p in paths]
    crs = srcs[0].crs
    res = srcs[0].res[0]
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox_lonlat)
    left, top = math.floor(left), math.ceil(top)
    width = int(math.ceil(right) - left)
    height = int(top - math.floor(bottom))
    out = np.full((height, width), NOD, dtype="float32")

    for s in srcs:
        dc = int(round((left - s.transform.c) / res))
        dr = int(round((s.transform.f - top) / res))
        j0, j1 = max(0, -dc), min(width, s.width - dc)
        i0, i1 = max(0, -dr), min(height, s.height - dr)
        if j1 <= j0 or i1 <= i0:
            continue
        data = s.read(1, window=Window(j0 + dc, i0 + dr, j1 - j0, i1 - i0)).astype("float32")
        bad = ~np.isfinite(data) | (data < -1e30)
        if s.nodata is not None:
            bad |= data == s.nodata
        data[bad] = NOD
        region = out[i0:i1, j0:j1]
        out[i0:i1, j0:j1] = np.where(region == NOD, data, region)

    profile = dict(driver="GTiff", height=height, width=width, count=1, dtype="float32",
                   crs=crs, transform=from_origin(left, top, res, res), nodata=NOD,
                   compress="deflate")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(out, 1)
    for s in srcs:
        s.close()
    return out_path
