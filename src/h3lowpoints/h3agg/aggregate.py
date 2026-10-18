import h3
import numpy as np
import pandas as pd
from h3.api import numpy_int
from pyproj import Transformer

from ..geometry import pixel_areas_m2
from ..hydro.depressions import pixel_weights

CELL_COLUMNS = ["h3", "max_depth_m", "depress_area_m2", "depress_volume_m3"]


def pixel_centers(transform, rows, cols):
    """Map coordinates of pixel centres, vectorised."""
    x, y = transform @ (np.asarray(cols) + 0.5, np.asarray(rows) + 0.5)
    return np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")


def lonlat(crs, x, y):
    """Longitude and latitude of map coordinates (a no-op for WGS 84)."""
    if crs.to_epsg() == 4326:
        return np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
    return Transformer.from_crs(crs, 4326, always_xy=True).transform(x, y)


def h3_ids(lat, lon, res):
    """H3 cell ids (as integers) for arrays of latitude and longitude."""
    return np.fromiter((numpy_int.latlng_to_cell(a, b, res) for a, b in zip(lat, lon)),
                       dtype=np.uint64, count=len(lat))


def depression_cells(depth, labels, transform, crs, res):
    """One row per H3 cell that contains depression pixels.

    Each pixel belongs to the cell containing its centre. Area and volume are sums over those
    pixels and depth is their maximum, so results can be rolled up to coarser resolutions with
    `rollup` without loss.
    """
    columns = CELL_COLUMNS
    rr, cc = np.nonzero(labels > 0)
    if len(rr) == 0:
        return pd.DataFrame(columns=columns)
    lon, lat = lonlat(crs, *pixel_centers(transform, rr, cc))
    area = pixel_weights(pixel_areas_m2(transform, crs, depth.shape[0]), rr)
    d = depth[rr, cc]
    df = pd.DataFrame({"cell": h3_ids(lat, lon, res), "depth": d, "area": area, "volume": d * area})
    out = df.groupby("cell").agg(max_depth_m=("depth", "max"), depress_area_m2=("area", "sum"),
                                 depress_volume_m3=("volume", "sum")).reset_index()
    out["h3"] = [numpy_int.int_to_str(int(c)) for c in out["cell"]]
    return out[columns]


def rollup(cells, res):
    """Aggregate a cell table to a coarser H3 resolution.

    Depth takes the maximum of the children; area, volume and lowest-point counts add up.
    """
    if len(cells) == 0:
        return cells.copy()
    src = h3.get_resolution(cells["h3"].iloc[0])
    if not 0 <= res <= src:
        raise ValueError(f"Can only roll up to a coarser resolution than {src}, got {res}")
    parent = {c: h3.cell_to_parent(c, res) for c in cells["h3"].unique()}
    spec = {"max_depth_m": ("max_depth_m", "max"),
            "depress_area_m2": ("depress_area_m2", "sum"),
            "depress_volume_m3": ("depress_volume_m3", "sum")}
    if "n_lowpoints" in cells:
        spec["n_lowpoints"] = ("n_lowpoints", "sum")
    return (cells.assign(h3=cells["h3"].map(parent)).groupby("h3", as_index=False).agg(**spec))
