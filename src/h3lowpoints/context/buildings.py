import numpy as np
from rasterio import features
from scipy import ndimage


def building_overlap(labels, ids, buildings, transform, shape, min_area=20.0):
    """Fraction of each depression's area that lies under building footprints.

    `buildings` is a GeoDataFrame (or an iterable of shapely geometries) in the DEM's CRS.
    Footprints smaller than `min_area` (map units squared) are ignored.
    """
    geoms = getattr(buildings, "geometry", buildings)
    shapes = [(g, 1) for g in geoms if g is not None and not g.is_empty and g.area >= min_area]
    if not shapes:
        return np.zeros(len(ids))
    mask = features.rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="uint8")
    return np.asarray(ndimage.mean(mask.astype("float32"), labels, ids))
