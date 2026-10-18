"""The general verifier, the region builder (3DEP virtual rasters) and their commands."""
import json

import h3
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage

from h3lowpoints import analyze, analyze_tiled
from h3lowpoints import verify as V
from h3lowpoints.cli import main
from h3lowpoints.sources import usgs
from test_sources import PX1, f, fake_3dep_tile


def dem(path, seed=0, shape=(120, 140), nodata_box=True):
    rng = np.random.default_rng(seed)
    arr = ndimage.gaussian_filter(rng.normal(size=shape), 2.0) * 10 + np.linspace(0, 3, shape[1])
    if nodata_box:
        arr[10:30, 90:120] = -9999.0
    prof = {"driver": "GTiff", "height": shape[0], "width": shape[1], "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1), "nodata": -9999.0}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype("float32"), 1)
    return str(path)


@pytest.fixture
def good(tmp_path):
    return analyze(dem(tmp_path / "d.tif"), res=12, min_depth=0.1)


# ------------------------------------------------------------------ correct results pass

def test_correct_results_pass(tmp_path, good):
    assert V.check_result(good) == []
    p = dem(tmp_path / "e.tif", seed=4)
    assert V.check_result(analyze_tiled(p, tile=33, res=11, min_depth=0.05)) == []
    assert V.check_result(analyze(p, min_depth=0.05)) == []              # res="auto" -> 14


def test_a_result_with_no_depressions_passes(tmp_path):
    flat = tmp_path / "flat.tif"
    prof = {"driver": "GTiff", "height": 20, "width": 20, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1)}
    with rasterio.open(flat, "w", **prof) as dst:
        dst.write(np.zeros((20, 20), dtype="float32"), 1)
    assert V.check_result(analyze(str(flat))) == []


# ------------------------------------------------------------------ every corruption is caught

def _neighbour(cell):
    return next(c for c in h3.grid_ring(cell, 1))


CORRUPTIONS = {
    "drop a cell": (lambda d, c, m: (d, c.iloc[1:], m), "cell areas add to"),
    "double an area": (lambda d, c, m: (d.assign(area_m2=d["area_m2"] * 2), c, m), "cell areas add to"),
    "repeat an id": (lambda d, c, m: (d.assign(id=1), c, m), "ids are not unique"),
    "NaN depth": (lambda d, c, m: (d.assign(max_depth_m=np.where(d.index == 0, np.nan, d["max_depth_m"])), c, m), "NaN"),
    "below min_depth": (lambda d, c, m: (d.assign(max_depth_m=np.where(d.index == 0, 0.01, d["max_depth_m"])), c, m), "shallower than min_depth"),
    "volume too big": (lambda d, c, m: (d.assign(volume_m3=d["volume_m3"] * 1e6), c, m), "volume is negative or larger"),
    "lowpoint wrong res": (lambda d, c, m: (d.assign(h3_lowpoint=[h3.cell_to_parent(x, 9) for x in d["h3_lowpoint"]]), c, m), "not at the table's resolution"),
    "lowpoint_15 moved": (lambda d, c, m: (d.assign(h3_lowpoint_15=[_neighbour(x) for x in d["h3_lowpoint_15"]]), c, m), "do not contain their lowest point"),
    "count off": (lambda d, c, m: (d, c.assign(n_lowpoints=c["n_lowpoints"] + 1), m), "n_lowpoints adds to"),
    "coverage above 1": (lambda d, c, m: (d, c.assign(coverage=np.where(c.index == 0, 1.5, c["coverage"])), m), "coverage outside"),
    "covered area off": (lambda d, c, m: (d, c.assign(covered_area_m2=c["covered_area_m2"] * 0.5), m), "covered_area_m2 is not coverage"),
    "share above 1": (lambda d, c, m: (d, c.assign(depressed_share=2.0), m), "depressed_share outside"),
    "meta count": (lambda d, c, m: (d, c, {**m, "n_depressions": m["n_depressions"] + 3}), "meta says"),
    "cells at mixed res": (lambda d, c, m: (d, c.assign(h3=[h3.cell_to_parent(x, 10) if i == 0 else x for i, x in enumerate(c["h3"])]), m), "not all at resolution"),
    "lowpoint outside the table": (lambda d, c, m: (d.assign(h3_lowpoint=[x if i else _neighbour(x) for i, x in enumerate(d["h3_lowpoint"])]), c, m), "lowest points"),
}


@pytest.mark.parametrize("name", list(CORRUPTIONS))
def test_every_corruption_is_caught(good, name):
    corrupt, message = CORRUPTIONS[name]
    d, c, m = corrupt(good.depressions.copy(), good.cells.copy(), dict(good.meta))
    problems = V.check_tables(d, c, m)
    assert any(message in p for p in problems), problems


# ------------------------------------------------------------------ saved results and comparisons

def test_saved_results(tmp_path, good):
    folder = good.save(tmp_path / "out")
    assert V.check_saved(folder) == []
    cells = (folder / "cells.csv").read_text().splitlines()
    (folder / "cells.csv").write_text("\n".join(cells[:-1]) + "\n")      # lose the last cell
    assert any("cell areas add to" in p for p in V.check_saved(folder))
    assert "missing" in V.check_saved(tmp_path / "nowhere")[0]


def test_compare_results(tmp_path, good):
    assert V.compare_results(good, good) == []
    other = analyze(dem(tmp_path / "x.tif", seed=9), res=12, min_depth=0.1)
    assert V.compare_results(good, other)


def test_spot_check_agrees_on_any_dem(tmp_path):
    assert V.spot_check(dem(tmp_path / "d.tif"), windows=3, size=80, tile=23, res=12, min_depth=0.1) == []


def test_spot_check_catches_a_tiled_run_that_disagrees(tmp_path, monkeypatch):
    import h3lowpoints.tiling as tiling

    real = tiling.analyze_tiled

    def broken(*a, **k):
        r = real(*a, **k)
        r.depressions["area_m2"] *= 1.01
        return r
    monkeypatch.setattr(tiling, "analyze_tiled", broken)
    problems = V.spot_check(dem(tmp_path / "d.tif"), windows=2, size=80, tile=23, res=12, min_depth=0.1)
    assert problems and all(p.startswith("window ") for p in problems)
    assert any("area_m2 differs" in p for p in problems)


# ------------------------------------------------------------------ the 3DEP virtual raster

PITS = [(-97.0, 31.0, 6.0), (-97 - 15 * PX1, 31 + 9 * PX1, 3.0), (-97 + 12 * PX1, 31 - 14 * PX1, 2.0)]


def pitted(lon, lat):
    """The smooth test surface with three pits, one centred on the corner where four tiles meet."""
    out = f(lon, lat)
    for x, y, depth in PITS:
        out = out - depth * np.exp(-((lon - x) ** 2 + (lat - y) ** 2) / (4 * PX1) ** 2)
    return out


def _corner_tiles(folder):
    fake_3dep_tile(folder, 30, -98, (3560, 3606), (-6, 40), func=pitted)
    fake_3dep_tile(folder, 30, -97, (-6, 40), (-6, 40), func=pitted)
    fake_3dep_tile(folder, 31, -98, (3560, 3606), (3560, 3606), func=pitted)
    fake_3dep_tile(folder, 31, -97, (-6, 40), (3560, 3606), func=pitted)
    return (-97 - 30 * PX1, 31 - 30 * PX1, -97 + 30 * PX1, 31 + 30 * PX1)


def test_vrt_equals_the_in_memory_mosaic(tmp_path):
    box = _corner_tiles(tmp_path)
    vrt = usgs.build_3dep_vrt(box, tmp_path / "r.vrt", source=tmp_path)
    tif = usgs.fetch_3dep(box, tmp_path / "r.tif", source=tmp_path)
    with rasterio.open(vrt) as a, rasterio.open(tif) as b:
        assert a.transform == b.transform and a.crs == b.crs and a.nodata == b.nodata
        np.testing.assert_array_equal(a.read(1), b.read(1))
        xs = a.transform.c + (np.arange(a.width) + 0.5) * a.transform.a
        ys = a.transform.f + (np.arange(a.height) + 0.5) * a.transform.e
        np.testing.assert_allclose(a.read(1), pitted(xs[None, :], ys[:, None]), atol=1e-3)


def test_vrt_with_a_missing_tile_and_bad_arguments(tmp_path):
    fake_3dep_tile(tmp_path, 30, -98, (3560, 3606), (-6, 40))
    vrt = usgs.build_3dep_vrt((-97 - 30 * PX1, 31 - 30 * PX1, -97 + 30 * PX1, 31 - PX1),
                              tmp_path / "r.vrt", source=tmp_path)
    with rasterio.open(vrt) as r:
        a = r.read(1)
    assert (a[:, :30] != usgs.NODATA).all() and (a[:, 30:] == usgs.NODATA).all()
    with pytest.raises(ValueError, match="No 3DEP tiles"):
        usgs.build_3dep_vrt((-30.5, 0.5, -30.2, 0.8), tmp_path / "x.vrt", source=tmp_path)
    with pytest.raises(ValueError, match="30 or 10"):
        usgs.build_3dep_vrt((-98, 30, -97.9, 30.1), tmp_path / "x.vrt", resolution=5)


def test_analysis_of_a_vrt_equals_analysis_of_the_mosaic(tmp_path):
    box = _corner_tiles(tmp_path)
    vrt = usgs.build_3dep_vrt(box, tmp_path / "r.vrt", source=tmp_path)
    tif = usgs.fetch_3dep(box, tmp_path / "r.tif", source=tmp_path)
    a = analyze_tiled(str(vrt), tile=17, res=10, min_depth=0.01)
    b = analyze(str(tif), res=10, min_depth=0.01)
    assert len(a.depressions) >= 3                       # the comparison is not of two empty tables
    assert V.compare_results(a, b) == [] and V.check_result(a) == []


def test_tile_cache_downloads_once_and_never_leaves_a_partial_file(tmp_path, monkeypatch):
    src = fake_3dep_tile(tmp_path, 30, -98, (0, 20), (0, 20))
    calls = []

    class Resp:
        def __init__(self):
            self.f = open(src, "rb")

        def read(self, n=-1):
            return self.f.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    monkeypatch.setattr(usgs, "url_exists", lambda url: True)
    monkeypatch.setattr(usgs.urllib.request, "urlopen", lambda req, timeout=None: calls.append(1) or Resp())
    cache = tmp_path / "cache"
    first = usgs._download(usgs.tile_url(30, -98), cache)
    second = usgs._download(usgs.tile_url(30, -98), cache)
    assert first == second and len(calls) == 1
    assert not list(cache.glob("*.part"))
    assert open(first, "rb").read() == open(src, "rb").read()
    monkeypatch.setattr(usgs, "url_exists", lambda url: False)
    assert usgs._download(usgs.tile_url(40, -100), cache) is None


# ------------------------------------------------------------------ commands

def test_cli_verify(tmp_path, good, capsys):
    folder = good.save(tmp_path / "out")
    assert main(["verify", str(folder)]) == 0
    assert "all invariants hold" in capsys.readouterr().out
    meta = json.loads((folder / "meta.json").read_text())
    meta["n_depressions"] += 1
    (folder / "meta.json").write_text(json.dumps(meta))
    assert main(["verify", str(folder)]) == 1
    assert "meta says" in capsys.readouterr().out


def test_cli_verify_with_a_spot_check(tmp_path, capsys):
    p = dem(tmp_path / "d.tif")
    folder = analyze(p, res=12, min_depth=0.1).save(tmp_path / "out")
    assert main(["verify", str(folder), "--dem", p, "--windows", "2"]) == 0
    assert "agree on 2 random windows" in capsys.readouterr().out


def test_cli_build_from_local_tiles(tmp_path, monkeypatch, capsys):
    box = _corner_tiles(tmp_path / "tiles") if (tmp_path / "tiles").mkdir() is None else None
    real = usgs.build_3dep_vrt
    monkeypatch.setattr(usgs, "build_3dep_vrt",
                        lambda bbox, path, **k: real(bbox, path, resolution=k.get("resolution", 30),
                                                     source=tmp_path / "tiles"))
    out = tmp_path / "region"
    code = main(["build", "--bbox", *map(str, box), "--out", str(out), "--res", "10", "--min-depth", "0.01",
                 "--tile", "17", "--spot-check", "1", "--map", str(tmp_path / "m.html")])
    printed = capsys.readouterr().out
    assert code == 0 and "all invariants hold" in printed
    assert json.loads((out / "verify.json").read_text()) == {"problems": []}
    meta = json.loads((out / "meta.json").read_text())
    assert meta["build"]["dataset"] == "3dep" and set(meta["build"]["seconds"]) == {"tiles", "analysis"}
    assert (out / "dem.vrt").exists() and (tmp_path / "m.html").exists()


@pytest.mark.network
def test_live_build_of_a_small_region(tmp_path, capsys):
    out = tmp_path / "region"
    assert main(["build", "--bbox", "-97.02", "30.98", "-96.98", "31.02", "--out", str(out),
                 "--min-depth", "0.3", "--tile", "64", "--spot-check", "2"]) == 0
    assert "all invariants hold" in capsys.readouterr().out
    assert V.check_saved(out) == []
