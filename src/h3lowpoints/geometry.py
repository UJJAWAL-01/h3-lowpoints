"""Ground geometry of a DEM grid: pixel areas, CRS units and distances.

The analysis needs areas and distances in metres. For a projected CRS those follow from the
pixel size and the CRS's linear unit (metres, feet, US survey feet). For geographic coordinates
(degrees) a pixel's ground area depends on its latitude, so areas are computed per row. The area
of a lon/lat cell on the WGS 84 ellipsoid has a closed form through the authalic latitude, so the
values are exact for the ellipsoid and need no resampling to a projected CRS. Geographic
coordinates are treated as WGS 84, whatever datum they are tagged with: datum differences are
metres, far below a pixel.
"""
import math

import numpy as np
from pyproj import CRS, Geod
from scipy.spatial import cKDTree

_GEOD = Geod(ellps="WGS84")
_E2 = _GEOD.es                      # first eccentricity squared of WGS 84
_E = math.sqrt(_E2)
_DEG = math.pi / 180.0


def _q(sin_phi):
    """Snyder's q(phi): proportional to the area between the equator and latitude phi."""
    return (1 - _E2) * (sin_phi / (1 - _E2 * sin_phi ** 2)
                        - np.log((1 - _E * sin_phi) / (1 + _E * sin_phi)) / (2 * _E))


_Q_POLE = float(_q(1.0))
EARTH_RADIUS_M = float(_GEOD.a * math.sqrt(_Q_POLE / 2))   # authalic radius, 6371007.18 m


def crs_info(crs):
    """(is_geographic, metres_per_unit) for a CRS, or ValueError if it cannot be used.

    For a geographic CRS the unit is degrees and `metres_per_unit` is None, because the length
    of a degree depends on where you are.
    """
    if crs is None:
        raise ValueError("The DEM has no coordinate reference system; set one before analyzing")
    crs = CRS.from_user_input(crs)   # accepts rasterio, pyproj and string CRS alike
    unit = crs.axis_info[0].unit_conversion_factor if crs.axis_info else None
    if crs.is_geographic:
        if unit is None or abs(unit - _DEG) > 1e-6:
            raise ValueError(f"Geographic CRS must be in degrees, got {crs}")
        return True, None
    if crs.is_projected and unit:
        return False, float(unit)
    raise ValueError(f"The DEM needs a projected or geographic (degree) CRS, got {crs}")


def check_transform(transform):
    if abs(transform.b) > 1e-12 or abs(transform.d) > 1e-12:
        raise ValueError("Rotated or sheared rasters are not supported; resample to a north-up grid")
    if transform.a == 0 or transform.e == 0:
        raise ValueError("The raster transform has a zero pixel size")


def pixel_areas_m2(transform, crs, height):
    """Ground area in square metres of one pixel in each row, as an array of length `height`."""
    check_transform(transform)
    geographic, unit = crs_info(crs)
    if not geographic:
        return np.full(height, abs(transform.a * transform.e) * unit * unit)
    top = transform.f + transform.e * np.arange(height)
    bottom = top + transform.e
    if np.abs(top).max() > 90 + 1e-9 or np.abs(bottom).max() > 90 + 1e-9:
        raise ValueError("Latitudes outside +/-90 degrees: check the raster's transform and CRS")
    band = np.abs(_q(np.sin(np.radians(top))) - _q(np.sin(np.radians(bottom)))) / _Q_POLE
    return EARTH_RADIUS_M ** 2 * abs(transform.a) * _DEG * band


def cell_size_m(row_areas):
    """Representative pixel size in metres: the side of a square with the middle row's area."""
    return float(math.sqrt(row_areas[len(row_areas) // 2]))


def _unit_vectors(x, y):  # for finding the nearest point; the distance itself is geodesic
    lon, lat = np.radians(x), np.radians(y)
    return np.c_[np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]


def nearest_distance_m(sources_xy, targets_xy, crs):
    """Distance in metres from each target point to the nearest source point.

    Points are in the DEM's CRS. Geographic distances are geodesic distances on the WGS 84
    ellipsoid; projected distances are scaled by the CRS's linear unit.
    """
    geographic, unit = crs_info(crs)
    src, dst = np.asarray(sources_xy, dtype="float64"), np.asarray(targets_xy, dtype="float64")
    if not geographic:
        return cKDTree(src).query(dst)[0] * unit
    nearest = cKDTree(_unit_vectors(src[:, 0], src[:, 1])).query(_unit_vectors(dst[:, 0], dst[:, 1]))[1]
    return _GEOD.inv(dst[:, 0], dst[:, 1], src[nearest, 0], src[nearest, 1])[2]
