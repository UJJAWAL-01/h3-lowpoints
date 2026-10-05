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

from .classify import CODES, classify, serving_drains, touches_edge
from .h3agg.aggregate import depression_cells
from .hydro.depressions import depth_from_array, label_depressions

DEP_COLS = ["id", "max_depth_m", "area_m2", "volume_m3", "lat", "lon", "h3_lowpoint",
            "dist_to_drain_m", "drain_at_core", "significant", "suspect_pit", "edge", "cls"]


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


def analyze(dem_path, drains_xy=None, res=11, min_depth=0.15, core_frac=0.8, radius=5,
            sig_depth=0.3, sig_area=150.0, pit_depth=1.5, pit_area=150.0):
    """Find depressions in a DEM and describe them on H3 cells.

    drains_xy: optional (n, 2) array of drain locations in the DEM's CRS.
    Thresholds are fixed defaults; changing them is a deliberate analysis choice.
    """
    with rasterio.open(dem_path) as src:
        arr, nodata, transform, crs = src.read(1), src.nodata, src.transform, src.crs
    invalid = np.isnan(arr)
    if nodata is not None:
        invalid |= arr == nodata
    cell_area = abs(transform.a * transform.e)

    depth = depth_from_array(arr, invalid)
    labels, st = label_depressions(depth, cell_area, min_depth)
    ids = np.asarray(st["id"]).astype(int)
    has_drains = drains_xy is not None and len(drains_xy) > 0
    try:
        version = metadata.version("h3-lowpoints")
    except metadata.PackageNotFoundError:
        version = "unknown"
    meta = {
        "package_version": version, "h3_version": h3.__version__, "dem": str(dem_path),
        "crs": str(crs), "shape": list(arr.shape), "cell_size_m": float(abs(transform.a)),
        "params": dict(res=res, min_depth=min_depth, core_frac=core_frac, radius=radius,
                       sig_depth=sig_depth, sig_area=sig_area, pit_depth=pit_depth, pit_area=pit_area),
        "n_depressions": int(len(ids)), "n_drains": int(len(drains_xy)) if has_drains else 0,
    }
    if len(ids) == 0:
        return Result(pd.DataFrame(columns=DEP_COLS), pd.DataFrame(), meta, labels, depth, transform, crs)

    mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))
    edge = touches_edge(labels, ids)
    served = (serving_drains(labels, depth, ids, mx, np.asarray(drains_xy), transform, core_frac, radius)
              if has_drains else np.zeros(len(ids), dtype=bool))
    cls, significant, pit = classify(mx, area, served, edge, sig_depth, sig_area, pit_depth, pit_area)

    pos = ndimage.maximum_position(depth, labels, ids.tolist())
    rows, cols = np.array([p[0] for p in pos]), np.array([p[1] for p in pos])
    xs, ys = rasterio.transform.xy(transform, rows, cols)
    xs, ys = np.asarray(xs), np.asarray(ys)
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(xs, ys)
    dist = cKDTree(np.asarray(drains_xy)).query(np.c_[xs, ys])[0] if has_drains else np.full(len(ids), np.nan)

    deps = pd.DataFrame({
        "id": ids, "max_depth_m": mx, "area_m2": area, "volume_m3": vol, "lat": lat, "lon": lon,
        "h3_lowpoint": [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)],
        "dist_to_drain_m": dist, "drain_at_core": served, "significant": significant,
        "suspect_pit": pit, "edge": edge, "cls": cls})

    code_by_label = np.zeros(int(labels.max()) + 1)
    code_by_label[ids] = [CODES[c] for c in cls]
    cells = depression_cells(depth, labels, code_by_label, transform, crs, res)
    meta["class_counts"] = pd.Series(cls).value_counts().to_dict()
    return Result(deps, cells, meta, labels, depth, transform, crs)
