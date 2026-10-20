"""The water mask: open water from ESA WorldCover on an elevation raster's own pixel grid.

The oracle samples each elevation pixel at 3 x 3 evenly spaced points by hand and looks each
point up in the WorldCover array; the mask must agree pixel for pixel.
"""
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import read_result
from h3lowpoints.cli import main
from h3lowpoints.sources import landcover, usgs
from test_sources import PX1, write
from test_verify import PITS, _corner_tiles

WPX = 1 / 12000          # WorldCover pixel
EPS = 1e-7               # shifts the elevation grid so no sample point sits exactly on a pixel edge


def fake_worldcover(folder, lat, lon, west, north, arr):
    """A window of WorldCover tile (lat, lon) whose top-left corner is (west, north)."""
    name = landcover.worldcover_tile_name(lat, lon)
    return write(folder / f"{name}.tif", arr, "EPSG:4326", from_origin(west, north, WPX, WPX), 0,
                 dtype="uint8")


def blobs(shape, seed):
    rng = np.random.default_rng(seed)
    arr = np.full(shape, 10, dtype="uint8")                          # tree cover
    for _ in range(12):
        r, c = rng.integers(0, shape[0]), rng.integers(0, shape[1])
        rad = rng.integers(3, 40)
        yy, xx = np.ogrid[:shape[0], :shape[1]]
        arr[(yy - r) ** 2 + (xx - c) ** 2 < rad ** 2] = landcover.WATER_CLASS
    return arr


def oracle(dem_transform, shape, wc_west, wc_north, wc):
    """Water by hand: 5 or more of 9 sample points in each pixel on class 80."""
    out = np.zeros(shape, dtype="uint8")
    k = (np.arange(3) + 0.5) / 3
    for r in range(shape[0]):
        for c in range(shape[1]):
            xs = dem_transform.c + (c + k) * dem_transform.a
            ys = dem_transform.f + (r + k) * dem_transform.e
            cols = np.floor((xs - wc_west) / WPX).astype(int)
            rows = np.floor((wc_north - ys) / WPX).astype(int)
            hits = 0
            for rr in rows:
                for cc in cols:
                    inside = 0 <= rr < wc.shape[0] and 0 <= cc < wc.shape[1]
                    hits += inside and wc[rr, cc] == landcover.WATER_CLASS
            out[r, c] = hits >= 5
    return out


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_the_mask_matches_sampling_by_hand(tmp_path, seed):
    wc = blobs((400, 500), seed)
    # WorldCover tiles lie on WorldCover's own pixel grid, as the real ones do
    wc_west, wc_north = -98.0 + 10 * WPX, 31.0 - 7 * WPX
    fake_worldcover(tmp_path, 30, -99, wc_west, wc_north, wc)
    dem_tr = from_origin(-98.0 + 5 * PX1 + EPS, 31.0 - 5 * PX1 - EPS, PX1, PX1)
    shape = (100, 130)
    dem = write(tmp_path / "dem.tif", np.zeros(shape), "EPSG:4269", dem_tr, -9999.0)
    out = landcover.build_water_mask(dem, tmp_path / "water.tif", source=tmp_path, tile=37)
    with rasterio.open(out) as m, rasterio.open(dem) as d:
        assert m.transform == d.transform and m.shape == d.shape and m.dtypes[0] == "uint8"
        got = m.read(1)
    want = oracle(dem_tr, shape, wc_west, wc_north, wc)
    assert want.any() and not want.all()
    np.testing.assert_array_equal(got, want)


def test_a_projected_elevation_raster(tmp_path):
    wc = np.full((1200, 1200), 10, dtype="uint8")
    wc[300:900, 300:900] = landcover.WATER_CLASS                     # a lake 0.05 degrees across
    fake_worldcover(tmp_path, 30, -99, -97.6, 30.6, wc)
    from pyproj import Transformer
    x, y = Transformer.from_crs(4326, 32614, always_xy=True).transform(-97.55, 30.55)
    dem = write(tmp_path / "dem.tif", np.zeros((300, 300)), "EPSG:32614",
                from_origin(x - 3000, y + 3000, 20, 20), -9999.0)
    with rasterio.open(landcover.build_water_mask(dem, tmp_path / "w.tif", source=tmp_path)) as m:
        a = m.read(1)
    assert a[150, 150] == 1 and a[5, 5] == 0 and a[295, 295] == 0       # lake centre, and land
    assert 0.5 < a.mean() < 0.95                    # the 5 km lake covers most of the 6 km square


def test_no_tiles_is_an_error_and_a_failed_build_leaves_no_mask(tmp_path, monkeypatch):
    dem = write(tmp_path / "dem.tif", np.zeros((20, 20)), "EPSG:4269",
                from_origin(-98.0, 31.0, PX1, PX1), -9999.0)
    with pytest.raises(ValueError, match="No WorldCover tiles"):
        landcover.build_water_mask(dem, tmp_path / "w.tif", source=tmp_path)
    fake_worldcover(tmp_path, 30, -99, -98.0, 31.0, np.full((100, 100), 80, dtype="uint8"))
    calls = {"n": 0}
    real_write = rasterio.io.DatasetWriter.write

    def failing_write(self, *a, **k):            # a crash after the first block is written
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash while writing")
        return real_write(self, *a, **k)
    monkeypatch.setattr(rasterio.io.DatasetWriter, "write", failing_write)
    with pytest.raises(RuntimeError):
        landcover.build_water_mask(dem, tmp_path / "w.tif", source=tmp_path, tile=8)
    assert not (tmp_path / "w.tif").exists()                     # never a half-built mask
    monkeypatch.undo()
    with rasterio.open(landcover.build_water_mask(dem, tmp_path / "w.tif", source=tmp_path)) as m:
        assert m.read(1).all()                                   # and a later build completes


def _build(tmp_path, monkeypatch, out, *extra):
    real_vrt, real_mask = usgs.build_3dep_vrt, landcover.build_water_mask
    monkeypatch.setattr(usgs, "build_3dep_vrt",
                        lambda b, path, **k: real_vrt(b, path, resolution=30, source=tmp_path / "tiles"))
    monkeypatch.setattr(landcover, "build_water_mask",
                        lambda dem, path, **k: real_mask(dem, path, source=tmp_path / "wc", tile=k.get("tile", 4096)))
    box = (-97 - 30 * PX1, 31 - 30 * PX1, -97 + 30 * PX1, 31 + 30 * PX1)
    return main(["build", "--bbox", *map(str, box), "--out", str(out), "--res", "10",
                 "--min-depth", "0.5", "--tile", "17", *extra])


def test_build_with_a_water_mask(tmp_path, monkeypatch, capsys):
    (tmp_path / "tiles").mkdir()
    (tmp_path / "wc").mkdir()
    _corner_tiles(tmp_path / "tiles")
    lon0, lat0, _ = PITS[0]                       # open water over the deepest pit
    wc = np.full((240, 240), 10, dtype="uint8")
    yy, xx = np.ogrid[:240, :240]
    wc[(yy - 120) ** 2 + (xx - 120) ** 2 < 30 ** 2] = landcover.WATER_CLASS
    fake_worldcover(tmp_path / "wc", 30, -99, lon0 - 120 * WPX, lat0 + 120 * WPX, wc)

    assert _build(tmp_path, monkeypatch, tmp_path / "plain") == 0
    assert _build(tmp_path, monkeypatch, tmp_path / "masked", "--water-mask") == 0
    assert "all invariants hold" in capsys.readouterr().out
    plain, masked = read_result(tmp_path / "plain"), read_result(tmp_path / "masked")
    near = lambda d: ((d["lon"] - lon0).abs() < 8 * PX1) & ((d["lat"] - lat0).abs() < 8 * PX1)  # noqa: E731
    assert near(plain.depressions).any()                         # the pit is a depression ...
    assert not near(masked.depressions).any()                    # ... until it is open water
    assert len(masked.depressions) == len(plain.depressions) - 1     # the other pits are unchanged
    assert (tmp_path / "masked" / "water.vrt").exists()
    assert "WorldCover" in json.loads((tmp_path / "masked" / "meta.json").read_text())["build"]["water_mask"]
    with rasterio.open(tmp_path / "masked" / "water.vrt") as m, \
            rasterio.open(tmp_path / "masked" / "dem.vrt") as d:
        assert m.transform == d.transform and m.shape == d.shape and m.read(1).any()


@pytest.mark.network
def test_live_worldcover_water(tmp_path):
    # the middle of the Great Salt Lake is water; downtown Salt Lake City is not
    for name, lon, lat, want in (("lake", -112.5, 41.1, 1), ("city", -111.89, 40.76, 0)):
        dem = write(tmp_path / f"{name}.tif", np.zeros((30, 30)), "EPSG:4269",
                    from_origin(lon - 15 * PX1, lat + 15 * PX1, PX1, PX1), -9999.0)
        with rasterio.open(landcover.build_water_mask(dem, tmp_path / f"{name}_w.tif")) as m:
            assert (m.read(1) == want).mean() > 0.95, name


@pytest.mark.parametrize("tile", [13, 40, 4096])
def test_a_mask_built_tile_by_tile_equals_one_built_whole(tmp_path, tile):
    wc = blobs((400, 500), 7)
    fake_worldcover(tmp_path, 30, -99, -98.0 + 10 * WPX, 31.0 - 7 * WPX, wc)
    dem = write(tmp_path / "dem.tif", np.zeros((100, 130)), "EPSG:4269",
                from_origin(-98.0 + 5 * PX1 + EPS, 31.0 - 5 * PX1 - EPS, PX1, PX1), -9999.0)
    whole = landcover.build_water_mask(dem, tmp_path / "whole.tif", source=tmp_path)
    tiled = landcover.build_water_mask(dem, tmp_path / "tiled.vrt", source=tmp_path, tile=tile)
    with rasterio.open(whole) as a, rasterio.open(tiled) as b:
        assert a.transform == b.transform and a.shape == b.shape
        np.testing.assert_array_equal(a.read(1), b.read(1))
    files = list((tmp_path / "tiled_tiles").iterdir())
    if tile == 13:          # tiles without water store only an empty marker
        assert any(f.suffix == ".none" for f in files) and any(f.suffix == ".tif" for f in files)


def test_a_mask_build_split_by_its_time_limit_gives_the_same_mask(tmp_path, monkeypatch):
    from h3lowpoints.store import Paused

    wc = blobs((400, 500), 8)
    fake_worldcover(tmp_path, 30, -99, -98.0 + 10 * WPX, 31.0 - 7 * WPX, wc)
    dem = write(tmp_path / "dem.tif", np.zeros((100, 130)), "EPSG:4269",
                from_origin(-98.0 + 5 * PX1 + EPS, 31.0 - 5 * PX1 - EPS, PX1, PX1), -9999.0)
    whole = landcover.build_water_mask(dem, tmp_path / "whole.tif", source=tmp_path)
    clock = {"t": 0.0}
    import time as _time
    monkeypatch.setattr(_time, "monotonic", lambda: clock.__setitem__("t", clock["t"] + 1000.0) or clock["t"])
    parts = 0
    while True:
        try:
            out = landcover.build_water_mask(dem, tmp_path / "parts.vrt", source=tmp_path, tile=20, hours=1)
            break
        except Paused as stop:
            parts += 1
            assert not (tmp_path / "parts.vrt").exists() and stop.total == 35
        assert parts < 50
    assert parts >= 3
    monkeypatch.undo()
    with rasterio.open(whole) as a, rasterio.open(out) as b:
        np.testing.assert_array_equal(a.read(1), b.read(1))


def test_the_output_must_be_a_tif_or_a_vrt(tmp_path):
    with pytest.raises(ValueError, match=r"\.tif .*\.vrt"):
        landcover.build_water_mask(tmp_path / "dem.tif", tmp_path / "mask.png")
