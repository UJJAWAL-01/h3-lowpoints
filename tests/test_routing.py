"""Where water goes: catchments and spill links, checked against an independent implementation.

`oracle` applies the rules of `hydro.routing` (docs/definition.md, "Catchments and spill") one
pixel at a time in plain Python: its own steepest-neighbour search, a separate breadth-first
search from every flat pixel, recursive following of water paths and spill chains. The library
must agree with it exactly, on smooth terrain, on terrain rounded into flats and ties, with
nodata, and on longitude/latitude grids. Deliberately wrong variants of the rules must disagree,
so the comparison is known to be sensitive to the details that matter.
"""
import math
from collections import deque

import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin
from scipy import ndimage

from h3lowpoints import analyze_array, read_result
from h3lowpoints.geometry import pixel_areas_m2
from h3lowpoints.hydro.depressions import EIGHT, fill_from_array, label_depressions
from h3lowpoints.hydro.routing import OUTLET, pixel_spacing_m, route
from h3lowpoints.pipeline import CATCHMENT_COLUMNS, CATCHMENT_TABLE
from test_store import FORMATS

NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
UTM = ("EPSG:32614", from_origin(500000, 3350000, 1, 1))
GEO = ("EPSG:4326", from_origin(-98.0, 45.0, 0.0003, 0.0002))   # pixels wider than tall


def oracle(z, valid, filled, sink, kept, dx, dy, ties="first", slope=True, ground=True):
    """The routing rules, pixel by pixel. The keyword arguments switch on deliberate mistakes:
    ties="last" breaks ties the wrong way, slope=False ignores distance, ground=False routes on
    the filled surface instead of the ground."""
    h, w = z.shape
    surface = z if ground else filled
    order = list(range(8)) if ties == "first" else list(range(7, -1, -1))

    def inside(r, c):
        return 0 <= r < h and 0 <= c < w

    def dist(r, k):
        dr, dc = NB[k]
        return math.hypot(dx[r], dy[r]) if dr and dc else (dy[r] if dr else dx[r])

    outlet = {(r, c) for r in range(h) for c in range(w) if valid[r, c] and (
        r in (0, h - 1) or c in (0, w - 1)
        or any(not valid[r + dr, c + dc] for dr, dc in NB))}

    def steepest(r, c, height, level):
        best, value = None, 0.0
        for k in order:
            rr, cc = r + NB[k][0], c + NB[k][1]
            if inside(rr, cc) and valid[rr, cc]:
                v = (level - height[rr, cc]) / (dist(r, k) if slope else 1.0)
                if v > value:
                    best, value = (rr, cc), v
        return best

    plain = {(r, c) for r in range(h) for c in range(w)
             if valid[r, c] and sink[r, c] == 0 and (r, c) not in outlet}
    down = {p: steepest(*p, surface, surface[p]) for p in plain}   # None: on a flat
    rcv = dict(down)

    def flat_mate(p, q):
        return valid[q] and sink[q] == 0 and surface[q] == surface[p]

    def is_exit(q):
        return q in outlet or down.get(q) is not None

    memo = {}

    def steps(p):                       # breadth-first from this pixel to the nearest way off
        if p in memo:
            return memo[p]
        if is_exit(p):
            return memo.setdefault(p, 0)
        seen, todo = {p}, deque([(p, 0)])
        while todo:
            q, d = todo.popleft()
            for dr, dc in NB:
                n = (q[0] + dr, q[1] + dc)
                if inside(*n) and n not in seen and flat_mate(q, n):
                    if is_exit(n):
                        return memo.setdefault(p, d + 1)
                    seen.add(n)
                    todo.append((n, d + 1))
        raise AssertionError(f"flat pixel {p} has no way off")

    for p in plain:
        if down[p] is None:
            s = steps(p)
            rcv[p] = next(n for k in order
                          for n in [(p[0] + NB[k][0], p[1] + NB[k][1])]
                          if inside(*n) and flat_mate(p, n) and steps(n) == s - 1)

    def first(p):
        while True:
            if sink[p]:
                return int(sink[p])
            if p in outlet:
                return OUTLET
            p = rcv[p]

    n = int(sink.max())
    spill_pixel, spill_first = {}, {}
    for s in range(1, n + 1):
        start = tuple(np.argwhere(sink == s)[0])
        level = filled[start]
        region, todo = {start}, [start]
        while todo:
            q = todo.pop()
            for dr, dc in NB:
                m = (q[0] + dr, q[1] + dc)
                if inside(*m) and valid[m] and m not in region and filled[m] == level:
                    region.add(m)
                    todo.append(m)
        exits = [q for q in region if q in outlet or any(
            inside(q[0] + dr, q[1] + dc) and valid[q[0] + dr, q[1] + dc]
            and filled[q[0] + dr, q[1] + dc] < level for dr, dc in NB)]
        e = min(exits, key=lambda q: q[0] * w + q[1])
        spill_pixel[s] = e[0] * w + e[1]
        spill_first[s] = OUTLET if e in outlet else first(steepest(*e, filled, level))

    def final(t):
        while t != OUTLET and not kept[t]:
            t = spill_first[t]
        return t

    out = np.full((h, w), -1)
    for r in range(h):
        for c in range(w):
            if valid[r, c]:
                out[r, c] = final(first((r, c)))
    spill_to = {s: final(spill_first[s]) for s in range(1, n + 1)}
    return out, spill_pixel, spill_to


def inputs(z, invalid=None, min_depth=0.3):
    ground, filled, inv = fill_from_array(z, invalid)
    depth = np.where(inv, 0.0, filled - ground)
    sink, n = ndimage.label(depth > 0, structure=EIGHT)
    _, st = label_depressions(depth, 1.0, min_depth)
    kept = np.zeros(n + 1, dtype=bool)
    kept[np.asarray(st["id"], dtype=int)] = True
    return ground, ~inv, filled, sink, kept


def terrain(seed, shape=(24, 30), rounding=None, holes=False):
    rng = np.random.default_rng(seed)
    z = ndimage.gaussian_filter(rng.normal(size=shape), 1.5) * 10 + np.linspace(0, 2, shape[1])
    if rounding:
        z = np.round(z / rounding) * rounding          # flats and ties everywhere
    invalid = None
    if holes:
        invalid = np.zeros(shape, dtype=bool)
        invalid[5:9, 12:17] = True
    return z, invalid


CASES = [dict(seed=s) for s in range(4)] + [dict(seed=s, rounding=1.0) for s in range(4)] + [
    dict(seed=s, rounding=0.5, holes=True) for s in range(3)]


@pytest.mark.parametrize("case", CASES, ids=lambda c: "-".join(f"{k}{v}" for k, v in c.items()))
@pytest.mark.parametrize("grid", [UTM, GEO], ids=["projected", "lonlat"])
def test_routing_matches_the_pixel_by_pixel_oracle(case, grid):
    crs, transform = grid
    z, invalid = terrain(**case)
    ground, valid, filled, sink, kept = inputs(z, invalid)
    got = route(ground, valid, filled, sink, kept, transform, crs)
    dx, dy = pixel_spacing_m(transform, crs, z.shape[0])
    want, spill_pixel, spill_to = oracle(ground, valid, filled, sink, kept, dx, dy)
    np.testing.assert_array_equal(got["final"], want)
    for s in spill_pixel:
        assert got["spill_pixel"][s] == spill_pixel[s]
        assert got["spill_to"][s] == spill_to[s]


@pytest.mark.parametrize("mistake", [{"ties": "last"}, {"slope": False}, {"ground": False}])
def test_the_oracle_tells_wrong_rules_apart(mistake):
    """Planted bugs: each wrong variant of the rules changes the answer on some terrain."""
    differs = False
    for case in CASES:
        z, invalid = terrain(**case)
        ground, valid, filled, sink, kept = inputs(z, invalid)
        dx, dy = pixel_spacing_m(GEO[1], GEO[0], z.shape[0])
        right = oracle(ground, valid, filled, sink, kept, dx, dy)
        wrong = oracle(ground, valid, filled, sink, kept, dx, dy, **mistake)
        if not np.array_equal(right[0], wrong[0]) or right[2] != wrong[2]:
            differs = True
            break
    assert differs


def two_bowls():
    """Bowl A (west) overflows through a notch at 9.6 into bowl B, which overflows at 9.2 to the
    map edge; a shallow pit (3 cm) on the slope south of A fills at once and passes its water
    on down the slope into A."""
    z = np.full((30, 50), 10.0)
    z[5:25, 5:22] = 9.0
    z[5:25, 23:45] = 8.5
    z[12:16, 22] = 9.6
    z[12:16, 45:50] = 9.2
    for row, height in zip(range(25, 30), (9.7, 9.75, 9.85, 9.9, 9.95)):
        z[row, 5:22] = height                # a slope falling north, from the map edge into A
    z[27, 12] = 9.72                         # the pit: 3 cm below its lowest neighbour (row 26)
    return z


def test_two_bowls_worked_by_hand():
    r = analyze_array(two_bowls(), *UTM[::-1], res=12, min_depth=0.1, catchments=True)
    d = r.depressions.set_index("id")
    a, b = d.index[d["lon"] < d["lon"].median()][0], d.index[d["lon"] >= d["lon"].median()][0]
    assert d.loc[a, "spill_to"] == b and d.loc[b, "spill_to"] == OUTLET
    assert len(d) == 2                                   # the 5 cm pit is not a depression
    for i in (a, b):
        assert d.loc[i, "catchment_area_m2"] >= d.loc[i, "area_m2"]
    # the spill points are on the notches
    assert d.loc[a, "spill_lon"] < d.loc[b, "spill_lon"]
    ground, valid, filled, sink, kept = inputs(two_bowls(), min_depth=0.1)
    out = route(ground, valid, filled, sink, kept, UTM[1], UTM[0])
    assert sink[27, 12] > 0 and not kept[sink[27, 12]]   # a sink, too shallow to keep
    assert out["final"][27, 12] == a                     # it passes its water on to A
    assert (out["final"][25:29, 5:22] == a).all()         # and so does the whole slope


@pytest.mark.parametrize("grid", [UTM, GEO], ids=["projected", "lonlat"])
def test_every_pixel_is_accounted_for_and_spills_only_go_down(grid):
    crs, transform = grid
    z, invalid = terrain(7, shape=(60, 70), rounding=0.5, holes=True)
    r = analyze_array(z, transform, crs, res=10, min_depth=0.3, catchments=True,
                      nodata=None, mask=invalid)
    d = r.depressions.set_index("id")
    ground, valid, filled, sink, kept = inputs(z, invalid)
    out = route(ground, valid, filled, sink, kept, transform, crs)
    areas = np.broadcast_to(pixel_areas_m2(transform, crs, z.shape[0])[:, None], z.shape)
    to_outlet = areas[out["final"] == OUTLET].sum()
    assert d["catchment_area_m2"].sum() + to_outlet == pytest.approx(areas[valid].sum(), rel=1e-12)
    assert (d["catchment_area_m2"] >= d["area_m2"] * (1 - 1e-12)).all()
    level = {s: filled[sink == s].max() for s in d.index}
    for s, t in d["spill_to"].items():
        assert t != s
        if t != OUTLET:
            assert level[t] < level[s]
    sums = r.catchments.groupby("id")["area_m2"].sum()
    np.testing.assert_allclose(sums.reindex(d.index, fill_value=0), d["catchment_area_m2"], rtol=1e-12)


def test_without_catchments_nothing_changes():
    z, _ = terrain(1, shape=(40, 50))
    plain = analyze_array(z, *UTM[::-1], res=12, min_depth=0.3)
    routed = analyze_array(z, *UTM[::-1], res=12, min_depth=0.3, catchments=True)
    assert plain.catchments is None
    assert not set(CATCHMENT_COLUMNS) & set(plain.depressions.columns)
    pd.testing.assert_frame_equal(plain.depressions, routed.depressions.drop(columns=CATCHMENT_COLUMNS))
    pd.testing.assert_frame_equal(plain.cells, routed.cells)
    assert list(routed.catchments.columns) == CATCHMENT_TABLE


@pytest.mark.parametrize("fmt", FORMATS)
def test_catchments_are_saved_and_read_back(tmp_path, fmt):
    z, _ = terrain(2, shape=(40, 50))
    r = analyze_array(z, *UTM[::-1], res=12, min_depth=0.3, catchments=True)
    back = read_result(r.save(tmp_path / "out", format=fmt))
    pd.testing.assert_frame_equal(back.catchments, r.catchments, check_dtype=False)
    pd.testing.assert_frame_equal(back.depressions, r.depressions, check_dtype=False)
    assert back.depressions["spill_to"].dtype == np.int64
    plain = analyze_array(z, *UTM[::-1], res=12, min_depth=0.3)
    assert read_result(plain.save(tmp_path / "plain", format=fmt)).catchments is None


def test_no_depressions_gives_an_empty_catchment_table():
    r = analyze_array(np.tile(np.linspace(10, 0, 30), (20, 1)), *UTM[::-1], res=12, catchments=True)
    assert len(r.depressions) == 0 and len(r.catchments) == 0
    assert list(r.catchments.columns) == CATCHMENT_TABLE
