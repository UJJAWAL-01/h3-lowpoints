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
