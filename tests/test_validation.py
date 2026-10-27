"""Validation against observations: distances, lift and its intervals, on synthetic truth.

Ring distances are checked against a brute-force search, lift against a case worked by hand,
and the whole method against worlds where the answer is known: observations planted in
depressions must give a lift well above 1, observations drawn like the controls a lift near 1
with intervals that cover it, and on clustered observations the block bootstrap must keep its
coverage where resampling single points does not.
"""
import json
import math
import warnings

import h3
import h3.api.basic_int as h3i
import numpy as np
import pandas as pd
import pytest
import shapely

from h3lowpoints import Result, read_result, validate
from h3lowpoints.cli import main
from h3lowpoints.validation import RANDOM_CONTROLS, random_points, read_points
from test_store import FORMATS

RES = 9
BOX = shapely.box(-98, 30, -97, 31)
DEPS = ["id", "max_depth_m", "area_m2", "lat", "lon", "h3_lowpoint", "deeper_than_wide"]


def make_result(depths, flagged=(), res=RES, meta=None):
    """A result whose depressed cells are `depths` {int cell: depth}; `flagged` are lowest-point
    cells of depressions deeper than wide (area 900 m2, depth 50 m)."""
    cells = pd.DataFrame({"h3": [h3.int_to_str(c) for c in depths],
                          "max_depth_m": [float(d) for d in depths.values()]})
    rows = [{"id": i + 1, "max_depth_m": 50.0, "area_m2": 900.0,
             "lat": h3i.cell_to_latlng(c)[0], "lon": h3i.cell_to_latlng(c)[1],
             "h3_lowpoint": h3.int_to_str(c), "deeper_than_wide": True}
            for i, c in enumerate(flagged)]
    deps = pd.DataFrame(rows, columns=DEPS)
    return Result(deps, cells, {"params": {"res": res}, **(meta or {})})


def world(seed, patches=300):
    """Patches of depressed cells scattered over BOX, with random depths."""
    rng = np.random.default_rng(seed)
    depths = {}
    for lon, lat in zip(rng.uniform(-98, -97, patches), rng.uniform(30, 31, patches)):
        for c in h3i.grid_disk(h3i.latlng_to_cell(lat, lon, RES), 2):
            depths[c] = rng.lognormal(0, 0.8)
    return make_result(depths), np.array(list(depths), dtype=np.uint64)


def uniform(rng, n):
    """Points spread evenly by area over BOX."""
    lat = np.degrees(np.arcsin(rng.uniform(math.sin(math.radians(30)), math.sin(math.radians(31)), n)))
    return np.column_stack([rng.uniform(-98, -97, n), lat])


def clustered(rng, n, clusters=40, sd=0.004):
    """Points in tight clusters (about 400 m) around random centres."""
    pts = uniform(rng, clusters)[rng.integers(0, clusters, n)] + rng.normal(0, sd, (n, 2))
    inside = (pts[:, 0] > -98) & (pts[:, 0] < -97) & (pts[:, 1] > 30) & (pts[:, 1] < 31)
    return pts[inside]


def centre(cell):
    lat, lon = h3i.cell_to_latlng(int(cell))
    return [lon, lat]


def quiet(*args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return validate(*args, **kwargs)


# --- distances -------------------------------------------------------------------------------

@pytest.mark.parametrize("max_rings", [0, 1, 4, 12])
def test_rings_and_depths_match_a_brute_force_search(max_rings):
    rng = np.random.default_rng(3)
    origin = h3i.latlng_to_cell(30.5, -97.5, RES)
    near = h3i.grid_disk(origin, 15)
    depths = {int(c): float(rng.uniform(0.2, 3)) for c in rng.choice(near, 40, replace=False)}
    pts = np.array([centre(c) for c in rng.choice(near, 150)]) + rng.normal(0, 1e-4, (150, 2))
    v = quiet(make_result(depths), pts, pts[:20], max_rings=max_rings, n_boot=0)
    for row in v.points.itertuples():
        cell = h3.str_to_int(row.h3)
        dist = {d: h3i.grid_distance(cell, d) for d in depths}
        best = min(dist.values())
        if best > max_rings:
            assert pd.isna(row.rings) and math.isnan(row.depth_m)
        else:
            assert row.rings == best
            assert row.depth_m == max(depths[d] for d, k in dist.items() if k == best)


def test_lift_worked_by_hand():
    a = h3i.latlng_to_cell(30.5, -97.5, RES)
    ring1 = h3i.grid_ring(a, 1)[0]
    far = h3i.grid_ring(a, 5)[0]
    obs = [centre(a), centre(a), centre(ring1), centre(far)]       # within 0: 2/4; within 1: 3/4
    ctl = [centre(a), centre(ring1), centre(far), centre(far)]     # within 0: 1/4; within 1: 2/4
    v = quiet(make_result({a: 1.0}), obs, ctl, max_rings=2, n_boot=50)
    assert list(v.rings["obs_within"]) == [2, 3, 3]
    assert list(v.rings["controls_within"]) == [1, 2, 2]
    assert v.rings["lift"].tolist() == pytest.approx([2.0, 1.5, 1.5])
    assert v.rings["distance_m"][1] == pytest.approx(h3.average_hexagon_edge_length(RES, "m") * 3 ** 0.5)


def test_depth_classes_split_the_overall_counts():
    res, cells = world(0)
    rng = np.random.default_rng(1)
    v = quiet(res, uniform(rng, 500), uniform(rng, 2000), max_rings=3, n_boot=0,
              depth_classes=(0.5, 1, 2))
    assert list(v.by_depth["depth_class"].unique()) == ["0-0.5 m", "0.5-1 m", "1-2 m", ">= 2 m"]
    sums = v.by_depth.groupby("k")[["obs_within", "controls_within"]].sum()
    assert sums["obs_within"].tolist() == v.rings["obs_within"].tolist()
    assert sums["controls_within"].tolist() == v.rings["controls_within"].tolist()
    inside = v.points[v.points["rings"] == 0]                    # the class follows the depth
    assert ((inside["depth_m"] >= 2).sum() ==
            v.by_depth.query("k == 0 and depth_class == '>= 2 m'")[["obs_within", "controls_within"]]
            .to_numpy().sum())


# --- synthetic truth -------------------------------------------------------------------------

def test_a_planted_effect_is_found():
    res, cells = world(0)
    rng = np.random.default_rng(1)
    planted = np.array([centre(c) for c in rng.choice(cells, 200)])
    obs = np.vstack([planted, uniform(rng, 200)])                 # half the reports in depressions
    v = quiet(res, obs, uniform(rng, 4000), boundary=BOX, n_boot=300)
    share = v.rings["control_share"][0]                           # about the depressed share of BOX
    expected = (0.5 + 0.5 * share) / share
    assert v.rings["lift"][0] == pytest.approx(expected, rel=0.15)
    assert v.rings["lift_low"][0] > 3
    assert (v.rings["lift"].diff().dropna() < 0).all()            # the effect fades with distance


def test_no_effect_gives_a_lift_near_one_with_intervals_covering_it():
    lifts, cover = [], []
    for s in range(30):
        rng = np.random.default_rng(100 + s)
        res, _ = world(1000 + s)
        v = quiet(res, uniform(rng, 400), uniform(rng, 4000), boundary=BOX, n_boot=200,
                  max_rings=1, seed=s)
        lifts.append(v.rings["lift"][0])
        cover.append(v.rings["lift_low"][0] <= 1 <= v.rings["lift_high"][0])
    assert np.mean(lifts) == pytest.approx(1, abs=0.1)
    assert np.mean(cover) >= 0.8


def test_blocks_keep_coverage_on_clustered_data_where_single_points_do_not():
    """Planted bug: resampling points one by one (blocks of a single cell) ignores clustering
    and gives intervals that are too narrow."""
    cover = {6: [], RES: []}
    for s in range(40):
        rng = np.random.default_rng(100 + s)
        res, _ = world(1000 + s)
        obs, ctl = clustered(rng, 400), uniform(rng, 4000)
        for block_res in cover:
            v = quiet(res, obs, ctl, boundary=BOX, n_boot=200, max_rings=1, block_res=block_res,
                      seed=s)
            cover[block_res].append(v.rings["lift_low"][0] <= 1 <= v.rings["lift_high"][0])
    blocks, points = np.mean(cover[6]), np.mean(cover[RES])
    assert blocks >= 0.75
    assert blocks >= points + 0.15


def test_same_seed_same_answer_and_the_seed_matters():
    res, _ = world(0)
    rng = np.random.default_rng(1)
    obs, ctl = uniform(rng, 300), uniform(rng, 1000)
    a, b = quiet(res, obs, ctl, seed=5, n_boot=100), quiet(res, obs, ctl, seed=5, n_boot=100)
    c = quiet(res, obs, ctl, seed=6, n_boot=100)
    pd.testing.assert_frame_equal(a.rings, b.rings)
    assert not a.rings["lift_low"].equals(c.rings["lift_low"])
    assert a.rings["lift"].equals(c.rings["lift"])                 # the estimate itself is fixed


# --- results on disk -------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", FORMATS)
def test_a_saved_result_read_in_pieces_gives_the_same_answer(tmp_path, fmt):
    res, cells = world(0)
    rng = np.random.default_rng(1)
    obs = np.vstack([[centre(c) for c in rng.choice(cells, 100)], uniform(rng, 100)])
    ctl = uniform(rng, 1000)
    folder = res.save(tmp_path / "r", format=fmt)
    a = quiet(res, obs, ctl, n_boot=100)
    b = quiet(folder, obs, ctl, n_boot=100, rows=97)
    c = quiet(read_result(folder), obs, ctl, n_boot=100)
    for other in (b, c):
        pd.testing.assert_frame_equal(a.rings, other.rings)
        pd.testing.assert_frame_equal(a.by_depth, other.by_depth)
        pd.testing.assert_frame_equal(a.points, other.points)
    assert b.report["result"] == str(folder)


def test_points_outside_the_area_are_left_out():
    res, _ = world(0)
    rng = np.random.default_rng(1)
    outside = np.array([[-90.0, 30.5], [-97.5, 40.0]])
    obs = np.vstack([uniform(rng, 50), outside])
    ctl = np.vstack([uniform(rng, 200), outside, outside])
    v = quiet(res, obs, ctl, boundary=BOX, n_boot=0)
    assert v.report["observations"]["outside_area"] == 2
    assert v.report["controls"]["outside_area"] == 4
    assert len(v.points) == 250
    assert any("outside the area" in w for w in v.warnings)
    # without a boundary, the box recorded by `build` is used
    boxed = make_result({}, meta={"build": {"bbox": [-98, 30, -97, 31]}})
    v = quiet(boxed, obs, ctl, n_boot=0)
    assert v.report["observations"]["outside_area"] == 2 and "build.bbox" in v.report["area"]
    # without either, nothing is left out, with a warning
    with pytest.warns(UserWarning, match="No boundary"):
        v = validate(make_result({}), obs, ctl, n_boot=0)
    assert v.report["observations"]["n"] == 52


def test_a_clipped_result_asks_for_its_boundary():
    meta = {"build": {"bbox": [-98, 30, -97, 31]}, "clip": {"boundary": "Texas"}}
    rng = np.random.default_rng(1)
    with pytest.warns(UserWarning, match="clipped to Texas"):
        validate(make_result({}, meta=meta), uniform(rng, 50), uniform(rng, 50), n_boot=0)


# --- controls --------------------------------------------------------------------------------

def test_random_controls_are_flagged_and_spread_by_area():
    res, _ = world(0)
    rng = np.random.default_rng(1)
    with pytest.warns(UserWarning, match="random points in the boundary"):
        v = validate(res, uniform(rng, 200), boundary=BOX, n_boot=0)
    assert RANDOM_CONTROLS in v.warnings
    assert v.report["controls"] == {"n": 2000, "source": "random points in the boundary",
                                    "outside_area": 0, "missing_coordinates": 0}
    tall = shapely.box(0, 0, 1, 60)
    pts = random_points(tall, 40000, seed=2)
    assert shapely.contains_xy(tall, pts["lon"], pts["lat"]).all()
    north = (pts["lat"] > 30).mean()                              # equal-area: sin 60 - sin 30 of sin 60
    assert north == pytest.approx((math.sin(math.radians(60)) - 0.5) / math.sin(math.radians(60)),
                                  abs=0.01)
    assert len(random_points(tall, 1000, seed=2)) == 1000


def test_controls_need_a_source():
    with pytest.raises(ValueError, match="give controls"):
        validate(make_result({}), [[-97.5, 30.5]])


# --- suspect depressions ---------------------------------------------------------------------

def test_ground_of_depressions_deeper_than_wide_is_left_out():
    a = h3i.latlng_to_cell(30.5, -97.5, RES)
    b = h3i.latlng_to_cell(30.2, -97.2, RES)
    res = make_result({a: 50.0, b: 1.0}, flagged=[a])
    obs = [centre(a)] * 5 + [centre(b)]
    ctl = [centre(a), centre(b)] + [centre(h3i.grid_ring(b, 8)[0])] * 8
    kept = quiet(res, obs, ctl, exclude_suspect=False, n_boot=0, max_rings=1)
    dropped = quiet(res, obs, ctl, n_boot=0, max_rings=1)
    assert kept.rings["obs_within"][0] == 6
    assert dropped.rings["obs_within"][0] == 1
    assert dropped.report["excluded"] == {"deeper_than_wide": 1, "cells": 1}
    assert kept.report["excluded"] is None


# --- inputs ----------------------------------------------------------------------------------

def test_points_are_read_from_csv_arrays_and_frames(tmp_path):
    csv = tmp_path / "p.csv"
    pd.DataFrame({"ID": ["a", "b", "c"], "Longitude": [-97.5, None, -97.4],
                  "Latitude": [30.5, 30.4, 30.3]}).to_csv(csv, index=False)
    pts = read_points(csv)
    assert pts["point_id"].tolist() == ["a", "c"] and pts["lon"].tolist() == [-97.5, -97.4]
    assert read_points(np.array([[-97.5, 30.5]]))["lat"].tolist() == [30.5]
    assert read_points(pd.DataFrame({"lng": [1.0], "lat": [2.0]}))["lon"].tolist() == [1.0]
    with pytest.raises(ValueError, match="lon/lat"):
        read_points(pd.DataFrame({"x": [1.0], "y": [2.0]}))
    with pytest.raises(ValueError, match="degrees"):
        read_points(np.array([[620000.0, 3350000.0]]))            # projected metres, not degrees


def test_points_are_read_from_vector_files_in_any_crs(tmp_path):
    gpd = pytest.importorskip("geopandas")
    square = shapely.box(-97.51, 30.49, -97.49, 30.51)
    frame = gpd.GeoDataFrame({"id": [7, 8]}, geometry=[square, shapely.Point(-97.4, 30.4)],
                             crs=4326).to_crs(32614)
    path = tmp_path / "buildings.gpkg"
    frame.to_file(path)
    pts = read_points(path)
    assert pts["point_id"].tolist() == [7, 8]
    assert square.contains(shapely.Point(pts["lon"][0], pts["lat"][0]))   # a point inside it
    assert pts["lon"][1] == pytest.approx(-97.4) and pts["lat"][1] == pytest.approx(30.4)
    assert read_points(frame)["point_id"].tolist() == [7, 8]


def test_an_unreadable_vector_file_is_an_input_error(tmp_path, capsys):
    pytest.importorskip("geopandas")
    broken = tmp_path / "cut.geojson"                             # a download cut off midway
    broken.write_text('{"type": "FeatureCollection", "features": [{"type": "Feat')
    with pytest.raises(ValueError, match="could not read"):
        read_points(broken)
    folder = make_result({}).save(tmp_path / "r")
    good = tmp_path / "obs.csv"
    pd.DataFrame({"lon": [-97.5], "lat": [30.5]}).to_csv(good, index=False)
    for args in (["--observations", str(broken), "--controls", str(good)],     # points
                 ["--observations", str(good), "--boundary", str(broken)]):   # boundary
        with pytest.raises(SystemExit, match="error: could not read"):
            main(["validate", str(folder), *args])


def test_bad_settings_are_refused():
    res = make_result({})
    pts = [[-97.5, 30.5]]
    for kwargs, match in [({"block_res": 10}, "block_res"), ({"max_rings": -1}, "max_rings"),
                          ({"depth_classes": (1, 0.5)}, "depth_classes"), ({"ci": 1.0}, "ci"),
                          ({"n_boot": -1}, "n_boot")]:
        with pytest.raises(ValueError, match=match):
            quiet(res, pts, pts, **kwargs)
    with pytest.raises(ValueError, match="no observations"):
        quiet(res, np.zeros((0, 2)), pts)


# --- classes of depressions ------------------------------------------------------------------

SPACING = h3.average_hexagon_edge_length(RES, "m") * math.sqrt(3)
CLASSED = ["id", "max_depth_m", "area_m2", "volume_m3", "lat", "lon", "h3_lowpoint",
           "deeper_than_wide"]


def area_for(rings):
    """A depression area whose equivalent radius is `rings` ring spacings."""
    return math.pi * (rings * SPACING) ** 2


def classed_result(depressions, depths):
    """A result with depressed cells `depths` {cell: depth} and `depressions`, a list of
    (lowest-point cell, area, volume)."""
    rows = [{"id": i + 1, "max_depth_m": 1.0, "area_m2": float(a), "volume_m3": float(v),
             "lat": h3i.cell_to_latlng(c)[0], "lon": h3i.cell_to_latlng(c)[1],
             "h3_lowpoint": h3.int_to_str(c), "deeper_than_wide": False}
            for i, (c, a, v) in enumerate(depressions)]
    cells = pd.DataFrame({"h3": [h3.int_to_str(c) for c in depths],
                          "max_depth_m": [float(d) for d in depths.values()]})
    return Result(pd.DataFrame(rows, columns=CLASSED), cells, {"params": {"res": RES}})


def classed_world(seed, patches=300):
    """Patches of depressed cells over BOX, each one depression (2 rings across its lowest point),
    labelled 'a' or 'b' at random. Returns the result and the labels."""
    rng = np.random.default_rng(seed)
    depths, deps, labels = {}, [], {}
    for i, (lon, lat) in enumerate(zip(rng.uniform(-98, -97, patches), rng.uniform(30, 31, patches))):
        low = h3i.latlng_to_cell(lat, lon, RES)
        for c in h3i.grid_disk(low, 2):
            depths[c] = rng.lognormal(0, 0.8)
        deps.append((low, area_for(2), rng.uniform(1e3, 1e5)))
        labels[i + 1] = "a" if rng.random() < 0.5 else "b"
    return classed_result(deps, depths), pd.Series(labels)


def class_cells(res, labels, name):
    """Depressed cells a point in which is classed `name`, found through validate itself."""
    cells = np.array([h3.str_to_int(c) for c in res.cells["h3"]], dtype=np.uint64)
    pts = np.array([centre(c) for c in cells])
    v = quiet(res, pts, pts, n_boot=0, max_rings=0, classes=labels, class_order=["a", "b"])
    got = v.points[v.points["kind"] == "observation"]
    return cells[(got["class"] == name).to_numpy()]


def test_claims_by_hand_overlaps_unclaimed_ground_and_ties():
    o = h3i.latlng_to_cell(30.5, -97.5, RES)
    big_ring1 = h3i.grid_ring(o, 1)
    small = h3i.grid_ring(o, 2)[0]                  # inside big's 2-ring footprint
    alone = h3i.grid_ring(o, 6)[0]                  # its own small depression
    stray = h3i.grid_ring(o, 9)[0]                  # depressed, but nobody's lowest point is near
    depths = {c: 1.0 for c in h3i.grid_disk(o, 2)} | {alone: 1.0, stray: 1.0}
    deps = [(small, area_for(0.4), 10.0),          # id 1, rounds to 0 rings: its own cell only
            (o, area_for(2), 500.0),               # id 2, claims 2 rings, holds the most water
            (alone, area_for(0.4), 10.0)]          # id 3
    labels = {1: "small", 2: "big", 3: "small"}
    pts = [centre(c) for c in (o, big_ring1[0], small, alone, stray)]
    v = quiet(classed_result(deps, depths), pts, pts, n_boot=0, max_rings=0, classes=labels,
              class_order=["small", "big"])
    got = v.points[v.points["kind"] == "observation"]["class"].tolist()
    assert got == ["big", "big", "big", "small", "unclassified"]   # the most water wins
    assert v.report["classes"]["depressed_cells_claimed"] == 20
    assert v.report["classes"]["depressed_cells_unclassified"] == 1
    # equal volumes: the lower id wins the overlap
    deps[0] = (small, area_for(0.4), 500.0)
    v = quiet(classed_result(deps, depths), pts, pts, n_boot=0, max_rings=0, classes=labels)
    assert v.points["class"].iloc[2] == "small"
    # a point equally near two equally deep cells of different classes takes the earlier class
    between = h3i.grid_ring(o, 4)[0]
    a = h3i.grid_ring(between, 1)[0]
    b = next(c for c in h3i.grid_ring(between, 1) if h3i.grid_distance(a, c) >= 2)
    deps = [(a, area_for(0), 1.0), (b, area_for(0), 1.0)]
    for order, want in ((["x", "y"], "x"), (["y", "x"], "y")):
        v = quiet(classed_result(deps, {a: 1.0, b: 1.0}), [centre(between)], [centre(between)],
                  n_boot=0, max_rings=1, classes={1: "x", 2: "y"}, class_order=order)
        assert v.points["class"].iloc[0] == want and v.points["rings"].iloc[0] == 1


def test_class_ratio_worked_by_hand():
    o = h3i.latlng_to_cell(30.5, -97.5, RES)
    a, b = o, h3i.grid_ring(o, 5)[0]
    far = h3i.grid_ring(o, 12)[0]
    deps = [(a, area_for(0), 1.0), (b, area_for(0), 1.0)]
    obs = [centre(a)] * 3 + [centre(b)] + [centre(far)] * 4       # a: 3/8, b: 1/8
    ctl = [centre(a)] * 2 + [centre(b)] * 2 + [centre(far)] * 6   # a: 2/10, b: 2/10
    v = quiet(classed_result(deps, {a: 1.0, b: 1.0}), obs, ctl, n_boot=50, max_rings=1,
              classes={1: "a", 2: "b"})
    k0 = v.by_class[v.by_class["k"] == 0].set_index("class")
    assert k0.loc["a", "lift"] == pytest.approx((3 / 8) / (2 / 10))
    assert k0.loc["b", "lift"] == pytest.approx((1 / 8) / (2 / 10))
    assert k0.loc["unclassified", "obs_within"] == 0
    ratio = v.class_ratios.query("k == 0").iloc[0]
    assert (ratio["class_a"], ratio["class_b"]) == ("a", "b")
    assert ratio["ratio"] == pytest.approx(3.0)                   # (3 / 2) / (1 / 2)


def test_classes_split_the_overall_counts():
    res, labels = classed_world(0)
    rng = np.random.default_rng(1)
    v = quiet(res, uniform(rng, 500), uniform(rng, 2000), max_rings=3, n_boot=0, classes=labels)
    assert v.by_class["class"].unique().tolist() == ["a", "b", "unclassified"]
    sums = v.by_class.groupby("k")[["obs_within", "controls_within"]].sum()
    assert sums["obs_within"].tolist() == v.rings["obs_within"].tolist()
    assert sums["controls_within"].tolist() == v.rings["controls_within"].tolist()
    near = v.points.dropna(subset=["rings"])
    assert near["class"].notna().all() and v.points.loc[v.points["rings"].isna(), "class"].isna().all()


def test_a_planted_class_effect_is_found_and_no_effect_is_not():
    res, labels = classed_world(0)
    rng = np.random.default_rng(1)
    in_a = class_cells(res, labels, "a")
    obs = np.vstack([[centre(c) for c in rng.choice(in_a, 200)], uniform(rng, 200)])
    v = quiet(res, obs, uniform(rng, 4000), boundary=BOX, n_boot=300, classes=labels)
    r = v.class_ratios.query("k == 0").iloc[0]
    assert r["ratio_low"] > 3
    cover = []
    for s in range(20):
        rng = np.random.default_rng(200 + s)
        v = quiet(res, uniform(rng, 600), uniform(rng, 4000), boundary=BOX, n_boot=200,
                  max_rings=0, seed=s, classes=labels)
        r = v.class_ratios.iloc[0]
        cover.append(r["ratio_low"] <= 1 <= r["ratio_high"])
    assert np.mean(cover) >= 0.75


def test_classes_from_a_file_on_a_saved_result_give_the_same_answer(tmp_path):
    res, labels = classed_world(0)
    rng = np.random.default_rng(1)
    obs, ctl = uniform(rng, 300), uniform(rng, 1000)
    folder = res.save(tmp_path / "r", format="parquet")
    pd.DataFrame({"id": labels.index, "class": labels.to_numpy()}).to_csv(tmp_path / "c.csv",
                                                                          index=False)
    a = quiet(res, obs, ctl, n_boot=100, classes=labels)
    b = quiet(folder, obs, ctl, n_boot=100, rows=97, classes=tmp_path / "c.csv")
    pd.testing.assert_frame_equal(a.by_class, b.by_class)
    pd.testing.assert_frame_equal(a.class_ratios, b.class_ratios)
    pd.testing.assert_frame_equal(a.points, b.points)
    assert a.report["classes"] == b.report["classes"]


def test_bad_classes_are_refused():
    res, labels = classed_world(0)
    pts = [[-97.5, 30.5]]
    for kwargs, match in [({"classes": labels, "class_order": ["a"]}, "not in class_order"),
                          ({"classes": labels, "class_order": ["a", "b", "a"]}, "each class once"),
                          ({"classes": labels, "class_order": ["a", "b", "unclassified"]},
                           "each class once"),
                          ({"classes": pd.DataFrame({"id": [1], "label": ["a"]})}, "'id' and 'class'"),
                          ({"classes": pd.Series(["a", "b"], index=[1, 1])}, "more than one label"),
                          ({"classes": pd.Series([None], index=[1], dtype=object)}, "no depression")]:
        with pytest.raises(ValueError, match=match):
            quiet(res, pts, pts, **kwargs)
    with pytest.raises(ValueError, match="volume_m3"):
        quiet(make_result({h3i.latlng_to_cell(30.5, -97.5, RES): 1.0},
                          flagged=[h3i.latlng_to_cell(30.5, -97.5, RES)]), pts, pts, classes={1: "a"})


# --- outputs ---------------------------------------------------------------------------------

def test_the_report_is_strict_json_and_the_points_table_is_written(tmp_path):
    a = h3i.latlng_to_cell(30.5, -97.5, RES)
    far = h3i.grid_ring(a, 5)[0]
    v = quiet(make_result({a: 1.0}), [centre(far)] * 3, [centre(far)] * 3, n_boot=20,
              max_rings=2)
    path = v.save(tmp_path / "out" / "report.json", points=tmp_path / "points.csv")

    def refuse(name):
        raise AssertionError(f"{name} is not valid JSON")

    report = json.loads(path.read_text(), parse_constant=refuse)   # undefined lifts are null
    assert report["rings"][0]["lift"] is None
    back = pd.read_csv(tmp_path / "points.csv")
    assert list(back.columns) == ["point_id", "kind", "lon", "lat", "h3", "rings", "depth_m", "block"]
    assert back["rings"].isna().all()
    assert "lift" in v.summary()


def test_command_line(tmp_path, capsys):
    res, cells = world(0)
    rng = np.random.default_rng(1)
    folder = res.save(tmp_path / "r")
    obs = np.vstack([[centre(c) for c in rng.choice(cells, 100)], uniform(rng, 100)])
    pd.DataFrame(obs, columns=["lon", "lat"]).to_csv(tmp_path / "obs.csv", index=False)
    pd.DataFrame(uniform(rng, 1000), columns=["lon", "lat"]).to_csv(tmp_path / "ctl.csv", index=False)
    code = main(["validate", str(folder), "--observations", str(tmp_path / "obs.csv"),
                 "--controls", str(tmp_path / "ctl.csv"), "--out", str(tmp_path / "v.json"),
                 "--points", str(tmp_path / "pts.csv"), "--max-rings", "2", "--boot", "50",
                 "--block-res", "5", "--seed", "1"])
    assert code == 0
    report = json.loads((tmp_path / "v.json").read_text())
    assert report["max_rings"] == 2 and report["bootstrap"]["block_res"] == 5
    assert report["rings"][0]["lift"] > 3
    assert len(pd.read_csv(tmp_path / "pts.csv")) == 1200
    out = capsys.readouterr().out
    assert "lift" in out and "report written to" in out


def test_command_line_with_classes(tmp_path, capsys):
    res, labels = classed_world(0)
    rng = np.random.default_rng(1)
    folder = res.save(tmp_path / "r")
    pd.DataFrame(uniform(rng, 300), columns=["lon", "lat"]).to_csv(tmp_path / "obs.csv", index=False)
    pd.DataFrame(uniform(rng, 1000), columns=["lon", "lat"]).to_csv(tmp_path / "ctl.csv", index=False)
    pd.DataFrame({"id": labels.index, "class": labels.to_numpy()}).to_parquet(tmp_path / "c.parquet")
    code = main(["validate", str(folder), "--observations", str(tmp_path / "obs.csv"),
                 "--controls", str(tmp_path / "ctl.csv"), "--out", str(tmp_path / "v.json"),
                 "--points", str(tmp_path / "pts.csv"), "--boot", "50",
                 "--classes", str(tmp_path / "c.parquet"), "--class-order", "b,a"])
    assert code == 0
    report = json.loads((tmp_path / "v.json").read_text())
    assert report["classes"]["order"] == ["b", "a"]
    assert {(r["class_a"], r["class_b"]) for r in report["class_ratios"]} == {("b", "a")}
    assert "class" in pd.read_csv(tmp_path / "pts.csv").columns
    out = capsys.readouterr().out
    assert "By class of the nearest depressed ground" in out and "unclassified" in out
