from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

KEEP = [
    "status", "enabled", "year_abandoned", "year_built", "subtype", "struct_shape",
    "material", "width", "height", "inlet_type", "sump", "dem_calculated_sump",
    "depressed", "depression_a", "up_invert", "dn_invert", "ditch_shape",
    "up_node_type", "dn_node_type", "surface_elevation", "top_elevation", "ownership",
]
INLET_NODES = {"INLET", "GRATE"}


def load_clipped(name, bbox_lonlat, crs, folder="data/assets", pad=0.005, tag="pilot"):
    """Load an Austin asset layer, clip to the pilot box, reproject, and cache as GPKG."""
    folder = Path(folder)
    cache = folder / f"{tag}_{name}.gpkg"
    if cache.exists():
        return gpd.read_file(cache)
    g = gpd.read_file(folder / f"{name}.geojson").set_crs(4326, allow_override=True)
    minx, miny, maxx, maxy = bbox_lonlat
    g = g.cx[minx - pad:maxx + pad, miny - pad:maxy + pad]
    g = g[[c for c in KEEP if c in g.columns] + ["geometry"]].copy()
    for c in g.columns:
        if c != "geometry":
            g[c] = g[c].astype("string")
    g = g.to_crs(crs)
    g.to_file(cache, driver="GPKG")
    return g


def _col(g, name):
    return g[name] if name in g.columns else pd.Series(pd.NA, index=g.index, dtype="string")


def existed_at(g, year):
    """Keep assets that plausibly existed and worked in `year` (the lidar year)."""
    yb = pd.to_numeric(_col(g, "year_built"), errors="coerce").astype("float64")
    ya = pd.to_numeric(_col(g, "year_abandoned"), errors="coerce").astype("float64")
    st = _col(g, "status").astype("object").fillna("")
    keep = ~st.isin(["NEVERCON", "UNDERCON"])
    keep &= yb.isna() | (yb <= year)
    gone = st.isin(["REMOVED", "INACTIVE"])
    keep &= ~gone | (ya > year)
    return g[np.asarray(keep.fillna(False), dtype=bool)]


def drain_points(combo, bridge, pipes, year, include_pr=True):
    """Points where surface water can enter the storm system (inlets and grates)."""
    pts = []
    for layer in (combo, bridge):
        pts += list(existed_at(layer, year).geometry)
    kinds = set(INLET_NODES)
    if include_pr:
        kinds |= {"PR" + k for k in INLET_NODES}
    p = existed_at(pipes, year)
    for geom, up, dn in zip(p.geometry, p["up_node_type"], p["dn_node_type"]):
        lines = list(getattr(geom, "geoms", [geom]))
        if not lines:
            continue
        if str(up) in kinds:
            pts.append(Point(lines[0].coords[0][:2]))
        if str(dn) in kinds:
            pts.append(Point(lines[-1].coords[-1][:2]))
    return gpd.GeoDataFrame(geometry=pts, crs=pipes.crs)


def points_to_xy(points, precision=0.5):
    """Unique (x, y) array from a GeoDataFrame of points, rounded to `precision` map units."""
    xy = np.array([(g.x, g.y) for g in points.geometry], dtype="float64").reshape(-1, 2)
    if len(xy) == 0:
        return xy
    _, keep = np.unique(np.round(xy / precision) * precision, axis=0, return_index=True)
    return xy[np.sort(keep)]
