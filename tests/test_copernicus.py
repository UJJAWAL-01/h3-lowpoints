"""The Copernicus GLO-30 reader: tile naming, bounding boxes, mosaics, guards and the CLI.

Most tests use small fake tiles with the real tile grid (1/3600 degree rows), so they run
offline. Tests marked `network` read the real bucket (H3LP_NETWORK=1 to run them).
"""
import warnings

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze
from h3lowpoints.cli import main
from h3lowpoints.sources import copernicus as cop
from h3lowpoints.sources.copernicus import (NODATA, fetch_dem, tile_name, tile_url,
                                            tiles_for_bbox)

PX = 1 / 3600


def f(lon, lat):
    """A smooth, position-dependent height, so a mosaic can be checked pixel by pixel."""
    return lon * 10.0 + lat


def make_tile(folder, lat, lon, west, south, nx=40, ny=40, xres=PX, func=f, pit=None):
    """Write a fake tile named for (lat, lon) whose data covers only a small window."""
    north = south + ny * PX
    xs = west + (np.arange(nx) + 0.5) * xres
    ys = north - (np.arange(ny) + 0.5) * PX
    arr = np.broadcast_to(func(xs[None, :], ys[:, None]), (ny, nx)).astype("float32").copy()
    if pit is not None:
        arr[pit] = arr[pit] - 10
    path = folder / f"{tile_name(lat, lon)}.tif"
    prof = {"driver": "GTiff", "height": ny, "width": nx, "count": 1, "dtype": "float32",
            "crs": "EPSG:4326", "transform": from_origin(west, north, xres, PX)}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)
    return path


# ------------------------------------------------------------------ names and boxes

@pytest.mark.parametrize("lat, lon, name", [
    (0, 6, "Copernicus_DSM_COG_10_N00_00_E006_00_DEM"),          # appears in the real tile list
    (30, -98, "Copernicus_DSM_COG_10_N30_00_W098_00_DEM"),
    (0, 0, "Copernicus_DSM_COG_10_N00_00_E000_00_DEM"),
    (-1, -1, "Copernicus_DSM_COG_10_S01_00_W001_00_DEM"),
    (-34, 151, "Copernicus_DSM_COG_10_S34_00_E151_00_DEM"),
    (89, 179, "Copernicus_DSM_COG_10_N89_00_E179_00_DEM"),
    (-90, -180, "Copernicus_DSM_COG_10_S90_00_W180_00_DEM"),
])
def test_tile_names(lat, lon, name):
    assert tile_name(lat, lon) == name


def test_tile_url():
    assert tile_url(30, -98) == (
        "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N30_00_W098_00_DEM/"
        "Copernicus_DSM_COG_10_N30_00_W098_00_DEM.tif")


@pytest.mark.parametrize("bbox, want", [
    ((-97.9, 30.1, -97.6, 30.4), [(30, -98)]),                              # inside one tile
    ((-98.0, 30.0, -97.0, 31.0), [(30, -98)]),                              # exactly one tile
    ((-98.0, 30.0, -96.9999, 31.0), [(30, -98), (30, -97)]),                # a hair into the next
    ((-0.5, -0.5, 0.5, 0.5), [(-1, -1), (-1, 0), (0, -1), (0, 0)]),         # across both zero lines
    ((179.2, 89.2, 180.0, 90.0), [(89, 179)]),                              # the far corner
    ((-180.0, -90.0, -179.5, -89.5), [(-90, -180)]),                        # the opposite corner
    ((9.5, 49.5, 10.5, 50.5), [(49, 9), (49, 10), (50, 9), (50, 10)]),
])
def test_tiles_for_bbox(bbox, want):
    assert sorted(tiles_for_bbox(*bbox)) == sorted(want)


@pytest.mark.parametrize("bbox", [
    (10, 0, 5, 1),            # west > east (a box over the antimeridian must be split)
    (0, 10, 1, 5),            # south > north
    (0, 0, 0, 1),             # zero width
    (-181, 0, 0, 1), (0, 0, 181, 1), (0, -91, 1, 0), (0, 0, 1, 91),
])
def test_invalid_boxes_are_rejected(bbox):
    with pytest.raises(ValueError):
        tiles_for_bbox(*bbox)


# ------------------------------------------------------------------ fetching

def test_mosaic_across_a_tile_seam_is_exact(tmp_path):
    make_tile(tmp_path, 30, -98, west=-97 - 40 * PX, south=30.0)   # hugs the east edge of W098
    make_tile(tmp_path, 30, -97, west=-97.0, south=30.0)            # hugs the west edge of W097
    out = fetch_dem((-97 - 20 * PX, 30.0, -97 + 20 * PX, 30 + 40 * PX), tmp_path / "o.tif",
                    source=tmp_path)
    with rasterio.open(out) as r:
        a = r.read(1)
        assert r.crs.to_epsg() == 4326 and r.nodata == NODATA and r.dtypes[0] == "float32"
        assert r.shape == (40, 40)
        xs = r.transform.c + (np.arange(40) + 0.5) * r.transform.a
        ys = r.transform.f + (np.arange(40) + 0.5) * r.transform.e
    np.testing.assert_allclose(a, f(xs[None, :], ys[:, None]), atol=1e-3)
    assert (a != NODATA).all()


def test_output_grid_is_aligned_to_the_tile_grid(tmp_path):
    make_tile(tmp_path, 30, -98, west=-97.99, south=30.0, nx=100, ny=100)
    out = fetch_dem((-97.9876, 30.0021, -97.9812, 30.0079), tmp_path / "o.tif", source=tmp_path)
    with rasterio.open(out) as r:
        # origin on the 1/3600 grid, so the pixels line up with the source tiles
        assert (r.transform.c / PX) == pytest.approx(round(r.transform.c / PX), abs=1e-4)
        assert (r.transform.f / PX) == pytest.approx(round(r.transform.f / PX), abs=1e-4)
        assert r.bounds.left <= -97.9876 and r.bounds.right >= -97.9812      # snapped outwards
        assert r.bounds.bottom <= 30.0021 and r.bounds.top >= 30.0079


def test_missing_tile_becomes_nodata_not_an_error(tmp_path):
    make_tile(tmp_path, 30, -98, west=-97 - 40 * PX, south=30.0)
    make_tile(tmp_path, 30, -96, west=-96.0, south=30.0)             # W097 is missing between
    out = fetch_dem((-97 - 40 * PX, 30.0, -96 + 40 * PX, 30 + 40 * PX), tmp_path / "o.tif",
                    source=tmp_path)
    with rasterio.open(out) as r:
        a = r.read(1)
    assert a.shape[1] == 3600 + 80
    assert (a[:, :40] != NODATA).all() and (a[:, -40:] != NODATA).all()   # both real windows
    assert (a[:, 40:-40] == NODATA).all()                                 # the gap


def test_nodata_gap_acts_as_an_outlet_in_the_analysis(tmp_path):
    flat = lambda x, y: 100 + 0 * x   # noqa: E731
    pit = (slice(15, 25), slice(30, 40))          # touches the window's east edge
    make_tile(tmp_path, 30, -98, west=-97 - 40 * PX, south=30.0, func=flat, pit=pit)
    box = (-97 - 40 * PX, 30.0, -97 + 40 * PX, 30 + 40 * PX)
    # Control: with real flat terrain on the east side the pit is enclosed, so it is found.
    make_tile(tmp_path, 30, -97, west=-97.0, south=30.0, func=flat)
    enclosed = fetch_dem(box, tmp_path / "enclosed.tif", source=tmp_path)
    found = analyze(str(enclosed), res=8, min_depth=1.0).depressions
    assert len(found) == 1 and found["max_depth_m"].iloc[0] == pytest.approx(10.0, abs=1e-4)
    # Same pit, but the neighbouring tile is missing: the gap is nodata, an outlet, so no depression.
    (tmp_path / f"{tile_name(30, -97)}.tif").unlink()
    gap = fetch_dem(box, tmp_path / "gap.tif", source=tmp_path)
    with rasterio.open(gap) as r:
        assert (r.read(1) == NODATA).any()
    assert len(analyze(str(gap), res=8, min_depth=1.0).depressions) == 0


def test_all_tiles_missing_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="No GLO-30 tiles"):
        fetch_dem((-30.5, 0.5, -30.2, 0.8), tmp_path / "o.tif", source=tmp_path)


def test_tile_limit_guards_against_huge_requests(tmp_path):
    with pytest.raises(ValueError, match="touches 400 tiles"):
        fetch_dem((0, 0, 20, 20), tmp_path / "o.tif", source=tmp_path)
    make_tile(tmp_path, 0, 0, west=0.0, south=0.0)
    fetch_dem((0, 0, 0.005, 0.005), tmp_path / "o.tif", source=tmp_path, max_tiles=1)   # ok


def test_narrower_tiles_towards_the_poles_warn_and_use_the_finest_grid(tmp_path):
    make_tile(tmp_path, 49, 10, west=10.0, south=50 - 40 * PX, nx=40)                  # 1/3600 columns
    make_tile(tmp_path, 50, 10, west=10.0, south=50.0, nx=24, xres=1 / 2400)           # 1/2400 columns
    with pytest.warns(UserWarning, match="different longitude resolutions"):
        out = fetch_dem((10.0, 50 - 40 * PX, 10.01, 50 + 40 * PX), tmp_path / "o.tif",
                        source=tmp_path)
    with rasterio.open(out) as r:
        a = r.read(1)
        assert r.res[0] == pytest.approx(PX, rel=1e-6)
        xs = r.transform.c + (np.arange(r.width) + 0.5) * r.transform.a
        ys = r.transform.f + (np.arange(r.height) + 0.5) * r.transform.e
    want = f(xs[None, :], ys[:, None])
    assert (a != NODATA).all()
    np.testing.assert_allclose(a, want, atol=0.01)     # nearest-neighbour repeat: < 0.01 m here


def test_same_resolution_tiles_do_not_warn(tmp_path):
    make_tile(tmp_path, 30, -98, west=-97 - 40 * PX, south=30.0)
    make_tile(tmp_path, 30, -97, west=-97.0, south=30.0)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fetch_dem((-97 - 20 * PX, 30.0, -97 + 20 * PX, 30 + 40 * PX), tmp_path / "o.tif",
                  source=tmp_path)
    # other libraries may warn about their own internals; only ours matters here
    assert not [w for w in caught if "longitude resolutions" in str(w.message)]


def test_southern_and_western_hemispheres(tmp_path):
    make_tile(tmp_path, -34, 151, west=151.0, south=-34 + 1 - 40 * PX)
    out = fetch_dem((151.0, -34 + 1 - 40 * PX, 151 + 40 * PX, -33.0), tmp_path / "o.tif",
                    source=tmp_path)
    with rasterio.open(out) as r:
        a = r.read(1)
        ys = r.transform.f + (np.arange(r.height) + 0.5) * r.transform.e
        xs = r.transform.c + (np.arange(r.width) + 0.5) * r.transform.a
    np.testing.assert_allclose(a, f(xs[None, :], ys[:, None]), atol=1e-3)
    assert ys.max() < 0


def test_creates_missing_output_folders(tmp_path):
    make_tile(tmp_path, 0, 0, west=0.0, south=0.0)
    out = fetch_dem((0, 0, 0.005, 0.005), tmp_path / "a" / "b" / "o.tif", source=tmp_path)
    assert out.exists()


def test_a_pit_in_a_fetched_dem_is_found_by_analyze(tmp_path):
    make_tile(tmp_path, 30, -98, west=-97.99, south=30.0, nx=200, ny=200,
              func=lambda x, y: 100 + 0 * x, pit=(slice(80, 120), slice(80, 120)))
    out = fetch_dem((-97.99, 30.0, -97.99 + 200 * PX, 30 + 200 * PX), tmp_path / "o.tif",
                    source=tmp_path)
    dep = analyze(str(out), res=9, min_depth=1.0).depressions
    assert len(dep) == 1
    assert dep["max_depth_m"].iloc[0] == pytest.approx(10.0, abs=1e-4)
    # 40 x 40 pixels of about 30.9 m x 26.7 m at 30 N
    assert dep["area_m2"].iloc[0] == pytest.approx(1600 * 30.9 * 26.7, rel=0.02)


def test_available_tiles_is_only_used_for_the_default_source(tmp_path, monkeypatch):
    def boom():
        raise AssertionError("must not touch the network when a local source is given")
    monkeypatch.setattr(cop, "available_tiles", boom)
    make_tile(tmp_path, 0, 0, west=0.0, south=0.0)
    fetch_dem((0, 0, 0.005, 0.005), tmp_path / "o.tif", source=tmp_path)


def test_default_source_consults_the_tile_list(monkeypatch):
    monkeypatch.setattr(cop, "available_tiles", lambda: frozenset({tile_name(30, -98)}))
    assert cop._locate(30, -98, None).endswith(f"{tile_name(30, -98)}.tif")
    assert cop._locate(30, -98, None).startswith("/vsicurl/https://")
    assert cop._locate(0, 0, None) is None


# ------------------------------------------------------------------ command line

def test_cli_fetch_then_analyze(tmp_path, capsys):
    make_tile(tmp_path, 30, -98, west=-97.99, south=30.0, nx=200, ny=200,
              func=lambda x, y: 100 + 0 * x, pit=(slice(80, 120), slice(80, 120)))
    dem = tmp_path / "dem.tif"
    assert main(["fetch-dem", "--bbox", "-97.99", "30.0", str(-97.99 + 200 * PX), str(30 + 200 * PX),
                 "--out", str(dem), "--source", str(tmp_path)]) == 0
    assert "pixels written" in capsys.readouterr().out
    out = tmp_path / "res"
    assert main(["analyze", str(dem), "--res", "9", "--min-depth", "1", "--out", str(out)]) == 0
    assert (out / "depressions.csv").exists()


def test_cli_fetch_reports_errors_cleanly(tmp_path):
    with pytest.raises(SystemExit, match="No GLO-30 tiles"):
        main(["fetch-dem", "--bbox", "-30.5", "0.5", "-30.2", "0.8", "--out",
              str(tmp_path / "o.tif"), "--source", str(tmp_path)])
    with pytest.raises(SystemExit, match="west < east"):
        main(["fetch-dem", "--bbox", "10", "0", "5", "1", "--out", str(tmp_path / "o.tif")])


# ------------------------------------------------------------------ the real bucket

@pytest.mark.network
def test_live_tile_list_contains_the_names_we_generate():
    names = cop.available_tiles()
    assert len(names) > 25_000
    assert tile_name(30, -98) in names and tile_name(52, 4) in names
    assert tile_name(0, -30) not in names            # open Atlantic: no tile


@pytest.mark.network
def test_live_fetch_austin(tmp_path):
    out = fetch_dem((-97.85, 30.20, -97.65, 30.35), tmp_path / "austin.tif")
    with rasterio.open(out) as r:
        a = r.read(1)
        assert r.shape == (540, 720) and r.crs.to_epsg() == 4326
    assert 100 < a.min() < 200 and 250 < a.max() < 400          # Austin is 120 to 320 m
    assert (a != NODATA).all()


@pytest.mark.network
def test_live_open_ocean_has_no_tiles(tmp_path):
    with pytest.raises(ValueError, match="No GLO-30 tiles"):
        fetch_dem((-30.5, 0.5, -30.2, 0.8), tmp_path / "o.tif")


@pytest.mark.network
def test_live_dutch_polders_are_depressions_below_sea_level(tmp_path):
    """Rotterdam's polders lie below sea level behind dikes: a real, large, known depression."""
    out = fetch_dem((4.0, 51.7, 4.6, 52.1), tmp_path / "nl.tif")
    res = analyze(str(out), res=8, min_depth=1.0)
    big = res.depressions.sort_values("area_m2", ascending=False).iloc[0]
    assert big["area_m2"] > 20e6                                  # tens of square kilometres
    assert not big["edge"]
    with rasterio.open(out) as r:
        a = r.read(1)
    inside = res.labels == int(big["id"])
    assert a[inside].min() < -3                                   # well below sea level
    assert (a + res.depth)[inside].max() == pytest.approx(0.0, abs=0.5)   # spills at sea level


@pytest.mark.network
@pytest.mark.parametrize("name, bbox, tile", [
    ("norway_fjords", (6.0, 60.0, 7.2, 61.0), 400),            # sea and fjords: lots of nodata edges
    ("alps", (10.3, 46.3, 10.9, 46.8), 300),                   # steep mountains
    ("death_valley", (-117.2, 35.9, -116.7, 36.4), 250),       # closed desert basin
])
def test_live_tiled_equals_whole_on_real_terrain(tmp_path, name, bbox, tile):
    from test_tiling import assert_equivalent

    from h3lowpoints import analyze_tiled

    path = fetch_dem(bbox, tmp_path / f"{name}.tif")
    whole = analyze(str(path), res=8, min_depth=1.0)
    assert len(whole.depressions) > 100                         # the comparison is not trivial
    assert_equivalent(whole, analyze_tiled(str(path), tile=tile, res=8, min_depth=1.0))
