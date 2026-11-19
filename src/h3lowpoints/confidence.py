"""Confidence grade: is a depression real, or more likely an error of the elevation data?

    from h3lowpoints import analyze
    from h3lowpoints.confidence import confidence, compare, ERROR_3DEP_30M
    result = analyze("dem.tif", breach_crossings=lines, buildings=footprints)
    grades = confidence(result, error_m=ERROR_3DEP_30M, water=water_table)

The grade (A best to D) follows a fixed rule, written down before any data was looked at
(study rules, section 5):

1. Base grade from the depth against the vertical error `e` of the elevation data:
   A if depth >= 2e, B if e <= depth < 2e, C if depth < e.
2. Any flag sets D. The flags are `road_dam` (the depression goes, or is shallower than the
   minimum depth, once road embankments over streams are cut) and `artifact` (a likely building
   artefact, or a bridge line over it).
3. Support raises the grade one level (D to C, C to B, B to A): water seen in at least one year
   (water history class permanent, seasonal or some_years), or flooded in at least one radar scene.

Agreement with a second terrain model (`compare`) is reported beside the grade, as
`terrain_agreement`, and is not part of it, so that the grade can be tested against it.

A check that was not run (no road lines, no buildings, no water history) leaves its column empty
(missing, not False), and `attrs["checks"]` lists the checks that were run.
"""
import numpy as np
import pandas as pd

ERROR_3DEP_30M = 1.0   # m; an assumption: USGS states 0.82 m RMSE for 3DEP 1/3 arc-second, rounded up
ERROR_GLO30 = 2.0      # m; about 1.7 m RMSE against ICESat worldwide, 2.3 m against lidar in a city study
GRADES = ("A", "B", "C", "D")
SUPPORT_CLASSES = ("permanent", "seasonal", "some_years")
CONFIRM_SHARE = 0.25   # a depression is confirmed when the other terrain covers this share of it

GRADE_COLUMNS = ["id", "max_depth_m", "error_m", "base_grade", "road_dam", "building_artifact",
                 "bridge_crossing", "artifact", "seen_wet", "seen_flooded", "support", "grade",
                 "reason", "terrain_agreement"]


def grade(depth_m, error_m, flagged, support):
    """(grade, base grade) letters for each depression, by the rule in the module doc."""
    depth = np.asarray(depth_m, dtype=float)
    e = np.broadcast_to(np.asarray(error_m, dtype=float), depth.shape)
    if not (e > 0).all():
        raise ValueError("error_m must be positive")
    base = np.where(depth >= 2 * e, 0, np.where(depth >= e, 1, 2))      # 0 = A, 1 = B, 2 = C
    level = np.where(np.asarray(flagged, dtype=bool), 3, base)
    level = np.where(np.asarray(support, dtype=bool), np.maximum(level - 1, 0), level)
    letters = np.array(GRADES)
    return letters[level], letters[base]


def _missing(n):
    return pd.Series(pd.NA, index=range(n), dtype="boolean")


def _flag(deps, name):
    """A boolean column of the depression table, or missing values if the check was not run."""
    if name not in deps:
        return _missing(len(deps))
    return deps[name].astype("boolean").reset_index(drop=True)


def _by_id(ids, table, column, test):
    """test(value) for each id from a table keyed by id; missing where the id is not in it."""
    values = pd.Series(ids).map(table.set_index("id")[column])
    out = test(values).astype("boolean")
    out[values.isna()] = pd.NA
    return out


def confidence(result, *, error_m, water=None, floods=None, agreement=None):
    """One row per depression: the grade, and one column per reason (see the module doc).

    result: a `Result` (or its depression table). Uses `max_depth_m`, and `road_dam`,
        `bridge_crossing` and `likely_building_artifact` where the analysis made them.
    error_m: the vertical error of the elevation data in metres (`ERROR_3DEP_30M`,
        `ERROR_GLO30`, or your own figure).
    water: the table from `water.water_history` (uses `water_class`).
    floods: a table with `id` and `scenes_flooded` (radar flood events).
    agreement: the table from `compare` (uses `covered_share`), reported as `terrain_agreement`.
    """
    deps = getattr(result, "depressions", result)
    ids = deps["id"].to_numpy()
    n = len(ids)
    checks = [c for c, col in (("road_dam", "road_dam"), ("bridge_crossing", "bridge_crossing"),
                               ("building_artifact", "likely_building_artifact")) if col in deps]
    road = _flag(deps, "road_dam")
    bridge = _flag(deps, "bridge_crossing")
    building = _flag(deps, "likely_building_artifact")
    artifact = (building.fillna(False) | bridge.fillna(False)).astype("boolean")
    if "bridge_crossing" not in checks and "building_artifact" not in checks:
        artifact = _missing(n)
    wet = _missing(n) if water is None else _by_id(
        ids, water, "water_class", lambda v: v.isin(SUPPORT_CLASSES))
    flooded = _missing(n) if floods is None else _by_id(
        ids, floods, "scenes_flooded", lambda v: v >= 1)
    checks += [c for c, t in (("water", water), ("floods", floods)) if t is not None]
    flagged = (road.fillna(False) | artifact.fillna(False)).to_numpy(bool)
    support = (wet.fillna(False) | flooded.fillna(False)).to_numpy(bool)
    depth = deps["max_depth_m"].to_numpy(float)
    letters, base = grade(depth, error_m, flagged, support)
    reason = [_reason(*v) for v in zip(base, road.fillna(False), building.fillna(False),
                                       bridge.fillna(False), wet.fillna(False),
                                       flooded.fillna(False))]
    if agreement is not None:
        checks.append("terrain")
        terrain = pd.Series(ids).map(agreement.set_index("id")["covered_share"]).to_numpy(float)
    else:
        terrain = np.full(len(ids), np.nan)
    out = pd.DataFrame({"id": ids, "max_depth_m": depth, "error_m": float(error_m),
                        "base_grade": base, "road_dam": road.to_numpy(),
                        "building_artifact": building.to_numpy(), "bridge_crossing": bridge.to_numpy(),
                        "artifact": artifact.to_numpy(), "seen_wet": wet.to_numpy(),
                        "seen_flooded": flooded.to_numpy(), "support": support, "grade": letters,
                        "reason": reason, "terrain_agreement": terrain})
    for c in ("road_dam", "building_artifact", "bridge_crossing", "artifact", "seen_wet",
              "seen_flooded"):
        out[c] = out[c].astype("boolean")
    out.attrs["checks"] = checks
    out.attrs["rule"] = ("A depth >= 2e, B e <= depth < 2e, C depth < e; road_dam or artifact "
                         "sets D; seen wet or seen flooded raises one level; e = error_m")
    return out[GRADE_COLUMNS]


def _reason(base, road, building, bridge, wet, flooded):
    words = [{"A": "depth at least twice the data's error", "B": "depth between one and two times "
              "the data's error", "C": "depth less than the data's error"}[base]]
    words += [w for w, on in (("road embankment over a stream", road),
                              ("likely building artefact", building),
                              ("bridge over it", bridge)) if on]
    words += [w for w, on in (("water seen in at least one year", wet),
                              ("flooded in a radar scene", flooded)) if on]
    return "; ".join(words)


def compare(a, b, *, confirm_share=CONFIRM_SHARE):
    """How well two analyses of the same place agree (for example 30 m against 10 m terrain).

    a, b: `Result`s with labels (in memory, from `analyze`). Both are laid on the finer of the two
    grids (the coarser labels by nearest pixel) and every depression of `a` is matched with the
    depression of `b` that overlaps it most. Returns one row per depression of `a`:

        id, compared_share   share of its area inside the area `b` covers (its area measured
                             on its own grid)
        covered_share        share of that compared part that depressions of `b` cover
        match_id, match_share  the `b` depression overlapping it most, and the share of the
                             compared part it covers
        match_depth_m, depth_diff_m  that depression's depth, and minus this one's
        confirmed            covered_share >= confirm_share

    `attrs["summary"]` gives the counts: matched (confirmed) and missed depressions of `a`
    (missed: not confirmed though at least half of it lies where `b` covers), those mostly outside
    `b`'s area, and extra depressions of `b` (at least half inside `a`'s area, covered less than
    `confirm_share` by depressions of `a`).
    """
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    from .geometry import pixel_areas_m2

    for r in (a, b):
        if r.labels is None:
            raise ValueError("compare needs results with labels (from analyze, not from disk)")
    fine, coarse = (a, b) if _pixel_area(a) <= _pixel_area(b) else (b, a)
    shape = fine.labels.shape

    def onto_fine(r, values):
        if r is fine:
            return values
        out = np.full(shape, -1, dtype="int32")      # -1 = outside the other grid
        reproject(values.astype("int32"), out, src_transform=r.transform, src_crs=r.crs,
                  dst_transform=fine.transform, dst_crs=fine.crs, resampling=Resampling.nearest,
                  src_nodata=None, dst_nodata=-1)
        return np.maximum(out, 0)
    la = onto_fine(a, a.labels).ravel()
    lb = onto_fine(b, b.labels).ravel()
    inside_b = onto_fine(b, np.ones(b.labels.shape, dtype="int32")).ravel() > 0
    inside_a = onto_fine(a, np.ones(a.labels.shape, dtype="int32")).ravel() > 0
    area = np.repeat(pixel_areas_m2(fine.transform, fine.crs, shape[0]), shape[1])
    ids_a = a.depressions["id"].to_numpy()
    ids_b = b.depressions["id"].to_numpy()
    na, nb = int(max(a.labels.max(), 0)) + 1, int(max(b.labels.max(), 0)) + 1
    own_a, own_b = _own_areas(a, na), _own_areas(b, nb)
    in_b = np.bincount(la, weights=area * inside_b, minlength=na)
    both = (la > 0) & (lb > 0)
    cov_a = np.bincount(la[both], weights=area[both], minlength=na)
    pairs = pd.DataFrame({"a": la[both], "b": lb[both], "w": area[both]}).groupby(
        ["a", "b"], as_index=False)["w"].sum()
    best = pairs.sort_values(["a", "w", "b"], ascending=[True, False, True]).drop_duplicates("a")
    best = best.set_index("a")
    with np.errstate(invalid="ignore", divide="ignore"):
        compared = np.minimum(in_b[ids_a] / own_a[ids_a], 1.0)
        covered = np.minimum(cov_a[ids_a] / in_b[ids_a], 1.0)
        match = best["b"].reindex(ids_a)
        match_share = best["w"].reindex(ids_a).to_numpy() / in_b[ids_a]
    depth_b = b.depressions.set_index("id")["max_depth_m"]
    match_depth = match.map(depth_b).to_numpy(float)
    out = pd.DataFrame({"id": ids_a, "compared_share": compared, "covered_share": covered,
                        "match_id": match.astype("Int64").to_numpy(),
                        "match_share": np.nan_to_num(match_share),
                        "match_depth_m": match_depth,
                        "depth_diff_m": match_depth - a.depressions["max_depth_m"].to_numpy(float),
                        "confirmed": np.nan_to_num(covered) >= confirm_share})
    b_in_a = np.bincount(lb, weights=area * inside_a, minlength=nb)
    cov_b = np.bincount(lb[both], weights=area[both], minlength=nb)
    with np.errstate(invalid="ignore", divide="ignore"):
        share_b = np.nan_to_num(cov_b[ids_b] / b_in_a[ids_b])
        b_inside = b_in_a[ids_b] / own_b[ids_b]
    out.attrs["summary"] = {
        "n_a": len(ids_a), "n_b": len(ids_b), "confirm_share": confirm_share,
        "matched": int(out["confirmed"].sum()),
        "missed": int((~out["confirmed"] & (out["compared_share"] >= 0.5)).sum()),
        "outside": int((out["compared_share"] < 0.5).sum()),
        "extra": int(((share_b < confirm_share) & (b_inside >= 0.5)).sum()),
        "median_depth_diff_m": float(np.nanmedian(out.loc[out.confirmed, "depth_diff_m"]))
        if out["confirmed"].any() else None,
        "grid": "finer of the two"}
    return out


def _own_areas(r, n):
    """Area of each label of a result on its own grid."""
    from .geometry import pixel_areas_m2

    h, w = r.labels.shape
    rows = np.repeat(pixel_areas_m2(r.transform, r.crs, h), w)
    return np.bincount(r.labels.ravel(), weights=rows, minlength=n)


def _pixel_area(r):
    from .geometry import pixel_areas_m2

    return float(np.median(pixel_areas_m2(r.transform, r.crs, r.labels.shape[0])))
