"""Fill curves and shape of depressions, from sums that add up exactly across tiles.

**Fill curve.** With the water `t` metres below full, a pixel of depth `d` is under water when
`d > t`, and holds `d - t` of water. The flooded area and the volume at each level in
`FILL_LEVELS` are sums over pixels, so pieces of a depression in different tiles simply add up.

**Shape.** The depression is described by the ellipse with the same second moments as its
pixels (each pixel a uniform rectangle, weighted by its ground area): `length_m` and `width_m`
are that ellipse's axes (4 standard deviations along each), `orientation_deg` the direction of
the long axis, clockwise from north (0 to 180). A one-pixel depression on a 30 m grid is
34.6 m by 34.6 m; a canal one pixel wide and N long is about 1.15 N pixels long and 34.6 m wide.
The moments are kept per piece as (weight, centre, centred sums) and merged exactly.
"""
import math

import numpy as np

FILL_LEVELS = ((0.1, "10cm"), (0.25, "25cm"), (0.5, "50cm"), (1.0, "1m"), (2.0, "2m"), (5.0, "5m"))
AREA_DOWN = [f"area_{name}_down_m2" for _, name in FILL_LEVELS]
VOLUME_DOWN = [f"volume_{name}_down_m3" for _, name in FILL_LEVELS]
SHAPE_COLUMNS = ["length_m", "width_m", "orientation_deg"]
LEVEL_COLUMNS = ["ground_m", "water_level_m"]
MOMENTS = ["m_w", "m_c", "m_r", "m_cc", "m_rr", "m_cr"]


def fill_sums(depth, area):
    """Per pixel: area under water and water held at each fill level (arrays, one per level)."""
    depth, area = np.asarray(depth, dtype=float), np.asarray(area, dtype=float)
    out = {}
    for (t, _), a_name, v_name in zip(FILL_LEVELS, AREA_DOWN, VOLUME_DOWN):
        wet = depth > t
        out[a_name] = np.where(wet, area, 0.0)
        out[v_name] = np.where(wet, (depth - t) * area, 0.0)
    return out


def moments(group, rows, cols, weight, n_groups):
    """Per group: total weight, weighted centre (column, row) and centred second-moment sums,
    in pixel units, each pixel counted as a uniform unit square (variance 1/12 per axis)."""
    w = np.bincount(group, weights=weight, minlength=n_groups)
    safe = np.where(w > 0, w, 1.0)
    mc = np.bincount(group, weights=weight * cols, minlength=n_groups) / safe
    mr = np.bincount(group, weights=weight * rows, minlength=n_groups) / safe
    dc, dr = cols - mc[group], rows - mr[group]
    return {"m_w": w, "m_c": mc, "m_r": mr,
            "m_cc": np.bincount(group, weights=weight * dc * dc, minlength=n_groups) + w / 12,
            "m_rr": np.bincount(group, weights=weight * dr * dr, minlength=n_groups) + w / 12,
            "m_cr": np.bincount(group, weights=weight * dc * dr, minlength=n_groups)}


def merge_moments(group, m, n_groups):
    """Moments of groups of pieces (`m`: a dict of per-piece arrays), merged exactly."""
    w = np.bincount(group, weights=m["m_w"], minlength=n_groups)
    safe = np.where(w > 0, w, 1.0)
    mc = np.bincount(group, weights=m["m_w"] * m["m_c"], minlength=n_groups) / safe
    mr = np.bincount(group, weights=m["m_w"] * m["m_r"], minlength=n_groups) / safe
    dc, dr = m["m_c"] - mc[group], m["m_r"] - mr[group]
    return {"m_w": w, "m_c": mc, "m_r": mr,
            "m_cc": np.bincount(group, weights=m["m_cc"] + m["m_w"] * dc * dc, minlength=n_groups),
            "m_rr": np.bincount(group, weights=m["m_rr"] + m["m_w"] * dr * dr, minlength=n_groups),
            "m_cr": np.bincount(group, weights=m["m_cr"] + m["m_w"] * dc * dr, minlength=n_groups)}


def shape(m, transform, spacing):
    """Length, width (m) and orientation (degrees clockwise from north) from merged moments.

    spacing: (dx, dy) per raster row in metres; the values at the depression's centre row
    (interpolated) convert pixel units to metres.
    """
    dx_rows, dy_rows = spacing
    rows = np.arange(len(dx_rows))
    dx = np.interp(m["m_r"], rows, dx_rows)          # m_r counts rows like the arrays do
    dy = np.interp(m["m_r"], rows, dy_rows)
    w = m["m_w"]
    east = math.copysign(1.0, transform.a) * dx         # map x grows by `a` per column
    north = math.copysign(1.0, transform.e) * dy        # map y grows by `e` per row (e < 0: south)
    cee = east * east * m["m_cc"] / w
    cnn = north * north * m["m_rr"] / w
    cen = east * north * m["m_cr"] / w
    half = (cee + cnn) / 2
    spread = np.sqrt(((cee - cnn) / 2) ** 2 + cen ** 2)
    length = 4 * np.sqrt(half + spread)
    width = 4 * np.sqrt(np.maximum(half - spread, 0.0))
    orientation = np.degrees(0.5 * np.arctan2(2 * cen, cnn - cee)) % 180.0
    orientation[orientation >= 180.0] = 0.0       # -1e-15 % 180 rounds to 180.0
    return length, width, orientation
