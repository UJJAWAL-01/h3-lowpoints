"""What a DEM's cell size lets the analysis claim.

Closed depressions can be found in any DEM, but what they mean depends on its resolution. A 1 m
lidar DEM resolves street sags and pits; a 30 m global DEM resolves only large basins and carries
vertical errors that are often larger than a street-scale depression.
"""
import math
import warnings

import h3

FINE_MAX_M = 2.0       # up to this cell size: street-scale depressions are resolved
MEDIUM_MAX_M = 12.0    # up to this: neighbourhood-scale basins only
MIN_PIXELS_PER_H3_CELL = 4  # fewer DEM pixels than this per H3 cell makes cell statistics noise


def cell_size_m(transform):
    """Pixel size in map units (metres for a projected metric CRS); geometric mean of x and y."""
    return float(math.sqrt(abs(transform.a * transform.e)))


def resolution_tier(size_m):
    """'fine', 'medium' or 'coarse' for a DEM cell size in metres."""
    if size_m <= FINE_MAX_M:
        return "fine"
    return "medium" if size_m <= MEDIUM_MAX_M else "coarse"


def metres_to_cells(distance_m, size_m):
    """Whole DEM cells covering `distance_m` (at least one)."""
    return max(1, int(math.ceil(distance_m / size_m - 1e-9)))


def min_h3_resolution(size_m):
    """Finest H3 resolution whose cells still span at least MIN_PIXELS_PER_H3_CELL DEM pixels."""
    need = MIN_PIXELS_PER_H3_CELL * size_m * size_m
    for res in range(15, -1, -1):
        if h3.average_hexagon_area(res, unit="m^2") >= need:
            return res
    return 0


def check_resolution(size_m, res, min_depth):
    """Warn when the DEM cannot support the requested analysis. Returns the tier."""
    tier = resolution_tier(size_m)
    finest = min_h3_resolution(size_m)
    if res > finest:
        warnings.warn(
            f"H3 resolution {res} cells hold fewer than {MIN_PIXELS_PER_H3_CELL} pixels of a "
            f"{size_m:g} m DEM; per-cell values will be noisy. Use res <= {finest}.",
            UserWarning, stacklevel=3)
    if tier != "fine" and min_depth < 1.0:
        warnings.warn(
            f"The DEM cell size is {size_m:g} m ({tier}). Only large basins are resolved, and "
            f"vertical error in DEMs this coarse often exceeds min_depth={min_depth:g} m.",
            UserWarning, stacklevel=3)
    return tier
