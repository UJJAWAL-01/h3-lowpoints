"""Confidence grade: the road-crossing fix, the grade rule exactly as frozen, and compare()."""
import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString

from h3lowpoints import analyze_array
from h3lowpoints.confidence import ERROR_3DEP_30M, compare, confidence, grade
from h3lowpoints.hydro.crossings import breach, line_mask, make_cuts
from h3lowpoints.hydro.depressions import fill_from_array
from h3lowpoints.pipeline import Result

gpd = pytest.importorskip("geopandas")

UTM = "EPSG:32615"
T = from_origin(500000, 3300000, 30, 30)          # 30 m pixels
N = 60


def xy(row, col, t=T):
    """Map coordinates of a pixel centre."""
    return t @ (col + 0.5, row + 0.5)


def layer(*lines, crs=UTM, **cols):
    return gpd.GeoDataFrame(cols, geometry=list(lines), crs=crs)


def valley(embankment=True):
    """A valley draining east along row 30, with a road embankment 4 m high across it at
    columns 29-31: the embankment dams the valley and makes a depression upstream."""
    r, c = np.mgrid[0:N, 0:N].astype(float)
    z = 100 - 0.05 * c + 0.5 * np.abs(r - 30)
    if embankment:
        z[:, 29:32] += 4.0
    return z


STREAM = LineString([xy(30, 0), xy(30, N - 1)])
ROAD = LineString([xy(0, 30), xy(N - 1, 30)])


def run(z, barriers, streams, bridges=None, t=T, crs=UTM, **kw):
    parts = (barriers, streams) if bridges is None else (barriers, streams, bridges)
    return analyze_array(z, t, crs, breach_crossings=parts, min_depth=0.15, res=9, **kw)


# ---------------------------------------------------------------- the cut

def test_embankment_over_a_stream_is_a_road_dam():
    res = run(valley(), layer(ROAD), layer(STREAM))
    deps = res.depressions
    assert len(deps) == 1 and deps.max_depth_m.iloc[0] == pytest.approx(1.4)  # spills west at 100 m
    assert deps.road_dam.iloc[0] and deps.breached_depth_m.iloc[0] < 0.15
    assert not deps.bridge_crossing.iloc[0]
    b = res.meta["breach"]
    assert b["n_crossings"] == 1 and b["n_crossings_cut"] == 1 and b["n_road_dam"] == 1


def test_the_cut_goes_to_stream_level_and_never_raises_ground():
    z = valley()
    invalid = np.zeros(z.shape, bool)
    cut, rec = breach(z, invalid, T, UTM, make_cuts([ROAD], [STREAM], UTM, 100))
    assert (cut <= z).all()
    changed = np.argwhere(cut < z)
    assert set(changed[:, 0]) == {30}                      # only the stream's row
    assert changed[:, 1].min() >= 26 and changed[:, 1].max() <= 34   # within 100 m (3.3 px)
    # level = higher of the lowest ground each side: west side lowest is column 28 (98.6 m)
    assert rec["level_m"][0] == pytest.approx(100 - 0.05 * 28)
    assert cut[30, 30] == pytest.approx(100 - 0.05 * 28)


def test_without_a_stream_nothing_changes():
    res = run(valley(), layer(ROAD), layer(LineString([xy(5, 0), xy(5, 10)])))
    assert not res.depressions.road_dam.iloc[0]
    assert res.depressions.breached_depth_m.iloc[0] == pytest.approx(res.depressions.max_depth_m.iloc[0])
    assert res.meta["breach"]["n_crossings"] == 0


def test_a_natural_basin_is_not_drained():
    r, c = np.mgrid[0:N, 0:N].astype(float)
    z = 100 + 0.01 * ((r - 30) ** 2 + (c - 30) ** 2)       # a bowl: no lower way out
    stream = LineString([xy(30, 5), xy(30, 30)])            # a stream running into it
    road = LineString([xy(10, 20), xy(50, 20)])            # a road across the stream
    res = run(z, layer(road), layer(stream))
    deps = res.depressions
    assert len(deps) == 1 and res.meta["breach"]["n_crossings"] == 1
    assert not deps.road_dam.iloc[0]
    assert deps.breached_depth_m.iloc[0] == pytest.approx(deps.max_depth_m.iloc[0])


def test_a_cut_pit_inside_a_pond_adds_no_depth():
    """Found in the national run (1,461 of 10.5 million): a cut can lower a stream pixel inside a
    depression that still holds water, leaving a pit deeper than the depression. The terrain
    below (rough ground, roads and streams at random, seed 15) has one such case."""
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(15)
    z = 100 + gaussian_filter(rng.normal(size=(N, N)), 1.5) * 10
    mask = gaussian_filter(rng.normal(size=(N, N)), 2) > 0.25
    streams = [LineString([xy(rng.integers(0, N), 0), xy(rng.integers(0, N), N - 1)]) for _ in range(4)]
    roads = [LineString([xy(0, rng.integers(0, N)), xy(N - 1, rng.integers(0, N))]) for _ in range(4)]
    res = run(z, layer(*roads), layer(*streams), mask=mask)
    d = res.depressions
    work, filled, invalid = fill_from_array(z, mask)
    cut, _ = breach(work, invalid, T, UTM, make_cuts(roads, streams, UTM, 100))
    _, filled_cut, _ = fill_from_array(cut, invalid)
    deepest = [(filled_cut - cut)[res.labels == i].max() for i in d.id]
    over = np.asarray(deepest) > d.max_depth_m.to_numpy() + 1e-9
    assert over.sum() == 1                                  # the case is there
    assert (d.breached_depth_m <= d.max_depth_m + 1e-12).all()
    assert np.allclose(d.breached_depth_m[over], d.max_depth_m[over])
    assert (d.road_dam == (d.breached_depth_m < 0.15)).all()


def test_crossings_do_not_depend_on_their_order():
    z = valley()
    z[:, 40:42] += 3.0                                      # a second embankment downstream
    roads = [ROAD, LineString([xy(0, 40.5), xy(N - 1, 40.5)])]
    s2 = LineString([xy(31, 0), xy(31, N - 1)])
    invalid = np.zeros(z.shape, bool)
    a, _ = breach(z, invalid, T, UTM, make_cuts(roads, [STREAM, s2], UTM))
    b, _ = breach(z, invalid, T, UTM, make_cuts(roads[::-1], [s2, STREAM], UTM))
    assert np.array_equal(a, b)


def test_geographic_grid_and_lines_in_another_crs():
    t = from_origin(-95.0, 30.0, 1 / 3600, 1 / 3600)       # 1 arc-second, about 27 x 31 m
    z = valley()
    stream = LineString([xy(30, 0, t), xy(30, N - 1, t)])
    road = LineString([xy(0, 30, t), xy(N - 1, 30, t)])
    lines = gpd.GeoDataFrame(geometry=[road, stream], crs="EPSG:4326").to_crs(UTM)
    res = run(z, lines.iloc[[0]], lines.iloc[[1]], t=t, crs="EPSG:4326")
    assert res.depressions.road_dam.iloc[0]


def test_bridge_line_over_a_depression_is_flagged():
    res = run(valley(), layer(ROAD), layer(STREAM),
              bridges=layer(LineString([xy(20, 25), xy(40, 25)])))
    assert res.depressions.bridge_crossing.iloc[0]
    assert res.meta["breach"]["n_bridge_crossing"] == 1


def test_osm_lines_are_split_by_the_fixed_classes():
    from h3lowpoints.sources.osm import split_lines

    lines = layer(ROAD, STREAM, ROAD, ROAD, ROAD,
                  highway=["residential", None, "footway", "primary", "motorway_link"],
                  waterway=[None, "stream", None, None, None],
                  railway=[None, None, None, None, None],
                  bridge=[None, None, None, "yes", None],
                  tunnel=[None, "culvert", None, None, "yes"])
    barriers, streams, bridges = split_lines(lines)
    assert list(barriers.index) == [0, 3] and list(streams.index) == [1]
    assert list(bridges.index) == [3]


def test_line_mask_marks_the_pixels_a_line_crosses():
    mask = line_mask([LineString([xy(2, 1), xy(2, 7)])], T, (10, 10))
    assert set(map(tuple, np.argwhere(mask))) == {(2, c) for c in range(1, 8)}


# ---------------------------------------------------------------- the grade rule (frozen)

def test_base_grade_boundaries():
    e = 1.0
    letters, base = grade([2.0, 1.999, 1.0, 0.999, 0.2], e, [False] * 5, [False] * 5)
    assert list(base) == ["A", "B", "B", "C", "C"] and list(letters) == list(base)


def test_any_flag_sets_d_and_support_raises_one_level():
    depth = [3.0, 3.0, 1.5, 0.5, 3.0, 0.5]
    flagged = [True, True, False, False, False, False]
    support = [False, True, True, True, True, False]
    letters, _ = grade(depth, 1.0, flagged, support)
    assert list(letters) == ["D", "C", "A", "B", "A", "C"]


def test_grade_uses_the_stated_error():
    letters, _ = grade([3.0], 2.0, [False], [False])          # GLO-30: 3 m is B, not A
    assert letters[0] == "B"
    with pytest.raises(ValueError):
        grade([1.0], 0.0, [False], [False])


def test_confidence_table_reasons_and_missing_checks():
    deps = pd.DataFrame({"id": [1, 2, 3, 4], "max_depth_m": [2.5, 0.4, 1.2, 1.2],
                         "road_dam": [False, False, True, False],
                         "bridge_crossing": [False, False, False, False]})
    water = pd.DataFrame({"id": [1, 2, 3], "water_class": ["never_seen_wet", "seasonal",
                                                           "some_years"]})
    floods = pd.DataFrame({"id": [4], "scenes_flooded": [2]})
    g = confidence(deps, error_m=ERROR_3DEP_30M, water=water, floods=floods)
    assert list(g.grade) == ["A", "B", "C", "A"]
    assert list(g.base_grade) == ["A", "C", "B", "B"]
    assert g.seen_wet.isna().tolist() == [False, False, False, True]
    assert g.building_artifact.isna().all()                   # no buildings given: not checked
    assert "road embankment over a stream" in g.reason[2]
    assert "flooded in a radar scene" in g.reason[3]
    assert g.attrs["checks"] == ["road_dam", "bridge_crossing", "water", "floods"]
    assert g.terrain_agreement.isna().all()


def test_confidence_without_any_checks_is_the_base_grade():
    deps = pd.DataFrame({"id": [1, 2], "max_depth_m": [2.0, 0.5]})
    g = confidence(deps, error_m=1.0)
    assert list(g.grade) == ["A", "C"] and g.road_dam.isna().all() and g.artifact.isna().all()


# ---------------------------------------------------------------- compare

def _result(labels, t, depths):
    ids = sorted(int(v) for v in np.unique(labels) if v > 0)
    deps = pd.DataFrame({"id": ids, "max_depth_m": [depths[i] for i in ids]})
    return Result(deps, None, {}, labels=labels, transform=t, crs=UTM)


def test_compare_matches_misses_and_extras():
    t30, t10 = from_origin(0, 900, 30, 30), from_origin(0, 900, 10, 10)
    a = np.zeros((10, 10), "int32")
    a[1:3, 1:3] = 1                                  # 60 x 60 m
    a[6:8, 6:8] = 2                                  # nothing under it at 10 m
    b = np.zeros((30, 30), "int32")
    b[3:9, 3:6] = 5                                  # left half of depression 1
    b[24:27, 3:6] = 7                                # where 30 m has nothing
    out = compare(_result(a, t30, {1: 1.0, 2: 0.6}), _result(b, t10, {5: 1.4, 7: 0.3}))
    one, two = out.set_index("id").loc[1], out.set_index("id").loc[2]
    assert one.covered_share == pytest.approx(0.5) and one.confirmed and one.match_id == 5
    assert one.depth_diff_m == pytest.approx(0.4) and one.compared_share == pytest.approx(1.0)
    assert two.covered_share == 0 and not two.confirmed and pd.isna(two.match_id)
    s = out.attrs["summary"]
    assert (s["matched"], s["missed"], s["extra"], s["outside"]) == (1, 1, 1, 0)


def test_compare_is_the_same_whichever_grid_is_finer():
    t = from_origin(0, 900, 30, 30)
    a = np.zeros((10, 10), "int32")
    a[2:5, 2:5] = 1
    b = np.zeros((10, 10), "int32")
    b[2:5, 2:4] = 3
    out = compare(_result(a, t, {1: 1.0}), _result(b, t, {3: 1.0}))
    assert out.covered_share.iloc[0] == pytest.approx(6 / 9)


def test_compare_with_a_second_terrain_covering_only_part_of_the_area():
    t30, t10 = from_origin(0, 300, 30, 30), from_origin(0, 300, 10, 10)
    a = np.zeros((10, 10), "int32")
    a[2:4, 4:6] = 1                                  # x 120-180 m: half inside b (0-150 m)
    a[6:8, 7:9] = 2                                  # wholly outside b
    b = np.zeros((30, 15), "int32")                  # covers x 0-150 m only
    b[6:12, 12:15] = 4                               # all of depression 1's inside part
    out = compare(_result(a, t30, {1: 1.0, 2: 1.0}), _result(b, t10, {4: 1.2})).set_index("id")
    assert out.loc[1].compared_share == pytest.approx(0.5)
    assert out.loc[1].covered_share == pytest.approx(1.0) and out.loc[1].confirmed
    assert out.loc[2].compared_share == 0 and not out.loc[2].confirmed
    s = out.attrs["summary"]
    assert (s["matched"], s["missed"], s["outside"], s["extra"]) == (1, 0, 1, 0)
