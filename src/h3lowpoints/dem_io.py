import rasterio
from pyproj import CRS
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds


def utm_crs_for(bounds, crs):
    """The WGS 84 UTM zone covering the centre of `bounds` (given in `crs`)."""
    west, south, east, north = transform_bounds(crs, 4326, *bounds)
    info = query_utm_crs_info(
        datum_name="WGS 84",
        area_of_interest=AreaOfInterest((west + east) / 2, (south + north) / 2,
                                        (west + east) / 2, (south + north) / 2))
    if not info:
        raise ValueError("Could not find a UTM zone for this DEM")
    return CRS.from_epsg(int(info[0].code))


def reproject_to_utm(src_path, dst_path, resolution=None):
    """Write a copy of a DEM in the matching WGS 84 UTM zone (metres), ready for `analyze`.

    Use this for DEMs in geographic coordinates, such as Copernicus GLO-30. `resolution` is the
    output cell size in metres; by default it is chosen to match the input. Bilinear resampling
    smooths the surface slightly, so prefer a DEM that is already projected when you have one.
    """
    with rasterio.open(src_path) as src:
        dst_crs = utm_crs_for(src.bounds, src.crs)
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds, resolution=resolution)
        profile = src.profile.copy()
        profile.update(crs=dst_crs, transform=transform, width=width, height=height,
                       compress="deflate")
        with rasterio.open(dst_path, "w", **profile) as dst:
            reproject(rasterio.band(src, 1), rasterio.band(dst, 1), src_transform=src.transform,
                      src_crs=src.crs, dst_transform=transform, dst_crs=dst_crs,
                      resampling=Resampling.bilinear, src_nodata=src.nodata,
                      dst_nodata=src.nodata)
    return dst_path
