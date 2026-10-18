import numpy as np
from rasterio import features
from scipy import ndimage


def rasterize_buildings(buildings, transform, shape, min_area=20.0):
    """Building footprints as a 0/1 raster on a DEM grid, or None if there are no footprints.

    `buildings` is a GeoDataFrame (or an iterable of shapely geometries) in the DEM's CRS.
    Footprints smaller than `min_area` (map units squared) are ignored; use 0 for geographic
    coordinates, where map units are degrees.
    """
    geoms = getattr(buildings, "geometry", buildings)
    shapes = [(g, 1) for g in geoms if g is not None and not g.is_empty and g.area >= min_area]
    if not shapes:
        return None
    return features.rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="uint8")


def building_overlap(labels, ids, buildings, transform, shape, min_area=20.0):
    """Fraction of each depression's pixels that lie under building footprints."""
    mask = rasterize_buildings(buildings, transform, shape, min_area)
    if mask is None:
        return np.zeros(len(ids))
    return np.asarray(ndimage.mean(mask.astype("float32"), labels, ids))
