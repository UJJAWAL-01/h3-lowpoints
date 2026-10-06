import json
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import cKDTree

from .buildings import building_overlap
from .classify import CODES, classify, serving_drains, touches_edge
from .h3agg.aggregate import depression_cells
from .hydro.depressions import depth_from_array, label_depressions

DEP_COLS = ["id", "max_depth_m", "area_m2", "volume_m3", "lat", "lon", "h3_lowpoint",
            "dist_to_drain_m", "drain_at_core", "significant", "suspect_pit", "edge", "cls",
            "building_overlap", "likely_building_artifact"]


@dataclass
class Result:
    depressions: pd.DataFrame
    cells: pd.DataFrame
    meta: dict
    labels: np.ndarray = field(repr=False, default=None)
    depth: np.ndarray = field(repr=False, default=None)
    transform: object = field(repr=False, default=None)
    crs: object = field(repr=False, default=None)

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
            "Reproject it first, for example with gdalwarp -t_srs EPSG:xxxxx."
        )


def analyze_array(arr, transform, crs, nodata=None, drains_xy=None, res=11, min_depth=0.15,
                  core_frac=0.8, radius=5, sig_depth=0.3, sig_area=150.0, pit_depth=1.5,
                  pit_area=150.0, buildings=None, core=None, source="array"):
    """Analyze an in-memory DEM.

    `core` is an optional (row0, row1, col0, col1) pixel box. When given, only depressions whose
    lowest point lies inside it are returned (this is how tiling avoids double counting).
    """
    _check_crs(crs)
    invalid = np.isnan(arr)
    if nodata is not None:
        invalid |= arr == nodata
    cell_area = abs(transform.a * transform.e)

    depth = depth_from_array(arr, invalid)
    labels, st = label_depressions(depth, cell_area, min_depth)
    ids = np.asarray(st["id"]).astype(int)
    has_drains = drains_xy is not None and len(drains_xy) > 0
    meta = {
        "package_version": _package_version(), "h3_version": h3.__version__, "dem": source,
        "crs": str(crs), "shape": list(arr.shape), "cell_size_m": float(abs(transform.a)),
        "params": {"res": res, "min_depth": min_depth, "core_frac": core_frac, "radius": radius,
                   "sig_depth": sig_depth, "sig_area": sig_area, "pit_depth": pit_depth,
                   "pit_area": pit_area},
        "n_drains": len(drains_xy) if has_drains else 0,
    }
    if len(ids) == 0:
        meta["n_depressions"] = 0
        return Result(pd.DataFrame(columns=DEP_COLS), pd.DataFrame(), meta, labels, depth,
                      transform, crs)

    mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))
    edge = touches_edge(labels, ids)
    served = (serving_drains(labels, depth, ids, mx, np.asarray(drains_xy), transform,
                             core_frac, radius)
              if has_drains else np.zeros(len(ids), dtype=bool))
    cls, significant, pit = classify(mx, area, served, edge, sig_depth, sig_area,
                                     pit_depth, pit_area)

    pos = ndimage.maximum_position(depth, labels, ids.tolist())
    rows = np.array([p[0] for p in pos])
    cols = np.array([p[1] for p in pos])
    xs, ys = rasterio.transform.xy(transform, rows, cols)
    xs, ys = np.asarray(xs), np.asarray(ys)
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(xs, ys)
    dist = (cKDTree(np.asarray(drains_xy)).query(np.c_[xs, ys])[0]
            if has_drains else np.full(len(ids), np.nan))

    deps = pd.DataFrame({
        "id": ids, "max_depth_m": mx, "area_m2": area, "volume_m3": vol, "lat": lat, "lon": lon,
        "h3_lowpoint": [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)],
        "dist_to_drain_m": dist, "drain_at_core": served, "significant": significant,
        "suspect_pit": pit, "edge": edge, "cls": cls})
    if buildings is not None:
        deps["building_overlap"] = building_overlap(labels, ids, buildings, transform, arr.shape)
    else:
        deps["building_overlap"] = np.nan
    deps["likely_building_artifact"] = deps["building_overlap"].fillna(0.0) >= 0.5

    keep = np.ones(len(ids), dtype=bool)
    if core is not None:
        r0, r1, c0, c1 = core
        keep = (rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)
    size = int(labels.max()) + 1
    code_by_label = np.zeros(size)
    code_by_label[ids[keep]] = [CODES[c] for c in cls[keep]]
    if core is not None:
        owned = np.zeros(size, dtype=bool)
        owned[ids[keep]] = True
        labels = np.where(owned[labels], labels, 0)
    cells = depression_cells(depth, labels, code_by_label, transform, crs, res)
    deps = deps[keep].reset_index(drop=True)
    meta["n_depressions"] = len(deps)
    meta["class_counts"] = pd.Series(cls[keep]).value_counts().to_dict()
    return Result(deps, cells, meta, labels, depth, transform, crs)


def analyze(dem_path, drains_xy=None, res=11, min_depth=0.15, core_frac=0.8, radius=5,
            sig_depth=0.3, sig_area=150.0, pit_depth=1.5, pit_area=150.0, buildings=None):
    """Find depressions in a DEM file and describe them on H3 cells.

    drains_xy: optional (n, 2) array of drain locations in the DEM's CRS.
    buildings: optional GeoDataFrame of footprints in the DEM's CRS.
    Thresholds are fixed defaults; changing them is a deliberate analysis choice.
    """
    with rasterio.open(dem_path) as src:
        arr, nodata, transform, crs = src.read(1), src.nodata, src.transform, src.crs
    return analyze_array(arr, transform, crs, nodata, drains_xy=drains_xy, res=res,
                         min_depth=min_depth, core_frac=core_frac, radius=radius,
                         sig_depth=sig_depth, sig_area=sig_area, pit_depth=pit_depth,
                         pit_area=pit_area, buildings=buildings, source=str(dem_path))
