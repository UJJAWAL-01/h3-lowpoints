"""What a DEM's cell size lets the analysis claim, and which H3 resolutions suit it.

Closed depressions can be found in any DEM, but what they mean depends on its resolution. A 1 m
lidar DEM resolves street sags and pits; a 30 m global DEM resolves only large basins and carries
vertical errors that are often larger than a street-scale depression.

The finest useful H3 resolution is set by the DEM, not by H3: a cell needs several pixels for its
statistics to mean anything. `res="auto"` picks that finest resolution. Every depression's
lowest point also gets a resolution-15 cell id (`h3_lowpoint_15`), the finest H3 has, so the
precise location is always available whatever resolution the cells are summarised at.
"""
import math
import operator
import warnings

import h3

FINE_MAX_M = 2.0       # up to this cell size: street-scale depressions are resolved
MEDIUM_MAX_M = 12.0    # up to this: neighbourhood-scale basins only
MIN_PIXELS_PER_H3_CELL = 4  # fewer DEM pixels than this per H3 cell makes cell statistics noise
SUMMARY_MAX_RES = 3    # cells this coarse (12,000 km2 and up) hold summaries, never "hotspots"
FINEST_H3 = 15


def resolution_tier(size_m):
    """'fine', 'medium' or 'coarse' for a DEM cell size in metres."""
    if size_m <= FINE_MAX_M:
        return "fine"
    return "medium" if size_m <= MEDIUM_MAX_M else "coarse"


def metres_to_cells(distance_m, size_m):
    """Whole DEM cells covering `distance_m` (at least one)."""
    return max(1, int(math.ceil(distance_m / size_m - 1e-9)))


def finest_h3_resolution(size_m):
    """Finest H3 resolution whose cells still hold at least MIN_PIXELS_PER_H3_CELL DEM pixels."""
    need = MIN_PIXELS_PER_H3_CELL * size_m * size_m
    for res in range(FINEST_H3, -1, -1):
        if h3.average_hexagon_area(res, unit="m^2") >= need:
            return res
    return 0


def resolve_resolution(res, size_m):
    """The H3 resolution to use: `res` itself, or the finest the DEM supports for "auto"."""
    message = f'res must be an integer from 0 to 15 or "auto", got {res!r}'
    if isinstance(res, str):
        text = res.strip().lower()
        if text == "auto":
            return finest_h3_resolution(size_m)
        if not text.isdigit():
            raise ValueError(message)
        res = int(text)
    elif isinstance(res, bool):
        raise ValueError(message)
    else:
        try:
            res = operator.index(res)       # ints and numpy integers; floats are refused
        except TypeError:
            raise ValueError(message) from None
    if not 0 <= res <= FINEST_H3:
        raise ValueError(message)
    return res


def check_resolution(size_m, res, min_depth):
    """Warn when the DEM cannot support the requested analysis. Returns the tier."""
    tier = resolution_tier(size_m)
    finest = finest_h3_resolution(size_m)
    if res > finest:
        warnings.warn(
            f"H3 resolution {res} cells hold fewer than {MIN_PIXELS_PER_H3_CELL} pixels of a "
            f"{size_m:g} m DEM; per-cell values will be noisy. Use res <= {finest} (or res='auto').",
            UserWarning, stacklevel=3)
    if tier != "fine" and min_depth < 1.0:
        warnings.warn(
            f"The DEM cell size is {size_m:g} m ({tier}). Only large basins are resolved, and "
            f"vertical error in DEMs this coarse often exceeds min_depth={min_depth:g} m.",
            UserWarning, stacklevel=3)
    return tier


def guide_for_cell_size(size_m):
    """Which H3 resolutions a DEM with pixels of `size_m` metres supports, and what each means."""
    pixel = size_m * size_m
    finest = finest_h3_resolution(size_m)
    levels = []
    for r in range(FINEST_H3 + 1):
        area = h3.average_hexagon_area(r, unit="m^2")
        role = ("summary only" if r <= SUMMARY_MAX_RES else
                "supported" if r <= finest else "too fine for this DEM")
        levels.append({"res": r, "cell_area_m2": area, "pixels_per_cell": area / pixel, "use": role})
    return {"cell_size_m": size_m, "tier": resolution_tier(size_m), "finest": finest,
            "summary_only": list(range(SUMMARY_MAX_RES + 1)), "levels": levels}
