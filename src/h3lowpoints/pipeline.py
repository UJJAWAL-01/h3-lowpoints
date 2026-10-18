import json
import warnings
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import rasterio
from scipy.spatial import cKDTree

from .context.buildings import building_overlap
from .context.drains import drains_at_core
from .h3agg.aggregate import depression_cells, lonlat, pixel_centers, rollup
from .hydro.depressions import (depth_and_provisional, label_depressions, lowest_points,
                                touches_edge)
from .resolution import cell_size_m, check_resolution, metres_to_cells
from .waterlogging.classify import CODES, WaterloggingParams, classify

CORE_COLUMNS = ["id", "max_depth_m", "area_m2", "volume_m3", "lat", "lon", "h3_lowpoint", "edge"]
DRAIN_COLUMNS = ["dist_to_drain_m", "drain_at_core"]
BUILDING_COLUMNS = ["building_overlap", "likely_building_artifact"]
WATERLOGGING_COLUMNS = ["significant", "suspect_pit", "cls"]


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


def _check_crs(crs):
    units = (crs.linear_units or "").lower() if crs is not None and crs.is_projected else ""
    if units not in ("metre", "meter"):
        raise ValueError(
            f"The DEM needs a projected CRS in metres (for example a UTM zone), got {crs}. "
            "Reproject it first with h3lowpoints.reproject_to_utm or gdalwarp -t_srs EPSG:xxxxx."
        )


def dep_columns(has_drains, has_buildings, wl):
    """Columns of the depression table. Optional layers add columns only when they were used."""
    return (CORE_COLUMNS + (DRAIN_COLUMNS if has_drains else [])
            + (BUILDING_COLUMNS if has_buildings else []) + (WATERLOGGING_COLUMNS if wl else []))


def plan_analysis(crs, transform, res, min_depth, waterlogging, has_drains):
    """Validate the request against the DEM and settle what will be computed.

    Returns (cell_size, tier, WaterloggingParams or None). Emits warnings for requests the DEM
    cannot support; callers that analyze many windows call this once and pass the result on.
    """
    _check_crs(crs)
    size = cell_size_m(transform)
    tier = check_resolution(size, res, min_depth)
    if waterlogging is None:
        waterlogging = tier == "fine"
    elif waterlogging is not False and tier != "fine":
        warnings.warn(
            "The waterlogging classes were only validated on DEMs of 2 m or finer; at "
            f"{size:g} m treat them as unvalidated.", UserWarning, stacklevel=3)
    wl = None
    if waterlogging is not False:
        wl = waterlogging if isinstance(waterlogging, WaterloggingParams) else WaterloggingParams()
        if not has_drains:
            warnings.warn(
                "No drain data was given, so 'unexplained' only means 'significant depression'; "
                "it does not mean no drain exists.", UserWarning, stacklevel=3)
    return size, tier, wl


def analyze_array(arr, transform, crs, nodata=None, *, drains_xy=None, buildings=None, res=11,
                  min_depth=0.15, waterlogging=None, core=None, open_sides=(False,) * 4,
                  source="array", _plan=None):
    """Analyze an in-memory DEM.

    waterlogging: None (on only for DEMs of 2 m or finer), True/False, or a WaterloggingParams.
    core: optional (row0, row1, col0, col1) pixel box; when given only depressions whose lowest
        point lies inside it are returned (this is how tiling avoids double counting).
    open_sides: (top, bottom, left, right) flags for window borders that are cuts through larger
        data rather than real edges; depressions whose water level depends on them are flagged
        `edge`.
    """
    has_drains = drains_xy is not None and len(drains_xy) > 0
    size, tier, wl = _plan or plan_analysis(crs, transform, res, min_depth, waterlogging,
                                            has_drains)
    invalid = np.isnan(arr)
    if nodata is not None:
        invalid |= arr == nodata
    cell_area = abs(transform.a * transform.e)

    depth, provisional = depth_and_provisional(arr, invalid, open_sides=open_sides)
    labels, st = label_depressions(depth, cell_area, min_depth)
    ids = np.asarray(st["id"]).astype(int)
    meta = {
        "package_version": _package_version(), "h3_version": h3.__version__, "dem": source,
        "crs": str(crs), "shape": list(arr.shape), "cell_size_m": size,
        "resolution_tier": tier,
        "params": {"res": res, "min_depth": min_depth,
                   "waterlogging": asdict(wl) if wl else None},
        "n_drains": len(drains_xy) if has_drains else 0,
        "n_buildings": None if buildings is None else len(buildings),
        "class_codes": CODES if wl else None,
    }
    columns = dep_columns(has_drains, buildings is not None, wl)
    if len(ids) == 0:
        meta["n_depressions"] = 0
        return Result(pd.DataFrame(columns=columns), depression_cells(
            depth, labels, None, transform, crs, res), meta, labels, depth, transform, crs)

    mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))
    inside = labels > 0
    limited = np.bincount(labels[inside], weights=provisional[inside],
                          minlength=int(labels.max()) + 1)[ids] > 0
    edge = touches_edge(labels, ids) | limited

    rows, cols = lowest_points(depth, labels, ids)
    xs, ys = pixel_centers(transform, rows, cols)
    lon, lat = lonlat(crs, xs, ys)
    deps = pd.DataFrame({
        "id": ids, "max_depth_m": mx, "area_m2": area, "volume_m3": vol, "lat": lat, "lon": lon,
        "h3_lowpoint": [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)],
        "edge": edge})
    if has_drains:
        served = drains_at_core(labels, depth, ids, mx, np.asarray(drains_xy), transform,
                                wl.core_frac if wl else WaterloggingParams.core_frac,
                                metres_to_cells(wl.drain_radius_m if wl
                                                else WaterloggingParams.drain_radius_m, size))
        deps["dist_to_drain_m"] = cKDTree(np.asarray(drains_xy)).query(np.c_[xs, ys])[0]
        deps["drain_at_core"] = served
    else:
        served = np.zeros(len(ids), dtype=bool)
    if buildings is not None:
        deps["building_overlap"] = building_overlap(labels, ids, buildings, transform, arr.shape)
        deps["likely_building_artifact"] = deps["building_overlap"] >= 0.5
    if wl:
        cls, significant, pit = classify(mx, area, served, edge, wl)
        deps["significant"], deps["suspect_pit"], deps["cls"] = significant, pit, cls

    keep = np.ones(len(ids), dtype=bool)
    if core is not None:
        r0, r1, c0, c1 = core
        keep = (rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)
    code_by_label = None
    if wl:
        code_by_label = np.zeros(int(labels.max()) + 1)
        code_by_label[ids[keep]] = [CODES[c] for c in cls[keep]]
    if core is not None:
        owned = np.zeros(int(labels.max()) + 1, dtype=bool)
        owned[ids[keep]] = True
        labels = np.where(owned[labels], labels, 0)
    cells = depression_cells(depth, labels, code_by_label, transform, crs, res)
    deps = deps[keep].reset_index(drop=True)
    meta["n_depressions"] = len(deps)
    meta["n_edge"] = int(deps["edge"].sum())
    if wl:
        meta["class_counts"] = pd.Series(cls[keep]).value_counts().to_dict()
    return Result(deps[columns], cells, meta, labels, depth, transform, crs)


def analyze(dem_path, *, drains_xy=None, buildings=None, res=11, min_depth=0.15,
            waterlogging=None):
    """Find closed depressions in a DEM file and describe them on H3 cells.

    drains_xy: optional (n, 2) array of drain locations in the DEM's CRS.
    buildings: optional GeoDataFrame (or iterable of shapely polygons) in the DEM's CRS.
    waterlogging: see `analyze_array`. Thresholds are fixed defaults; changing them is a
        deliberate analysis choice.
    """
    with rasterio.open(dem_path) as src:
        arr, nodata, transform, crs = src.read(1), src.nodata, src.transform, src.crs
    return analyze_array(arr, transform, crs, nodata, drains_xy=drains_xy, buildings=buildings,
                         res=res, min_depth=min_depth, waterlogging=waterlogging,
                         source=str(dem_path))
