"""Levels, shape, fill curves and the depression x cell table (`details=True`).

Shapes are checked against cases worked by hand (a one-pixel pit, straight canals in four
directions, a rectangle), fill curves and the depression x cell table against sums over each
depression's own pixels, and the tiled analysis against the whole-raster one. Damaged results
must be caught by `verify`.
"""
import math

import h3
import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin

from h3lowpoints import analyze_array, analyze_tiled, read_result
from h3lowpoints import tiling as T
from h3lowpoints.cli import main
from h3lowpoints.hydro.details import AREA_DOWN, FILL_LEVELS, VOLUME_DOWN
from h3lowpoints.pipeline import DEPRESSION_CELL_TABLE, DETAIL_COLUMNS
from h3lowpoints.verify import check_saved
from test_store import FORMATS
from test_tiling import smooth, write_dem

UTM = ("EPSG:32614", from_origin(500000, 3350000, 30, 30))      # 30 m pixels
KW = dict(res=10, min_depth=0.2, details=True)


def run(z, **kw):
    return analyze_array(z, UTM[1], UTM[0], **{**KW, **kw})


# ------------------------------------------------------------------ shape, worked by hand

def test_a_one_pixel_pit():
    z = np.full((9, 9), 10.0)
    z[4, 4] = 9.0
    d = run(z).depressions.iloc[0]
    side = 4 * math.sqrt(900 / 12)                          # 34.64 m: the pixel's own extent
    assert d["length_m"] == pytest.approx(side) and d["width_m"] == pytest.approx(side)
    assert d["ground_m"] == 9.0 and d["water_level_m"] == 10.0


@pytest.mark.parametrize("direction, angle", [("east", 90.0), ("north", 0.0),
                                              ("northeast", 45.0), ("northwest", 135.0)])
def test_a_canal_is_long_and_thin_and_points_the_right_way(direction, angle):
    n = 21
    z = np.full((n + 6, n + 6), 10.0)
    for k in range(n):
        r, c = {"east": (13, 3 + k), "north": (3 + k, 13), "northeast": (3 + n - 1 - k, 3 + k),
                "northwest": (3 + k, 3 + k)}[direction]
        z[r, c] = 9.0
    d = run(z).depressions.iloc[0]
    assert d["orientation_deg"] == pytest.approx(angle, abs=1e-9)
    step2 = 900.0 if direction in ("east", "north") else 1800.0   # squared spacing of centres
    # along the canal: the spread of the pixel centres plus each pixel's own (900 / 12)
    assert d["length_m"] == pytest.approx(4 * math.sqrt((n * n - 1) / 12 * step2 + 900 / 12),
                                          rel=1e-9)
    assert d["width_m"] == pytest.approx(4 * math.sqrt(900 / 12), rel=1e-9)
    assert d["length_m"] / d["width_m"] > 10


def test_a_rectangle():
    z = np.full((20, 30), 10.0)
    z[5:9, 5:17] = 9.0                                       # 4 rows by 12 columns
    d = run(z).depressions.iloc[0]
    assert d["length_m"] == pytest.approx(4 * math.sqrt((12 * 12) / 12 * 900), rel=1e-12)
    assert d["width_m"] == pytest.approx(4 * math.sqrt((4 * 4) / 12 * 900), rel=1e-12)
    assert d["orientation_deg"] == pytest.approx(90.0)


# ------------------------------------------------------------------ fill curves and cells

def test_fill_curve_and_cells_are_sums_over_each_depressions_pixels():
    z = smooth(3, (60, 80), sigma=2.0)
    r = run(z, res=9)
    labels, depth = r.labels, r.depth
    area = 900.0
    for row in r.depressions.itertuples():
        d = depth[labels == row.id]
        for (t, _), a_name, v_name in zip(FILL_LEVELS, AREA_DOWN, VOLUME_DOWN):
            assert getattr(row, a_name) == pytest.approx(area * (d > t).sum(), abs=1e-6)
            assert getattr(row, v_name) == pytest.approx(area * np.clip(d - t, 0, None).sum(),
                                                         rel=1e-12, abs=1e-9)
    # the depression x cell table: per (depression, cell), straight from the pixels
    rr, cc = np.nonzero(labels)
    xs, ys = UTM[1] @ (cc + 0.5, rr + 0.5)
    from pyproj import Transformer

    lon, lat = Transformer.from_crs(UTM[0], 4326, always_xy=True).transform(xs, ys)
    px = pd.DataFrame({"id": labels[rr, cc], "h3": [h3.latlng_to_cell(a, b, 9) for a, b in zip(lat, lon)],
                       "d": depth[rr, cc]})
    want = px.groupby(["id", "h3"]).agg(area_m2=("d", lambda s: area * len(s)),
                                        volume_m3=("d", lambda s: area * s.sum()),
                                        max_depth_m=("d", "max")).reset_index()
    got = r.depression_cells.sort_values(["id", "h3"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(got[want.columns], want, check_dtype=False, rtol=1e-12)
    assert list(r.depression_cells.columns) == DEPRESSION_CELL_TABLE


def test_without_details_nothing_changes():
    z = smooth(4, (40, 50))
    plain, rich = run(z, details=False), run(z)
    assert plain.depression_cells is None
    pd.testing.assert_frame_equal(plain.depressions, rich.depressions.drop(columns=DETAIL_COLUMNS))


# ------------------------------------------------------------------ tiled equals whole

def same_details(whole, tiled):
    a = whole.depressions.set_index("h3_lowpoint_15").sort_index()
    b = tiled.depressions.set_index("h3_lowpoint_15").sort_index()
    assert len(a) == len(b)
    for col in DETAIL_COLUMNS:
        if col != "orientation_deg":
            np.testing.assert_allclose(a[col], b[col], rtol=1e-9, atol=1e-9, err_msg=col)
    long = (a["length_m"] / a["width_m"] > 1.05).to_numpy()   # round shapes have no direction
    diff = np.abs(a["orientation_deg"].to_numpy() - b["orientation_deg"].to_numpy())[long]
    assert (np.minimum(diff, 180 - diff) < 1e-6).all()
    a2b = dict(zip(whole.depressions["id"], whole.depressions["h3_lowpoint_15"].map(
        dict(zip(tiled.depressions["h3_lowpoint_15"], tiled.depressions["id"])))))
    ca = whole.depression_cells.assign(id=whole.depression_cells["id"].map(a2b))
    ca = ca.sort_values(["h3", "id"]).reset_index(drop=True)
    cb = tiled.depression_cells.sort_values(["h3", "id"]).reset_index(drop=True)
    assert (ca[["id", "h3"]].to_numpy() == cb[["id", "h3"]].to_numpy()).all()
    for col in DEPRESSION_CELL_TABLE[2:]:
        np.testing.assert_allclose(ca[col], cb[col], rtol=1e-9, atol=1e-9, err_msg=col)


@pytest.mark.parametrize("kind", ["smooth", "flats", "nodata", "lonlat"])
def test_tiled_equals_whole(tmp_path, kind):
    from h3lowpoints import analyze

    z = smooth(5, (70, 90))
    crs, transform = "EPSG:26914", from_origin(500000, 3350000, 1, 1)
    if kind == "flats":
        z = np.round(z * 2) / 2
    elif kind == "nodata":
        z[10:30, 40:60] = -9999.0
    elif kind == "lonlat":
        crs, transform = "EPSG:4326", from_origin(-98.0, 45.0, 0.0003, 0.0002)
    p = write_dem(tmp_path / "d.tif", z, crs=crs, transform=transform)
    res = 11 if kind == "lonlat" else 12
    whole = analyze(p, res=res, min_depth=0.2, details=True)
    for tile in (7, 19, 40):
        same_details(whole, analyze_tiled(p, tile=tile, res=res, min_depth=0.2, details=True))


@pytest.mark.parametrize("fmt", FORMATS)
def test_saved_read_back_and_verified(tmp_path, fmt):
    p = write_dem(tmp_path / "d.tif", smooth(6, (60, 70)))
    mem = analyze_tiled(p, tile=20, res=12, min_depth=0.2, details=True, catchments=True)
    disk = analyze_tiled(p, tile=20, res=12, min_depth=0.2, details=True, catchments=True,
                         out_dir=tmp_path / "out", format=fmt).load()
    pd.testing.assert_frame_equal(mem.depression_cells, disk.depression_cells, check_dtype=False)
    pd.testing.assert_frame_equal(mem.depressions, disk.depressions, check_dtype=False)
    assert check_saved(tmp_path / "out") == []


def test_a_crash_then_continuing_gives_the_uninterrupted_result(tmp_path, monkeypatch):
    p = write_dem(tmp_path / "d.tif", smooth(7, (60, 70)))
    kw = dict(tile=20, res=12, min_depth=0.2, details=True)
    reference = analyze_tiled(p, out_dir=tmp_path / "ref", **kw).load()
    real, calls = T._pass2_tile, {"n": 0}

    def crash(*a, **k):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("simulated crash")
        return real(*a, **k)
    monkeypatch.setattr(T, "_pass2_tile", crash)
    with pytest.raises(RuntimeError):
        analyze_tiled(p, out_dir=tmp_path / "run", **kw)
    monkeypatch.setattr(T, "_pass2_tile", real)
    got = analyze_tiled(p, out_dir=tmp_path / "run", **kw).load()
    pd.testing.assert_frame_equal(got.depressions, reference.depressions)
    pd.testing.assert_frame_equal(got.depression_cells, reference.depression_cells)


def test_command_line_option(tmp_path):
    p = write_dem(tmp_path / "d.tif", smooth(8, (50, 60)))
    assert main(["analyze", p, "--details", "--tile", "25", "--res", "12",
                 "--min-depth", "0.2", "--out", str(tmp_path / "out")]) == 0
    got = read_result(tmp_path / "out")
    assert set(DETAIL_COLUMNS) <= set(got.depressions.columns)
    assert got.depression_cells is not None and check_saved(tmp_path / "out") == []


# ------------------------------------------------------------------ verify catches damage

def _corrupt(folder, name, change):
    from h3lowpoints.store import read_table, write_table

    write_table(change(read_table(folder, name)), folder / f"{name}.csv")


DAMAGE = {
    "is not max_depth_m": ("depressions", lambda d: d.assign(ground_m=d["ground_m"] + 0.3)),
    "exceeds its length": ("depressions", lambda d: d.assign(width_m=d["length_m"] * 2)),
    "outside [0, 180)": ("depressions", lambda d: d.assign(orientation_deg=200.0)),
    "area grows": ("depressions", lambda d: d.assign(area_1m_down_m2=d["area_m2"] * 2)),
    "volume grows": ("depressions", lambda d: d.assign(volume_2m_down_m3=d["volume_m3"] * 2)),
    "does not add up to area_m2": ("depression_cells", lambda c: c.assign(area_m2=c["area_m2"] * 1.5)),
    "deepest depression_cells row": ("depression_cells", lambda c: c.assign(max_depth_m=c["max_depth_m"] / 2)),
    "names depressions": ("depression_cells", lambda c: c.assign(id=c["id"] + 10**6)),
}


@pytest.mark.parametrize("message", list(DAMAGE))
def test_verify_catches_damaged_details(tmp_path, message):
    p = write_dem(tmp_path / "d.tif", smooth(9, (60, 70)))
    folder = analyze_tiled(p, tile=25, res=12, min_depth=0.2, details=True,
                           out_dir=tmp_path / "out").path
    assert check_saved(folder) == []
    _corrupt(folder, *DAMAGE[message])
    assert any(message in problem for problem in check_saved(folder)), check_saved(folder)


def test_clip_keeps_all_cells_of_kept_depressions(tmp_path):
    import shapely

    from h3lowpoints.clip import clip_result

    p = write_dem(tmp_path / "d.tif", smooth(10, (60, 70)))
    folder = analyze_tiled(p, tile=25, res=12, min_depth=0.2, details=True,
                           out_dir=tmp_path / "out").path
    full = read_result(folder)
    west = shapely.box(-180, -90, float(full.depressions["lon"].median()), 90)
    got = read_result(clip_result(folder, west, tmp_path / "clipped"))
    want = full.depression_cells[full.depression_cells["id"].isin(set(got.depressions["id"]))]
    pd.testing.assert_frame_equal(got.depression_cells, want.reset_index(drop=True), check_dtype=False)
    assert check_saved(tmp_path / "clipped") == []


def test_orientation_does_not_depend_on_which_way_rows_run():
    """The same northeast canal stored north-up and south-up points the same way."""
    n = 15
    z = np.full((n + 6, n + 6), 10.0)
    for k in range(n):
        z[3 + n - 1 - k, 3 + k] = 9.0                       # north-up: row 0 is the north
    north_up = run(z).depressions.iloc[0]
    south_up = analyze_array(z[::-1], from_origin(500000, 3350000 - 30 * z.shape[0], 30, -30),
                             "EPSG:32614", **KW).depressions.iloc[0]
    assert north_up["orientation_deg"] == pytest.approx(45.0)
    assert south_up["orientation_deg"] == pytest.approx(45.0)
    assert south_up["length_m"] == pytest.approx(north_up["length_m"])


@pytest.mark.network
def test_real_terrain_houston(tmp_path):
    """USGS 3DEP 30 m over west Houston: tiled details equal whole details."""
    from h3lowpoints import analyze
    from h3lowpoints.sources.usgs import fetch_3dep

    p = fetch_3dep((-95.65, 29.6, -95.3, 29.9), tmp_path / "houston.tif")
    whole = analyze(p, min_depth=0.5, details=True)
    for tile in (100, 512):
        same_details(whole, analyze_tiled(p, tile=tile, min_depth=0.5, details=True))
