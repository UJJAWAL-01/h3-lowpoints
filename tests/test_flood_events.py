"""Flood evidence: GFM coding, the catalogue search, flood_events rules and flood maps in
validate. Synthetic data with known answers; no network."""
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze_array
from h3lowpoints.floods import flood_events, judge
from h3lowpoints.sources import gfm
from h3lowpoints.validation import flood_map_points, validate_flood_map

UTM = "EPSG:32615"
T10 = from_origin(500000, 3300000, 10, 10)


def write(path, arr, transform=T10, crs=UTM, nodata=255):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype=arr.dtype, crs=crs, transform=transform, nodata=nodata) as dst:
        dst.write(arr, 1)
    return path


# ---------------------------------------------------------------- GFM coding and reading

def test_code_counts_masked_and_reference_water_as_not_seen():
    flood = np.array([1, 0, 1, 1, 1, 0, 255], "uint8")
    excl = np.array([0, 0, 1, 0, 0, 0, 0], "uint8")
    ref = np.array([0, 0, 0, 1, 2, 0, 0], "uint8")
    assert gfm.code(flood, excl, ref).tolist() == [1, 0, 255, 255, 255, 0, 255]
    excl[5] = 255                                       # no data in a layer: not seen
    assert gfm.code(flood, excl, ref)[5] == 255


def test_search_pages_filters_and_sorts():
    def feature(i, px):
        return {"id": f"ITEM_{i}", "properties": {"datetime": f"2017-08-{20 + i}T00:00:00Z",
                                                  "Equi7Tile": "T", "flooded_pixels": px},
                "geometry": {"type": "Point", "coordinates": [-95, 30]},
                "assets": {v: {"href": f"{k}{i}.tif"} for k, v in gfm.LAYERS.items()}}
    pages = [{"features": [feature(3, 30000), feature(1, 50000)],
              "links": [{"rel": "next", "href": "https://x/search", "body": {"token": "p2"}}]},
             {"features": [feature(2, 26000)], "links": []}]
    bodies = []

    def post(url, body):
        bodies.append(body)
        return pages[len(bodies) - 1]
    out = gfm.search((-96, 29, -95, 30), "2017-08-20", "2017-08-31", min_flooded_pixels=25000,
                     post=post)
    assert list(out.item) == ["ITEM_1", "ITEM_2", "ITEM_3"]
    assert bodies[0]["query"] == {"flooded_pixels": {"gte": 25000}}
    assert bodies[0]["datetime"] == "2017-08-20T00:00:00Z/2017-08-31T23:59:59Z"
    assert bodies[1] == {"token": "p2"} and out.flood.iloc[0] == "flood1.tif"


def test_read_scene_cuts_a_box_and_misses_cleanly(tmp_path):
    t = from_origin(500000, 3300000, 20, 20)
    flood = np.zeros((50, 50), "uint8")
    flood[10:20, 10:20] = 1
    excl = np.zeros((50, 50), "uint8")
    excl[10:20, 15:20] = 1
    ref = np.zeros((50, 50), "uint8")
    item = {k: str(write(tmp_path / f"{k}.tif", a, t)) for k, a in
            (("flood", flood), ("exclusion", excl), ("reference", ref))}
    box = (500000 + 200, 3300000 - 400, 500000 + 400, 3300000 - 200)   # columns/rows 10-19
    codes, tr, crs = gfm.read_scene(item, box, crs=UTM)
    # the window covers the box with a one-pixel margin (rows/columns 9-20)
    assert codes.shape == (12, 12) and tr.c == 500180 and tr.f == 3299820
    inner = codes[1:11, 1:11]
    assert (inner[:, :5] == 1).all() and (inner[:, 5:] == 255).all()
    assert (codes[0] == 0).all() and (codes[:, 0] == 0).all()
    assert gfm.read_scene(item, (600000, 3000000, 600100, 3000100), crs=UTM)[0] is None


# ---------------------------------------------------------------- the rules

def test_judge_thresholds():
    area = np.array([4000.0, 4000.0, 4000.0, 4000.0, 400.0])
    seen = np.array([5, 4, 10, 10, 1])
    flooded = np.array([1, 2, 1, 2, 1])
    o_share, f_share, observed, fl = judge(area, 400.0 * seen, seen, flooded)
    assert observed.tolist() == [True, False, True, True, True]   # 50 % of the area
    assert fl.tolist() == [False, False, False, True, False]      # 10 % and 2 pixels
    assert o_share[4] == 1.0


def two_pits():
    """Two 5 x 5 pits (1 m deep) in flat ground, 10 m pixels: ids 1 (west) and 2 (east)."""
    z = np.full((30, 40), 10.0)
    z[10:15, 5:10] = 9.0
    z[10:15, 25:30] = 9.0
    return analyze_array(z, T10, UTM, min_depth=0.15, res=11)


def test_flood_events_with_user_maps(tmp_path):
    res = two_pits()
    ids = res.depressions.sort_values("lon").id.tolist()
    m1 = np.zeros((30, 40), "uint8")
    m1[10:15, 5:8] = 1                                  # west pit: 15 of 25 pixels flooded
    m1[10:15, 25:30] = 255                              # east pit: masked, never "dry"
    m2 = np.zeros((30, 40), "uint8")
    m2[12, 25] = 1                                      # east pit: 1 pixel, below the minimum
    summary, seen = flood_events(res, {"a": write(tmp_path / "a.tif", m1),
                                       "b": write(tmp_path / "b.tif", m2)})
    s = summary.set_index("id")
    west, east = s.loc[ids[0]], s.loc[ids[1]]
    assert (west.scenes_observed, west.scenes_flooded) == (2, 1)
    assert west.flooded_scenes == "a" and west.max_flooded_share == pytest.approx(0.6)
    assert (east.scenes_covered, east.scenes_observed, east.scenes_flooded) == (2, 1, 0)
    assert set(seen.scene) == {"a", "b"} and seen.observed.all()
    assert len(seen[(seen.scene == "a")]) == 1               # the east pit was not observed in a


def test_flood_events_on_a_coarser_map_in_another_crs(tmp_path):
    res = two_pits()
    # the same ground on a 20 m grid in lon/lat-free form: a coarser projected grid
    m = np.zeros((15, 20), "uint8")
    m[5:8, 2:5] = 1                                     # covers the west pit
    summary, _ = flood_events(res, {"coarse": write(tmp_path / "c.tif", m,
                                                    from_origin(500000, 3300000, 20, 20))})
    s = summary.sort_values("id")
    assert s.scenes_flooded.sum() == 1


def test_flood_events_with_catalogue_rows_and_a_reader():
    res = two_pits()
    scenes = pd.DataFrame({"item": ["S1"], "datetime": ["2017-08-30T12:22:03Z"],
                           "flood": ["f"], "exclusion": ["e"], "reference": ["r"]})
    codes = np.zeros((30, 40), "uint8")
    codes[10:15, 25:30] = 1

    summary, seen = flood_events(res, scenes, reader=lambda item: (codes, T10, UTM))
    east = summary.sort_values("id").iloc[-1]
    assert east.scenes_flooded == 1 and east.first_flooded == "2017-08-30T12:22:03Z"
    assert "Copernicus Emergency Management Service information 2017" in \
        summary.attrs["settings"]["credit"]


# ---------------------------------------------------------------- flood maps in validate

def test_flood_map_points_never_use_unobserved_pixels(tmp_path):
    m = np.full((20, 20), 255, "uint8")
    m[:5, :] = 1
    m[15:, :] = 0
    obs, ctl, info = flood_map_points(write(tmp_path / "m.tif", m), n=30, seed=0)
    assert len(obs) == 30 and len(ctl) == 30
    assert (info["flooded_pixels"], info["dry_pixels"], info["not_observed_pixels"]) == (100, 100, 200)
    with rasterio.open(tmp_path / "m.tif") as src:
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, UTM, always_xy=True)
        for frame, value in ((obs, 1), (ctl, 0)):
            x, y = tr.transform(frame.lon.to_numpy(), frame.lat.to_numpy())
            rows, cols = rasterio.transform.rowcol(src.transform, x, y)
            assert (m[rows, cols] == value).all()


def test_validate_flood_map_sees_flooding_in_depressions(tmp_path):
    z = np.full((120, 120), 10.0)
    for r in range(10, 110, 20):
        for c in range(10, 110, 20):
            z[r:r + 4, c:c + 4] = 9.0
    res = analyze_array(z, T10, UTM, min_depth=0.15, res=12)
    m = np.zeros((120, 120), "uint8")
    m[z < 10] = 1                                     # flooding exactly in the pits
    v = validate_flood_map(res, write(tmp_path / "m.tif", m), n=500, n_boot=50,
                           max_rings=1, block_res=9)
    assert v.rings.lift.iloc[0] > 5
    assert v.report["controls"]["source"] == "observed dry pixels of the flood map"
    assert v.report["flood_map"]["flooded_pixels"] == 25 * 16


def test_flood_events_answer_does_not_depend_on_workers(tmp_path):
    res = two_pits()
    maps = {}
    for k in range(5):
        m = np.zeros((30, 40), "uint8")
        m[10:10 + k, 5:10] = 1
        m[10:15, 25:25 + k] = 1
        maps[f"m{k}"] = write(tmp_path / f"m{k}.tif", m)
    a, sa = flood_events(res, maps, workers=1)
    b, sb = flood_events(res, maps, workers=4)
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(sa, sb)


def test_items_without_a_layer_are_skipped_not_counted_dry():
    res = two_pits()
    scenes = pd.DataFrame({"item": ["OK", "NOEXCL"], "datetime": ["2025-01-01", "2025-12-01"],
                           "flood": ["f", "f"], "exclusion": ["e", None], "reference": ["r", "r"]})
    codes = np.zeros((30, 40), "uint8")
    summary, seen = flood_events(res, scenes, reader=lambda item: (codes, T10, UTM))
    assert summary.attrs["settings"]["skipped_missing_layer"] == ["NOEXCL"]
    assert summary.attrs["settings"]["n_maps"] == 1 and set(seen.scene) == {"OK"}
    with pytest.raises(ValueError, match="cannot be judged"):
        gfm.read_scene({"item": "x", "flood": "f", "exclusion": None, "reference": "r"})


def test_advisory_flags_make_pixels_not_observed_when_asked():
    flood = np.array([1, 1, 0, 0], "uint8")
    zero = np.zeros(4, "uint8")
    adv = np.array([0, 1, 2, 3], "uint8")
    assert gfm.code(flood, zero, zero).tolist() == [1, 1, 0, 0]
    assert gfm.code(flood, zero, zero, adv).tolist() == [1, 255, 255, 255]
    item = {"item": "x", "flood": "f", "exclusion": "e", "reference": "r"}
    assert gfm.missing_layers(item) == [] and gfm.missing_layers(item, True) == ["advisory"]


def test_read_scene_with_advisory(tmp_path):
    t = from_origin(500000, 3300000, 20, 20)
    flood = np.ones((20, 20), "uint8")
    zero = np.zeros((20, 20), "uint8")
    adv = np.zeros((20, 20), "uint8")
    adv[:, 10:] = 1
    item = {k: str(write(tmp_path / f"{k}.tif", a, t)) for k, a in
            (("flood", flood), ("exclusion", zero), ("reference", zero), ("advisory", adv))}
    plain, _, _ = gfm.read_scene(item)
    flagged, _, _ = gfm.read_scene(item, advisory=True)
    assert (plain == 1).all()
    assert (flagged[:, :10] == 1).all() and (flagged[:, 10:] == 255).all()
