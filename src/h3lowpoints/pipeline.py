from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import rasterio

from .context.buildings import building_overlap
from .context.drains import drains_at_core
from .coverage import Footprint
from .geometry import cell_size_m, crs_info, nearest_distance_m, pixel_areas_m2
from .h3agg.aggregate import depression_cells, lonlat, pixel_centers, rollup
from .hydro.depressions import depth_from_array, label_depressions, lowest_points, touches_edge
from .resolution import FINEST_H3, check_resolution, guide_for_cell_size, metres_to_cells, resolve_resolution
from .store import (check_format, missing_files, read_meta, read_table, remove_other_formats,
                    write_meta, write_table)

CORE_COLUMNS = ["id", "max_depth_m", "area_m2", "volume_m3", "lat", "lon", "h3_lowpoint",
                "h3_lowpoint_15", "edge"]
CELL_TABLE = ["h3", "max_depth_m", "depress_area_m2", "depress_volume_m3", "n_lowpoints",
              "covered_area_m2", "coverage", "depressed_share"]
DRAIN_COLUMNS = ["dist_to_drain_m", "drain_at_core"]
BUILDING_COLUMNS = ["building_overlap", "likely_building_artifact"]
TEXT_COLUMNS = {"h3", "h3_lowpoint", "h3_lowpoint_15"}
BOOL_COLUMNS = {"edge", "drain_at_core", "likely_building_artifact"}
INT_COLUMNS = {"id", "n_lowpoints"}


def _empty(columns):
    """A table with no rows whose columns have the types they have when there are rows."""
    def dtype(c):
        return ("object" if c in TEXT_COLUMNS else "bool" if c in BOOL_COLUMNS
                else "int64" if c in INT_COLUMNS else "float64")
    return pd.DataFrame({c: pd.Series(dtype=dtype(c)) for c in columns})


def empty_depressions(columns=CORE_COLUMNS):
    return _empty(columns)


def empty_cells():
    return _empty(CELL_TABLE)


@dataclass
class Result:
    """Output of an analysis.

    depressions  one row per depression (columns depend on what was requested, see docs/outputs.md)
    cells        one row per H3 cell that contains depression pixels
    meta         parameters, versions and resolution notes
    path         the folder holding the tables, when they were written to disk instead of
                 returned (`analyze_tiled(out_dir=...)`); `load()` reads them back
    """
    depressions: pd.DataFrame
    cells: pd.DataFrame
    meta: dict
    labels: np.ndarray = field(repr=False, default=None)
    depth: np.ndarray = field(repr=False, default=None)
    transform: object = field(repr=False, default=None)
    crs: object = field(repr=False, default=None)
    footprint: object = field(repr=False, default=None)
    path: object = None

    def at_resolution(self, res):
        """Cell table at a coarser H3 resolution, rolled up without re-analysis.

        Depth is the maximum of the children, area and volume add up, and the coverage of each
        coarser cell is measured afresh from the DEM's footprint (it cannot be summed from
        the cells in the table, which hold only the cells that have depressions).
        """
        out = rollup(self.cells, res)
        return add_coverage(out, self.footprint)

    @property
    def res(self):
        """The H3 resolution of the cell table (resolved, when "auto" was asked for)."""
        return self.meta["params"]["res"]

    def to_deck(self, value="max_depth_m", **kwargs):
        """3D pydeck map of the cells (needs the `viz` extra). See `viz.deck.cells_deck`."""
        from .viz.deck import cells_deck

        return cells_deck(self.cells, value=value, **kwargs)

    def to_html(self, path, **kwargs):
        """Write an interactive web map of this result. See `viz.webmap.build_map`."""
        from .viz.webmap import build_map

        return build_map(self.cells, self.depressions, self.meta, path, footprint=self.footprint,
                         **kwargs)

    def save(self, outdir, format="csv"):
        """Write `depressions`, `cells` (as .csv or .parquet) and `meta.json` to a folder."""
        check_format(format)
        if self.depressions is None:
            raise ValueError(f"the tables are already on disk in {self.path}; use load() to read them")
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        remove_other_formats(out, format)
        write_table(self.depressions, out / f"depressions.{format}", format)
        write_table(self.cells, out / f"cells.{format}", format)
        write_meta(out, {**self.meta, "output": {"format": format, "n_cells": len(self.cells)}})
        return out

    def load(self):
        """A copy of this result with its tables read from `path` (for results written to disk)."""
        if self.path is None:
            return self
        loaded = read_result(self.path)
        return Result(loaded.depressions, loaded.cells, self.meta, transform=self.transform,
                      crs=self.crs, footprint=self.footprint, path=self.path)


def read_result(folder):
    """A result saved in a folder (by `Result.save`, `analyze_tiled(out_dir=...)` or the CLI).

    The returned `Result` has the two tables and `meta`; it has no footprint, so
    `at_resolution` rolls up without measuring coverage afresh.
    """
    missing = missing_files(folder)
    if missing:
        raise FileNotFoundError(f"{folder} is missing {', '.join(missing)}")
    return Result(read_table(folder, "depressions"), read_table(folder, "cells"), read_meta(folder),
                  path=Path(folder))


def add_coverage(cells, footprint):
    """Add `covered_area_m2`, `coverage` and `depressed_share` to a cell table, if a footprint is known.

    `depressed_share` is the depressed area over the covered area, capped at 1 (the two come from
    different counts, see docs/definition.md).
    """
    if footprint is None or len(cells) == 0:
        return cells
    cov = footprint.coverage(cells["h3"])
    covered = cov["covered_area_m2"].to_numpy()
    share = np.minimum(1.0, cells["depress_area_m2"].to_numpy() / np.where(covered > 0, covered, np.nan))
    return cells.assign(covered_area_m2=covered, coverage=cov["coverage"].to_numpy(),
                        depressed_share=share)


def finish_cells(cells, lowpoints, footprint):
    """The cell table in its final form: lowest-point counts, coverage, fixed column order.

    `lowpoints` are the `h3_lowpoint` ids of the depressions (at the table's resolution).
    """
    counts = pd.Series(lowpoints).value_counts()
    cells = cells.assign(n_lowpoints=cells["h3"].map(counts).fillna(0).astype("int64"))
    cells = add_coverage(cells, footprint)
    if len(cells) == 0:
        return empty_cells()
    return cells[CELL_TABLE].reset_index(drop=True)


def resolution_guide(dem_path):
    """Which H3 resolutions a DEM file supports: the finest, the summary-only ones, and per level
    the cell area and how many DEM pixels each cell holds. See `resolution.guide_for_cell_size`."""
    with rasterio.open(dem_path) as src:
        size = cell_size_m(pixel_areas_m2(src.transform, src.crs, src.height))
    return guide_for_cell_size(size)


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
    """Validate the request against the DEM. Returns (cell_size_m, tier, res).

    `res` may be "auto", which becomes the finest H3 resolution the DEM supports. Raises
    ValueError for a CRS, grid or resolution the analysis cannot use, and warns about requests
    the DEM cannot support. Callers that analyze many windows call this once for the whole DEM
    and pass the result on.
    """
    size = cell_size_m(pixel_areas_m2(transform, crs, height))
    res = resolve_resolution(res, size)
    return size, check_resolution(size, res, min_depth), res


def base_meta(source, crs, shape, size, tier, geographic, res, min_depth, drain_radius_m,
              drain_core_frac, drains_xy, buildings, res_requested=None):
    """The run description stored in `Result.meta`, shared by whole-raster and tiled runs."""
    return {
        "res_requested": res if res_requested is None else res_requested,
        "package_version": _package_version(), "h3_version": h3.__version__, "dem": source,
        "crs": str(crs), "shape": list(shape), "cell_size_m": size, "geographic": geographic,
        "resolution_tier": tier,
        "params": {"res": res, "min_depth": min_depth, "drain_radius_m": drain_radius_m,
                   "drain_core_frac": drain_core_frac},
        "n_drains": 0 if drains_xy is None else len(drains_xy),
        "n_buildings": None if buildings is None else len(buildings),
    }


def analyze_array(arr, transform, crs, nodata=None, *, drains_xy=None, buildings=None, res="auto",
                  min_depth=0.15, drain_radius_m=5.0, drain_core_frac=0.8, mask=None,
                  source="array", _plan=None):
    """Analyze an in-memory DEM.

    drains_xy: optional (n, 2) drain locations in the DEM's CRS. Adds `dist_to_drain_m` and
        `drain_at_core` (a drain within `drain_radius_m` of the part of the depression deeper
        than `drain_core_frac` of its maximum).
    mask: optional boolean array, True where the DEM should be ignored (for example open water).
        Masked cells are treated like nodata: water can leave the DEM through them.
    res: H3 resolution of the cell table, or "auto" (the default) for the finest the DEM supports.
    """
    has_drains = drains_xy is not None and len(drains_xy) > 0
    requested = res
    size, tier, res = _plan or plan_analysis(crs, transform, arr.shape[0], res, min_depth)
    geographic, _ = crs_info(crs)
    row_areas = pixel_areas_m2(transform, crs, arr.shape[0])
    invalid = np.isnan(arr)
    if nodata is not None:
        invalid |= arr == nodata
    if mask is not None:
        invalid |= np.asarray(mask, dtype=bool)

    depth = depth_from_array(arr, invalid)
    labels, st = label_depressions(depth, row_areas, min_depth)
    ids = np.asarray(st["id"]).astype(int)
    meta = base_meta(source, crs, arr.shape, size, tier, geographic, res, min_depth,
                     drain_radius_m, drain_core_frac, drains_xy if has_drains else None, buildings,
                     res_requested=requested)
    columns = dep_columns(has_drains, buildings is not None)
    if len(ids) == 0:
        meta.update({"n_depressions": 0, "n_edge": 0})
        footprint = Footprint.from_array(invalid, transform, crs)
        cells = finish_cells(depression_cells(depth, labels, transform, crs, res), [], footprint)
        return Result(empty_depressions(columns), cells, meta, labels, depth, transform, crs,
                      footprint)

    mx, area, vol = (np.asarray(st[k]) for k in ("max_depth_m", "area_m2", "volume_m3"))
    edge = touches_edge(labels, ids)

    rows, cols = lowest_points(depth, labels, ids)
    xs, ys = pixel_centers(transform, rows, cols)
    lon, lat = lonlat(crs, xs, ys)
    deps = pd.DataFrame({
        "id": ids, "max_depth_m": mx, "area_m2": area, "volume_m3": vol, "lat": lat, "lon": lon,
        "h3_lowpoint": [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)],
        "h3_lowpoint_15": [h3.latlng_to_cell(la, lo, FINEST_H3) for la, lo in zip(lat, lon)],
        "edge": edge})
    if has_drains:
        deps["dist_to_drain_m"] = nearest_distance_m(drains_xy, np.c_[xs, ys], crs)
        deps["drain_at_core"] = drains_at_core(
            labels, depth, ids, mx, np.asarray(drains_xy), transform, drain_core_frac,
            metres_to_cells(drain_radius_m, size))
    if buildings is not None:
        deps["building_overlap"] = building_overlap(
            labels, ids, buildings, transform, arr.shape, min_area=0.0 if geographic else 20.0)
        deps["likely_building_artifact"] = deps["building_overlap"] >= 0.5

    footprint = Footprint.from_array(invalid, transform, crs)
    cells = finish_cells(depression_cells(depth, labels, transform, crs, res), deps["h3_lowpoint"],
                         footprint)
    meta.update({"n_depressions": len(deps), "n_edge": int(deps["edge"].sum())})
    return Result(deps[columns], cells, meta, labels, depth, transform, crs, footprint)


def analyze(dem_path, *, drains_xy=None, buildings=None, res="auto", min_depth=0.15,
            drain_radius_m=5.0, drain_core_frac=0.8, mask=None):
    """Find closed depressions in a DEM file and describe them on H3 cells.

    drains_xy: optional (n, 2) array of drain locations in the DEM's CRS.
    buildings: optional GeoDataFrame (or iterable of shapely polygons) in the DEM's CRS.
    mask: optional boolean array (same shape as the DEM), True where the DEM is to be ignored.
    res: H3 resolution of the cell table, or "auto" (the default) for the finest the DEM supports.
    """
    with rasterio.open(dem_path) as src:
        arr, nodata, transform, crs = src.read(1), src.nodata, src.transform, src.crs
    return analyze_array(arr, transform, crs, nodata, drains_xy=drains_xy, buildings=buildings,
                         res=res, min_depth=min_depth, drain_radius_m=drain_radius_m,
                         drain_core_frac=drain_core_frac, mask=mask, source=str(dem_path))
