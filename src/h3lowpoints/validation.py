"""Do observed locations fall in or near depressions more often than chance?

`validate` scores any set of observations (flood reports, high-water marks, insurance claims,
anything with coordinates) against the depression cells of a result. For each distance k, in
H3 rings, it compares the share of observations within k rings of depressed ground with the
share of control points (where observations could have come from, such as addresses). The
ratio is the lift. Its confidence interval comes from a spatial block bootstrap: whole coarse H3
cells are resampled, with their observations and controls together, so clustering in space is
not mistaken for evidence. See docs/validation.md.

Lift measures association, not cause: reports are made where people live and look, and controls
are only as good as their match to that.
"""
import itertools
import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path

import h3
import h3.api.basic_int as h3i
import numpy as np
import pandas as pd

from .pipeline import Result, _package_version, deeper_than_wide
from .store import CHUNK_ROWS, ids_to_int, iter_table, missing_files, read_meta, write_table

DEPTH_CLASSES = (0.5, 1.0, 2.0)
POINT_COLUMNS = ["point_id", "kind", "lon", "lat", "h3", "rings", "depth_m", "block"]
UNCLASSIFIED = "unclassified"  # depressed ground that no labelled depression claims
_DISK_BATCH = 20_000          # point cells whose rings are searched at a time
_FILTER_LEVELS = 4            # depression cells are first filtered by their parent this much coarser

RANDOM_CONTROLS = (
    "The controls are random points in the boundary. Observations such as flood reports come from "
    "where people live, travel and report, which random points ignore, so this lift mixes the "
    "effect of depressions with the effect of where people are. For a fair comparison give "
    "controls from the same population the observations come from (addresses, building centres).")


@dataclass
class Validation:
    """Output of `validate`.

    rings     one row per distance k (0 to max_rings): counts and shares of observations and
              controls within k rings of depressed ground, the lift and its confidence interval
    by_depth  the same for each depth class of the nearest depressed ground
    points    one row per observation and control used: its cell, its distance in rings to
              depressed ground (missing when beyond max_rings), that ground's depth, its block
              (and its class, with `classes`)
    report    everything above plus the settings, counts and warnings, as a JSON-ready dict
    by_class  with `classes`: the ring table for each class of the nearest depressed ground
    class_ratios  with `classes`: for each pair of classes and each k, the ratio of their lifts
              with its confidence interval
    """
    rings: pd.DataFrame
    by_depth: pd.DataFrame
    points: pd.DataFrame
    report: dict
    by_class: pd.DataFrame = None
    class_ratios: pd.DataFrame = None

    @property
    def warnings(self):
        return self.report["warnings"]

    def save(self, path, points=None):
        """Write the report as JSON to `path`, and the point table to `points` (.csv or .parquet)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report, indent=2))
        if points is not None:
            fmt = Path(points).suffix.lstrip(".").lower()
            write_table(self.points, points, "parquet" if fmt == "parquet" else "csv")
        return path

    def summary(self):
        """The ring table as text, for printing."""
        r, b = self.report, self.report["bootstrap"]
        level = f"{100 * b['ci']:g}%"
        lines = [f"Observations near depressions: H3 resolution {r['res']}, rings about "
                 f"{r['ring_spacing_m']:.0f} m apart",
                 f"{r['observations']['n']:,} observations, {r['controls']['n']:,} controls "
                 f"({r['controls']['source']}); {level} intervals from {b['n_boot']:,} samples of "
                 f"{b['n_blocks']:,} blocks (H3 resolution {b['block_res']})",
                 "",
                 f"{'rings':>5} {'~metres':>8} {'observations':>13} {'controls':>9} {'lift':>6}  "
                 f"{level} interval"]
        for row in self.rings.itertuples():
            lift = "-" if math.isnan(row.lift) else f"{row.lift:.2f}"
            ci = ("-" if math.isnan(row.lift_low) else f"{row.lift_low:.2f} to {row.lift_high:.2f}")
            lines.append(f"{row.k:>5} {row.distance_m:>8.0f} {100 * row.obs_share:>12.1f}% "
                         f"{100 * row.control_share:>8.1f}% {lift:>6}  {ci}")
        if self.by_class is not None:
            lines += ["", "By class of the nearest depressed ground:", f"{'class':>24} {'rings':>5} {'observations':>13} "
                          f"{'controls':>9} {'lift':>6}  {level} interval"]
            for row in self.by_class[self.by_class["k"] <= 1].to_dict("records"):
                lines.append(f"{row['class']:>24} {row['k']:>5} {row['obs_within']:>13,} "
                             f"{row['controls_within']:>9,} {_fmt(row['lift']):>6}  "
                             f"{_fmt_ci(row['lift_low'], row['lift_high'])}")
            lines += ["", f"{'lift of':>24} {'over':>24} {'rings':>5} {'ratio':>6}  {level} interval"]
            for row in self.class_ratios[self.class_ratios["k"] <= 1].to_dict("records"):
                lines.append(f"{row['class_a']:>24} {row['class_b']:>24} {row['k']:>5} "
                             f"{_fmt(row['ratio']):>6}  {_fmt_ci(row['ratio_low'], row['ratio_high'])}")
        return "\n".join(lines)


def _fmt(value):
    return "-" if math.isnan(value) else f"{value:.2f}"


def _fmt_ci(low, high):
    return "-" if math.isnan(low) else f"{low:.2f} to {high:.2f}"


def validate(result, observations, controls=None, *, boundary=None, n_random=None, max_rings=5,
             depth_classes=DEPTH_CLASSES, classes=None, class_order=None, block_res=6,
             n_boot=1000, ci=0.95, seed=0, exclude_suspect=True, rows=CHUNK_ROWS):
    """Lift of observations near depressions over controls, with block-bootstrap intervals.

    result         a `Result`, or a folder written by `analyze`, `build`, `clip` or `Result.save`
                   (read in pieces, so the national dataset works)
    observations   the observed locations (see `read_points` for the accepted forms)
    controls       where observations could have come from, in the same forms; when None,
                   `n_random` random points in `boundary` are used, with a warning
    boundary       the area studied (shapely geometry in lon/lat, or a vector file): points
                   outside it are left out. Without it, the result's recorded box is used if any
    max_rings      largest distance measured, in rings of the result's resolution
    depth_classes  edges, in metres, of the depth classes for `by_depth`
    classes        a label for each depression, for `by_class` and `class_ratios`: a Series
                   indexed by depression id, a dict, a DataFrame or a .csv/.parquet file with
                   `id` and `class` columns. Each labelled depression claims the depressed cells
                   within its equivalent radius, sqrt(area / pi), of its lowest point (rounded to
                   whole rings); where claims overlap, the depression holding the most water wins.
                   Depressed ground nobody claims is "unclassified"
    class_order    the classes in order (default: sorted): ties between equally near and deep
                   ground go to the earlier class, and ratios are of each class over each later one
    block_res      H3 resolution of the bootstrap blocks (coarser than or equal to the result's)
    n_boot, ci     bootstrap samples and confidence level of the percentile intervals
    exclude_suspect  leave out the ground around depressions flagged `deeper_than_wide`
    """
    meta, cells_src, deps_src, where = _source(result, rows)
    res = meta["params"]["res"]
    _check_settings(res, max_rings, depth_classes, block_res, n_boot, ci)
    labels, order = (None, None) if classes is None else _labels(classes, class_order)
    rng = np.random.default_rng(seed)
    notes = []

    obs, obs_missing = _points(observations)
    if len(obs) == 0:
        raise ValueError("there are no observations with coordinates")
    area, area_name = _area(boundary, meta)
    if controls is None:
        if boundary is None:
            raise ValueError("give controls, or a boundary to place random controls in")
        ctrl, ctrl_missing = random_points(area, n_random or max(1000, 10 * len(obs)), rng), 0
        source = "random points in the boundary"
        notes.append(RANDOM_CONTROLS)
    else:
        ctrl, ctrl_missing = _points(controls)
        source = "given"
    if area is None:
        notes.append("No boundary was given and the result records no box: points outside the "
                     "analysed area count as far from any depression. Pass the boundary studied.")
    elif boundary is None and "clip" in meta:
        notes.append(f"The result was clipped to {meta['clip'].get('boundary', 'a boundary')}, but "
                     "only its box is known here: pass the same boundary so points outside it are "
                     "left out.")
    obs_in, ctrl_in = _inside(area, obs), _inside(area, ctrl)
    obs, ctrl = obs[obs_in].reset_index(drop=True), ctrl[ctrl_in].reset_index(drop=True)
    if len(obs) == 0:
        raise ValueError("no observation lies inside the area studied")
    if len(ctrl) == 0:
        raise ValueError("no control lies inside the area studied")
    if (~obs_in).sum():
        notes.append(f"{(~obs_in).sum():,} observations lie outside the area studied and were left out.")

    pts = pd.concat([obs.assign(kind="observation"), ctrl.assign(kind="control")], ignore_index=True)
    cell = np.fromiter((h3i.latlng_to_cell(la, lo, res) for la, lo in zip(pts["lat"], pts["lon"])),
                       dtype=np.uint64, count=len(pts))
    unique, inverse = np.unique(cell, return_inverse=True)

    coarse = max(0, res - _FILTER_LEVELS)
    shift = np.uint64(3 * (15 - coarse))
    needed = np.unique(np.concatenate([np.unique(flat >> shift)
                                       for _, flat in _disks(unique, max_rings)]))
    dep_cells, dep_depth = _depression_cells(cells_src(), needed, shift)
    spacing = h3.average_hexagon_edge_length(res, "m") * math.sqrt(3)
    dep_code, claimed = None, None
    if labels is not None:
        # a depression claims ground around its lowest point, which may lie one coarse cell away
        around = _parents(needed << shift, coarse)
        around = np.unique(np.fromiter(itertools.chain.from_iterable(
            h3i.grid_disk(int(c), 1) for c in around), dtype=np.uint64))
        dep_code, claimed = _claims(deps_src(classes=True), labels, around, coarse, spacing,
                                    dep_cells, order)
    excluded = None
    if exclude_suspect:
        bad, n_bad = _suspect_cells(deps_src(), spacing)
        drop = _isin(dep_cells, bad)
        excluded = {"deeper_than_wide": n_bad, "cells": int(drop.sum())}
        dep_cells, dep_depth = dep_cells[~drop], dep_depth[~drop]
        dep_code = None if dep_code is None else dep_code[~drop]

    ring_u, depth_u, code_u = _rings(unique, dep_cells, dep_depth, max_rings, dep_code)
    ring, depth = ring_u[inverse], depth_u[inverse]
    edges = np.asarray(depth_classes, dtype=float)
    near = ring <= max_rings
    splits = [(np.where(near, np.digitize(np.nan_to_num(depth), edges), -1), len(edges) + 1)]
    if labels is not None:
        code = code_u[inverse]
        splits.append((np.where(near, code, -1), len(order) + 1))

    block_u = unique >> np.uint64(3 * (15 - block_res))
    block_key, block = np.unique(block_u[inverse], return_inverse=True)
    is_obs = (pts["kind"] == "observation").to_numpy()
    n_blocks = len(block_key)
    m_obs, n_obs = _tallies(block[is_obs], n_blocks, ring[is_obs], max_rings,
                            [(c[is_obs], n) for c, n in splits])
    m_ctl, n_ctl = _tallies(block[~is_obs], n_blocks, ring[~is_obs], max_rings,
                            [(c[~is_obs], n) for c, n in splits])
    point = _lift(m_obs.sum(0), n_obs.sum(), m_ctl.sum(0), n_ctl.sum())
    boot = _bootstrap(m_obs, n_obs, m_ctl, n_ctl, n_boot, rng)
    tail = 100 * (1 - ci) / 2
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)       # all-undefined columns give NaN
        low = np.nanpercentile(boot, tail, axis=0) if n_boot else np.full(len(point), np.nan)
        high = np.nanpercentile(boot, 100 - tail, axis=0) if n_boot else np.full(len(point), np.nan)
    undefined = np.isnan(boot).mean(axis=0) if n_boot else np.zeros(len(point))

    k = np.arange(max_rings + 1)
    columns = {"obs_within": m_obs.sum(0), "controls_within": m_ctl.sum(0),
               "obs_share": m_obs.sum(0) / n_obs.sum(), "control_share": m_ctl.sum(0) / n_ctl.sum(),
               "lift": point, "lift_low": low, "lift_high": high}
    whole = slice(0, max_rings + 1)
    rings = pd.DataFrame({"k": k, "distance_m": k * spacing,
                          **{c: v[whole] for c, v in columns.items()}})
    rings[["obs_within", "controls_within"]] = rings[["obs_within", "controls_within"]].astype(int)
    depth_part = slice(max_rings + 1, (max_rings + 1) * (len(edges) + 2))
    by_depth = pd.DataFrame({"depth_class": np.repeat(_class_labels(edges), max_rings + 1),
                             "k": np.tile(k, len(edges) + 1),
                             **{c: v[depth_part] for c, v in columns.items()}})
    by_depth[["obs_within", "controls_within"]] = by_depth[["obs_within",
                                                            "controls_within"]].astype(int)
    by_class = class_ratios = None
    if labels is not None:
        names = [*order, UNCLASSIFIED]
        class_part = slice(depth_part.stop, None)
        by_class = pd.DataFrame({"class": np.repeat(names, max_rings + 1),
                                 "k": np.tile(k, len(names)),
                                 **{c: v[class_part] for c, v in columns.items()}})
        by_class[["obs_within", "controls_within"]] = by_class[["obs_within",
                                                                "controls_within"]].astype(int)
        class_ratios = _ratios(order, point[class_part], boot[:, class_part], max_rings, tail)

    obs_blocks = len(np.unique(block[is_obs]))
    if obs_blocks < 20:
        notes.append(f"The observations fall in only {obs_blocks} blocks (H3 resolution "
                     f"{block_res}): the intervals rest on few independent areas. A finer "
                     "block_res or more observations would help.")
    if len(obs) < 30:
        notes.append(f"Only {len(obs)} observations: the lift is very uncertain.")
    if n_boot and undefined[whole].max() > 0.05:
        notes.append("In some bootstrap samples no control was near depressed ground, so the lift "
                     "was undefined there; its interval is unreliable at those distances.")

    block_ids = np.array([h3.int_to_str(int(h3i.cell_to_parent(int(c), block_res)))
                           for c in unique])[inverse]
    points = pd.DataFrame({
        "point_id": pts["point_id"].to_numpy(), "kind": pts["kind"].to_numpy(),
        "lon": pts["lon"].to_numpy(), "lat": pts["lat"].to_numpy(),
        "h3": [h3.int_to_str(int(c)) for c in cell],
        "rings": pd.array(np.where(ring <= max_rings, ring, -1), dtype="Int64"),
        "depth_m": depth, "block": block_ids})
    points.loc[ring > max_rings, "rings"] = pd.NA
    point_columns = list(POINT_COLUMNS)
    if labels is not None:
        names = np.array([*order, UNCLASSIFIED, None], dtype=object)
        points["class"] = names[np.where(near, code, len(order) + 1)]
        point_columns.append("class")

    report = {
        "result": where, "res": res, "ring_spacing_m": spacing,
        "observations": {"n": len(obs), "outside_area": int((~obs_in).sum()),
                         "missing_coordinates": obs_missing},
        "controls": {"n": len(ctrl), "source": source, "outside_area": int((~ctrl_in).sum()),
                     "missing_coordinates": ctrl_missing},
        "area": area_name, "max_rings": max_rings,
        "depression_cells_near_points": int(len(dep_cells)), "excluded": excluded,
        "rings": _records(rings), "depth_classes_m": [float(e) for e in edges],
        "by_depth": _records(by_depth),
        **({} if labels is None else {
            "classes": {"order": list(order), "depressions_labelled": int(len(labels)), **claimed,
                        "rule": "each labelled depression claims the depressed cells within "
                                "round(sqrt(area_m2 / pi) / ring_spacing_m) rings of its lowest "
                                "point; overlaps go to the most volume_m3 (then the lower id)"},
            "by_class": _records(by_class), "class_ratios": _records(class_ratios)}),
        "bootstrap": {"block_res": block_res, "n_blocks": n_blocks, "n_blocks_with_observations":
                      obs_blocks, "n_boot": n_boot, "ci": ci, "seed": seed,
                      "undefined_share": [float(u) for u in undefined[whole]]},
        "warnings": notes, "package_version": _package_version(),
    }
    for note in notes:
        warnings.warn(note, UserWarning, stacklevel=2)
    return Validation(rings, by_depth, points[point_columns], report, by_class, class_ratios)


def read_points(source):
    """Points as a DataFrame with `point_id`, `lon` and `lat` in degrees.

    `source` is a CSV file with lon/lat, longitude/latitude or lng/lat columns (and optionally an
    `id` column), a vector file in any CRS (GeoPackage, GeoJSON, Shapefile), a DataFrame with
    such columns, a GeoDataFrame, or an (n, 2) array of longitude, latitude. Geometries other
    than points are represented by a point inside them, so building footprints can serve as
    controls. Rows without coordinates are dropped.
    """
    return _points(source)[0]


def random_points(boundary, n, seed=0):
    """`n` points spread evenly by area over `boundary` (lon/lat geometry or vector file)."""
    import shapely

    from .clip import load_boundary

    geom = load_boundary(boundary)
    shapely.prepare(geom)
    rng = seed if isinstance(seed, np.random.Generator) else np.random.default_rng(seed)
    west, south, east, north = geom.bounds
    s0, s1 = math.sin(math.radians(south)), math.sin(math.radians(north))
    lons, lats, tried, got = [], [], 0, 0
    while got < n:
        if tried > 1_000_000 and got < tried * 1e-4:
            raise ValueError("the boundary covers almost none of its bounding box")
        m = int(min(1_000_000, max(1024, 2 * (n - got) * (tried + 1) / (got + 1))))
        # uniform in longitude and in the sine of latitude is uniform by area on the sphere
        lon = rng.uniform(west, east, m)
        lat = np.degrees(np.arcsin(rng.uniform(s0, s1, m)))
        keep = shapely.contains_xy(geom, lon, lat)
        lons.append(lon[keep])
        lats.append(lat[keep])
        tried, got = tried + m, got + int(keep.sum())
    return pd.DataFrame({"point_id": np.arange(n), "lon": np.concatenate(lons)[:n],
                         "lat": np.concatenate(lats)[:n]})


def _points(source):
    """(points, number of rows dropped for missing coordinates)."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        frame = pd.read_csv(path) if path.suffix.lower() == ".csv" else _read_vector(path)
    elif isinstance(source, pd.DataFrame):
        frame = _from_geometry(source) if _is_geo(source) else source
    else:
        arr = np.asarray(source, dtype=float)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError("points must be an (n, 2) array of longitude, latitude")
        frame = pd.DataFrame({"lon": arr[:, 0], "lat": arr[:, 1]})
    cols = {c.lower(): c for c in frame.columns}
    for xn, yn in (("lon", "lat"), ("longitude", "latitude"), ("lng", "lat")):
        if xn in cols and yn in cols:
            break
    else:
        raise ValueError("points need lon/lat, longitude/latitude or lng/lat columns (degrees)")
    ids = frame[cols["id"]].to_numpy() if "id" in cols else np.arange(len(frame))
    out = pd.DataFrame({"point_id": ids,
                        "lon": pd.to_numeric(frame[cols[xn]], errors="coerce").to_numpy(float),
                        "lat": pd.to_numeric(frame[cols[yn]], errors="coerce").to_numpy(float)})
    ok = np.isfinite(out["lon"].to_numpy()) & np.isfinite(out["lat"].to_numpy())
    out = out[ok].reset_index(drop=True)
    if len(out) and (out["lat"].abs().max() > 90 or out["lon"].abs().max() > 180):
        raise ValueError("coordinates outside longitude -180 to 180 or latitude -90 to 90: give "
                         "longitude and latitude in degrees, or a vector file with its CRS")
    return out, int((~ok).sum())


def _is_geo(frame):
    return hasattr(frame, "geometry") and getattr(frame, "crs", None) is not None


def _read_vector(path):
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Reading vector files needs geopandas: "
                          "pip install 'h3-lowpoints[sources]'") from exc
    try:
        frame = gpd.read_file(str(path))
    except Exception as exc:              # each I/O engine raises its own error types
        raise ValueError(f"could not read {path}: {exc}") from exc
    if frame.crs is None:
        raise ValueError(f"{path} has no coordinate reference system")
    return _from_geometry(frame)


def _from_geometry(frame):
    frame = frame[~(frame.geometry.isna() | frame.geometry.is_empty)].to_crs(4326)
    pts = frame.geometry.copy()
    other = (pts.geom_type != "Point").to_numpy()
    if other.any():                   # polygons, lines: a point guaranteed to lie on them
        pts[other] = pts[other].representative_point()
    out = pd.DataFrame({"lon": pts.x.to_numpy(), "lat": pts.y.to_numpy()})
    cols = {c.lower(): c for c in frame.columns}
    if "id" in cols:
        out["id"] = frame[cols["id"]].to_numpy()
    return out


def _source(result, rows):
    """(meta, cell chunks, depression chunks, description) of a result in memory or on disk."""
    if isinstance(result, Result):
        if result.cells is not None:
            return (result.meta, lambda: [result.cells], lambda classes=False: [result.depressions],
                    "in memory")
        result = result.path
    folder = Path(result)
    missing = missing_files(folder)
    if missing:
        raise FileNotFoundError(f"{folder} is missing {', '.join(missing)}")

    def depressions(classes=False):
        columns = (["id", "h3_lowpoint", "area_m2", "volume_m3"] if classes else
                   ["h3_lowpoint", "max_depth_m", "area_m2", "deeper_than_wide"])
        return iter_table(folder, "depressions", rows, columns=columns)
    return (read_meta(folder),
            lambda: iter_table(folder, "cells", rows, columns=["h3", "max_depth_m"]),
            depressions, str(folder))


def _check_settings(res, max_rings, depth_classes, block_res, n_boot, ci):
    if not (isinstance(max_rings, (int, np.integer)) and max_rings >= 0):
        raise ValueError(f"max_rings must be a whole number >= 0, not {max_rings!r}")
    if not 0 <= block_res <= res:
        raise ValueError(f"block_res must be between 0 and the result's resolution {res}, "
                         f"not {block_res}")
    edges = np.asarray(depth_classes, dtype=float)
    if edges.ndim != 1 or (edges <= 0).any() or (np.diff(edges) <= 0).any():
        raise ValueError("depth_classes must be increasing depths above 0")
    if n_boot < 0:
        raise ValueError("n_boot must be >= 0")
    if not 0 < ci < 1:
        raise ValueError("ci must be between 0 and 1")


def _labels(classes, class_order):
    """(labels: Series of class names indexed by depression id, the classes in order)."""
    if isinstance(classes, (str, Path)):
        path = Path(classes)
        classes = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    if isinstance(classes, pd.DataFrame):
        if not {"id", "class"} <= set(classes.columns):
            raise ValueError("a table of classes needs 'id' and 'class' columns")
        classes = pd.Series(classes["class"].to_numpy(), index=classes["id"].to_numpy())
    labels = pd.Series(classes).dropna()
    if not len(labels):
        raise ValueError("classes labels no depression")
    if labels.index.duplicated().any():
        raise ValueError("classes gives some depressions more than one label")
    labels = labels.astype(str)
    found = sorted(labels.unique())
    order = found if class_order is None else [str(c) for c in class_order]
    if len(set(order)) != len(order) or UNCLASSIFIED in order:
        raise ValueError(f"class_order must name each class once, and not {UNCLASSIFIED!r}")
    unknown = sorted(set(found) - set(order))
    if unknown:
        raise ValueError(f"classes not in class_order: {unknown}")
    return labels, order


def _parents(cells, res):
    """The parent at `res` of each H3 cell (or of a key shifted back into place), by its bits."""
    cells = np.asarray(cells, dtype=np.uint64)
    out = (cells & ~np.uint64(0xF << 52)) | np.uint64(res << 52)
    return out | np.uint64((1 << (3 * (15 - res))) - 1)


def _claims(chunks, labels, around, coarse, spacing, dep_cells, order):
    """The position in `order` of each depressed cell's class (len(order) when unclaimed), and
    counts for the report.

    Each labelled depression whose lowest point lies in one of the coarse cells `around` claims
    the cells within round(sqrt(area / pi) / spacing) rings of its lowest point's cell; a cell
    claimed more than once goes to the depression with the most volume, then the lower id.
    """
    code_of = {name: i for i, name in enumerate(order)}
    n_classes = len(order)
    cells, volume, ids, codes = [], [], [], []
    for chunk in chunks:
        if not len(chunk):
            continue
        if "volume_m3" not in chunk or "id" not in chunk:
            raise ValueError("classes need the depressions' id and volume_m3")
        low = ids_to_int(list(chunk["h3_lowpoint"]))
        keep = _isin(_parents(low, coarse), around) & chunk["id"].isin(labels.index).to_numpy()
        if not keep.any():
            continue
        sub = chunk[keep]
        radius = np.rint(np.sqrt(sub["area_m2"].to_numpy(float) / math.pi) / spacing).astype(int)
        disks = [h3i.grid_disk(int(c), int(r)) if r else [int(c)] for c, r in zip(low[keep], radius)]
        sizes = np.fromiter(map(len, disks), dtype=np.int64, count=len(disks))
        cells.append(np.fromiter(itertools.chain.from_iterable(disks), dtype=np.uint64,
                                 count=sizes.sum()))
        volume.append(np.repeat(sub["volume_m3"].to_numpy(float), sizes))
        ids.append(np.repeat(sub["id"].to_numpy(np.int64), sizes))
        codes.append(np.repeat(labels.loc[sub["id"].to_numpy()].to_numpy(), sizes))
    code = np.full(len(dep_cells), n_classes, dtype=np.int64)
    if cells:
        cells, volume, ids = np.concatenate(cells), np.concatenate(volume), np.concatenate(ids)
        names = np.concatenate(codes)
        order = np.lexsort((ids, -np.nan_to_num(volume, nan=-np.inf), cells))
        cells, names = cells[order], names[order]
        first = np.r_[True, cells[1:] != cells[:-1]]
        cells, names = cells[first], names[first]
        pos = np.minimum(np.searchsorted(cells, dep_cells), len(cells) - 1)
        hit = cells[pos] == dep_cells
        code[hit] = [code_of[n] for n in names[pos[hit]]]
    return code, {"depressed_cells_claimed": int((code < n_classes).sum()),
                  "depressed_cells_unclassified": int((code == n_classes).sum())}


def _ratios(order, lift, boot, k, tail):
    """Lift of each class over each later class, per k, with percentile intervals."""
    rows = []
    for a, b in itertools.combinations(range(len(order)), 2):
        for j in range(k + 1):
            ia, ib = a * (k + 1) + j, b * (k + 1) + j
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = lift[ia] / lift[ib]
                samples = boot[:, ia] / boot[:, ib]
            samples = samples[np.isfinite(samples)]
            rows.append({"class_a": order[a], "class_b": order[b], "k": j,
                         "ratio": ratio if np.isfinite(ratio) else np.nan,
                         "ratio_low": np.percentile(samples, tail) if len(samples) else np.nan,
                         "ratio_high": np.percentile(samples, 100 - tail) if len(samples) else np.nan,
                         "undefined_share": (1 - len(samples) / len(boot)) if len(boot) else np.nan})
    return pd.DataFrame(rows, columns=["class_a", "class_b", "k", "ratio", "ratio_low",
                                       "ratio_high", "undefined_share"])


def _area(boundary, meta):
    """(area, its description): a geometry, a (west, south, east, north) box, or None."""
    if boundary is not None:
        from .clip import load_boundary

        return load_boundary(boundary), "boundary"
    bbox = (meta.get("build") or {}).get("bbox")
    if bbox:
        return tuple(bbox), "the result's box (build.bbox in meta.json)"
    return None, None


def _inside(area, pts):
    lon, lat = pts["lon"].to_numpy(), pts["lat"].to_numpy()
    if area is None:
        return np.ones(len(pts), dtype=bool)
    if isinstance(area, tuple):
        west, south, east, north = area
        return (lon >= west) & (lon <= east) & (lat >= south) & (lat <= north)
    import shapely

    shapely.prepare(area)
    return shapely.contains_xy(area, lon, lat)


def _isin(values, sorted_set):
    """Boolean array: is each value in the sorted array `sorted_set`?"""
    if len(sorted_set) == 0:
        return np.zeros(len(values), dtype=bool)
    pos = np.minimum(np.searchsorted(sorted_set, values), len(sorted_set) - 1)
    return sorted_set[pos] == values


def _disks(cells, k):
    """Yield (owner, cell) arrays: every cell within k rings of each of `cells`, by batch."""
    for start in range(0, len(cells), _DISK_BATCH):
        disks = [h3i.grid_disk(int(c), k) for c in cells[start:start + _DISK_BATCH]]
        sizes = np.fromiter(map(len, disks), dtype=np.int64, count=len(disks))
        flat = np.fromiter(itertools.chain.from_iterable(disks), dtype=np.uint64, count=sizes.sum())
        yield np.repeat(np.arange(start, start + len(disks)), sizes), flat


def _depression_cells(chunks, needed, shift):
    """Sorted depression cells (and their depth) whose parent is among `needed`."""
    cells, depth = [], []
    for chunk in chunks:
        if not len(chunk):
            continue
        ids = ids_to_int(list(chunk["h3"]))
        keep = _isin(ids >> shift, needed)
        cells.append(ids[keep])
        depth.append(chunk["max_depth_m"].to_numpy(float)[keep])
    if not cells:
        return np.zeros(0, dtype=np.uint64), np.zeros(0)
    cells, depth = np.concatenate(cells), np.concatenate(depth)
    order = np.argsort(cells)
    return cells[order], depth[order]


def _suspect_cells(chunks, spacing):
    """Sorted cells around depressions flagged deeper_than_wide, and how many were flagged.

    The cell table does not say which depression a cell belongs to, so the ground of a flagged
    depression is approximated: every cell within sqrt(area) of its lowest point's cell.
    """
    out, n = [], 0
    for chunk in chunks:
        if not len(chunk) or "h3_lowpoint" not in chunk:
            continue
        flag = (chunk["deeper_than_wide"].to_numpy(bool) if "deeper_than_wide" in chunk
                else deeper_than_wide(chunk["max_depth_m"], chunk["area_m2"]))
        for low, area in zip(chunk["h3_lowpoint"][flag], chunk["area_m2"][flag]):
            out.extend(h3i.grid_disk(h3.str_to_int(low), max(1, math.ceil(math.sqrt(area) / spacing))))
            n += 1
    return np.unique(np.array(out, dtype=np.uint64)), n


def _grid_distance(a, b, k):
    try:
        return h3i.grid_distance(a, b)
    except Exception:                 # H3 cannot measure across a pentagon: count rings instead
        return next(r for r in range(k + 1) if b in h3i.grid_disk(a, r))


def _rings(cells, dep_cells, dep_depth, k, dep_code=None):
    """For each cell: rings to the nearest depression cell (k + 1 when none within k rings), the
    deepest depth among the nearest ones (NaN when none), and the class code of that deepest
    nearest cell (the lowest code among equals; -1 when none or without `dep_code`)."""
    ring = np.full(len(cells), k + 1, dtype=np.int64)
    depth = np.full(len(cells), np.nan)
    code = np.full(len(cells), -1, dtype=np.int64)
    if len(dep_cells) == 0:
        return ring, depth, code
    dep_code = np.full(len(dep_cells), -1, dtype=np.int64) if dep_code is None else dep_code
    for owner, flat in _disks(cells, k):
        pos = np.minimum(np.searchsorted(dep_cells, flat), len(dep_cells) - 1)
        hit = dep_cells[pos] == flat
        if not hit.any():
            continue
        own, near, d, c = owner[hit], flat[hit], dep_depth[pos[hit]], dep_code[pos[hit]]
        dist = np.fromiter((_grid_distance(int(cells[o]), int(n), k) for o, n in zip(own, near)),
                           dtype=np.int64, count=len(own))
        order = np.lexsort((c, -d, dist, own))         # by cell, nearest, deepest, first class
        own, dist, d, c = own[order], dist[order], d[order], c[order]
        first = np.r_[True, own[1:] != own[:-1]]
        ring[own[first]] = dist[first]
        depth[own[first]] = d[first]
        code[own[first]] = c[first]
    return ring, depth, code


def _tallies(block, n_blocks, ring, k, splits):
    """Per block: points within 0..k rings (overall, then per class of each split), and points.

    `splits` are (class of each point, number of classes) pairs; the columns are the k + 1
    overall counts, then each split's classes, k + 1 columns each.
    """
    near = ring <= k
    parts = []
    for cls, n in splits:
        hits = np.zeros((n_blocks, n, k + 1))
        np.add.at(hits, (block[near], cls[near], ring[near]), 1)
        within = hits.cumsum(axis=2)
        if not parts:
            parts.append(within.sum(axis=1))
        parts.append(within.reshape(n_blocks, -1))
    return np.concatenate(parts, axis=1), np.bincount(block, minlength=n_blocks).astype(float)


def _lift(obs_within, n_obs, ctl_within, n_ctl):
    with np.errstate(divide="ignore", invalid="ignore"):
        lift = (obs_within / n_obs) / (ctl_within / n_ctl)
    return np.where(np.isfinite(lift), lift, np.nan)


def _bootstrap(m_obs, n_obs, m_ctl, n_ctl, n_boot, rng):
    """Lift in `n_boot` samples of whole blocks, drawn with replacement (one row per sample)."""
    n_blocks = len(n_obs)
    batch = max(1, min(200, 2_000_000 // n_blocks))
    out = []
    for start in range(0, n_boot, batch):
        b = min(batch, n_boot - start)
        draws = rng.integers(0, n_blocks, size=(b, n_blocks)) + np.arange(b)[:, None] * n_blocks
        weight = np.bincount(draws.ravel(), minlength=b * n_blocks).reshape(b, n_blocks)
        weight = weight.astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            lift = ((weight @ m_obs) / (weight @ n_obs)[:, None]) / (
                (weight @ m_ctl) / (weight @ n_ctl)[:, None])
        out.append(np.where(np.isfinite(lift), lift, np.nan))
    return np.vstack(out) if out else np.zeros((0, m_obs.shape[1]))


def _class_labels(edges):
    bounds = [0.0, *edges]
    labels = [f"{a:g}-{b:g} m" for a, b in zip(bounds[:-1], bounds[1:])]
    return labels + [f">= {bounds[-1]:g} m"]


def _records(frame):
    """Rows as dicts with plain Python numbers, NaN as None (valid JSON)."""
    out = []
    for row in frame.to_dict("records"):
        out.append({c: (None if isinstance(v, float) and math.isnan(v)
                        else v.item() if hasattr(v, "item") else v) for c, v in row.items()})
    return out
