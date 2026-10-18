import json
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import rasterio

from .context.buildings import building_overlap
from .context.drains import drains_at_core
from .geometry import cell_size_m, crs_info, nearest_distance_m, pixel_areas_m2
from .h3agg.aggregate import depression_cells, lonlat, pixel_centers, rollup
from .hydro.depressions import (depth_and_provisional, label_depressions, lowest_points,
                                touches_edge)
from .resolution import check_resolution, metres_to_cells

CORE_COLUMNS = ["id", "max_depth_m", "area_m2", "volume_m3", "lat", "lon", "h3_lowpoint", "edge",
                "level_uncertain"]
DRAIN_COLUMNS = ["dist_to_drain_m", "drain_at_core"]
BUILDING_COLUMNS = ["building_overlap", "likely_building_artifact"]


@dataclass
class Result:
    """Output of an analysis.

    depressions  one row per depression (columns depend on what was requested, see docs/outputs.md)
    cells        one row per H3 cell that contains depression pixels
    meta         parameters, versions and resolution notes
    """
    depressions: pd.DataFrame
    cells: pd.DataFrame
    meta: dict
    labels: np.ndarray = field(repr=False, default=None)
    depth: np.ndarray = field(repr=False, default=None)
    transform: object = field(repr=False, default=None)
    crs: object = field(repr=False, default=None)

    def at_resolution(self, res):
        """Cell table rolled up to a coarser H3 resolution (no re-analysis needed)."""
        return rollup(self.cells, res)

    def to_deck(self, value="max_depth_m", **kwargs):
        """3D pydeck map of the cells (needs the `viz` extra). See `viz.deck.cells_deck`."""
        from .viz.deck import cells_deck

        return cells_deck(self.cells, value=value, **kwargs)

    def save(self, outdir):
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        self.depressions.to_csv(out / "depressions.csv", index=False)
        self.cells.to_csv(out / "cells.csv", index=False)
        (out / "meta.json").write_text(json.dumps(self.meta, indent=2, default=str))
        return out


def _package_version():
    try:
        return metadata.version("h3-lowpoints")
    except metadata.PackageNotFoundError:
        return "unknown"


def dep_columns(has_drains, has_buildings):
    """Columns of the depression table. Optional layers add columns only when they were used."""
    return (CORE_COLUMNS + (DRAIN_COLUMNS if has_drains else [])
            + (BUILDING_COLUMNS if has_buildings else []))


def plan_analysis(crs, transform, height, res, min_depth):
    """Validate the request against the DEM. Returns (cell_size_m, tier).

    Raises ValueError for a CRS or grid the analysis cannot use, and emits warnings for requests
    the DEM cannot support. Callers that analyze many windows call this once for the whole DEM
    and pass the result on.
    """
    size = cell_size_m(pixel_areas_m2(transform, crs, height))
    return size, check_resolution(size, res, min_depth)


def analyze_array(arr, transform, crs, nodata=None, *, drains_xy=None, buildings=None, res=11,
                  min_depth=0.15, drain_radius_m=5.0, drain_core_frac=0.8, core=None,
                  open_sides=(False,) * 4, source="array", _plan=None):
    """Analyze an in-memory DEM.

    drains_xy: optional (n, 2) drain locations in the DEM's CRS. Adds `dist_to_drain_m` and
        `drain_at_core` (a drain within `drain_radius_m` of the part of the depression deeper
        than `drain_core_frac` of its maximum).
    core: optional (row0, row1, col0, col1) pixel box; when given only depressions whose lowest
        point lies inside it are returned (this is how tiling avoids double counting).
    open_sides: (top, bottom, left, right) flags for window borders that are cuts through larger
        data rather than real edges; depressions whose water level depends on them get
        `level_uncertain` (see docs/definition.md).
    """
    has_drains = drains_xy is not None and len(drains_xy) > 0
    size, tier = _plan or plan_analysis(crs, transform, arr.shape[0], res, min_depth)
    geographic, _ = crs_info(crs)
    row_areas = pixel_areas_m2(transform, crs, arr.shape[0])
    invalid = np.isnan(arr)
    if nodata is not None:
        invalid |= arr == nodata

    depth, provisional = depth_and_provisional(arr, invalid, open_sides=open_sides)
    labels, st = label_depressions(depth, row_areas, min_depth)
    ids = np.asarray(st["id"]).astype(int)
    meta = {
        "package_version": _package_version(), "h3_version": h3.__version__, "dem": source,
        "crs": str(crs), "shape": list(arr.shape), "cell_size_m": size, "geographic": geographic,
        "resolution_tier": tier,
        "params": {"res": res, "min_depth": min_depth, "drain_radius_m": drain_radius_m,
                   "drain_core_frac": drain_core_frac},
        "n_drains": len(drains_xy) if has_drains else 0,
        "n_buildings": None if buildings is None else len(buildings),
    }
    columns = dep_columns(has_drains, buildings is not None)
    if len(ids) == 0:
        meta.update({"n_depressions": 0, "n_edge": 0, "n_level_uncertain": 0})
        return Result(pd.DataFrame(columns=columns), depression_cells(
            depth, labels, transform, crs, res), meta, labels, depth, transform, crs)

    mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))
    inside = labels > 0
    limited = np.bincount(labels[inside], weights=provisional[inside],
                          minlength=int(labels.max()) + 1)[ids] > 0
    edge = touches_edge(labels, ids)

    rows, cols = lowest_points(depth, labels, ids)
    xs, ys = pixel_centers(transform, rows, cols)
    lon, lat = lonlat(crs, xs, ys)
    deps = pd.DataFrame({
        "id": ids, "max_depth_m": mx, "area_m2": area, "volume_m3": vol, "lat": lat, "lon": lon,
        "h3_lowpoint": [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)],
        "edge": edge, "level_uncertain": limited})
    if has_drains:
        deps["dist_to_drain_m"] = nearest_distance_m(drains_xy, np.c_[xs, ys], crs)
        deps["drain_at_core"] = drains_at_core(
            labels, depth, ids, mx, np.asarray(drains_xy), transform, drain_core_frac,
            metres_to_cells(drain_radius_m, size))
    if buildings is not None:
        deps["building_overlap"] = building_overlap(
            labels, ids, buildings, transform, arr.shape, min_area=0.0 if geographic else 20.0)
        deps["likely_building_artifact"] = deps["building_overlap"] >= 0.5

    keep = np.ones(len(ids), dtype=bool)
    if core is not None:
        r0, r1, c0, c1 = core
        keep = (rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)
        owned = np.zeros(int(labels.max()) + 1, dtype=bool)
        owned[ids[keep]] = True
        labels = np.where(owned[labels], labels, 0)
    cells = depression_cells(depth, labels, transform, crs, res)
    deps = deps[keep].reset_index(drop=True)
    meta.update({"n_depressions": len(deps), "n_edge": int(deps["edge"].sum()),
                 "n_level_uncertain": int(deps["level_uncertain"].sum())})
    return Result(deps[columns], cells, meta, labels, depth, transform, crs)


def analyze(dem_path, *, drains_xy=None, buildings=None, res=11, min_depth=0.15,
            drain_radius_m=5.0, drain_core_frac=0.8):
    """Find closed depressions in a DEM file and describe them on H3 cells.

    drains_xy: optional (n, 2) array of drain locations in the DEM's CRS.
    buildings: optional GeoDataFrame (or iterable of shapely polygons) in the DEM's CRS.
    """
    with rasterio.open(dem_path) as src:
        arr, nodata, transform, crs = src.read(1), src.nodata, src.transform, src.crs
    return analyze_array(arr, transform, crs, nodata, drains_xy=drains_xy, buildings=buildings,
                         res=res, min_depth=min_depth, drain_radius_m=drain_radius_m,
                         drain_core_frac=drain_core_frac, source=str(dem_path))
