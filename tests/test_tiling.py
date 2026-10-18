import pytest
import numpy as np
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze, analyze_tiled
from h3lowpoints.cli import main


def _dem(path):
    arr = np.full((100, 100), 10.0, dtype="float32")
    arr[10:20, 10:20] = 9.5
    arr[40:50, 45:55] = 9.6   # crosses the tile boundary at column 50
    arr[70:80, 70:85] = 9.7
    prof = {"driver": "GTiff", "height": 100, "width": 100, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1), "nodata": -9999.0}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)


def test_tiled_matches_whole(tmp_path):
    p = tmp_path / "dem.tif"
    _dem(p)
    keys = ["area_m2", "max_depth_m"]
    whole = analyze(str(p))
    tiled = analyze_tiled(str(p), tile=50, buffer=10)
    a = whole.depressions.sort_values(keys).reset_index(drop=True)
    b = tiled.depressions.sort_values(keys).reset_index(drop=True)
    assert len(a) == len(b) == 3
    np.testing.assert_allclose(a["max_depth_m"], b["max_depth_m"], atol=1e-6)
    np.testing.assert_allclose(a["area_m2"], b["area_m2"])
    assert not b["edge"].any()
    np.testing.assert_allclose(whole.cells["depress_area_m2"].sum(), tiled.cells["depress_area_m2"].sum())


def test_cli_tiled(dem_path, tmp_path):
    out = tmp_path / "out"
    assert main(["analyze", dem_path, "--tile", "30", "--buffer", "10", "--out", str(out)]) == 0
    assert (out / "depressions.csv").exists()


def _random_dem(path, seed, size=400):
    from scipy import ndimage

    rng = np.random.default_rng(seed)
    arr = (ndimage.gaussian_filter(rng.normal(size=(size, size)), 2.0) * 10
           + np.linspace(0, 4, size)).astype("float32")
    prof = {"driver": "GTiff", "height": size, "width": size, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1), "nodata": -9999.0}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)


def _keyed(df):
    return df.round({"lat": 7, "lon": 7}).sort_values(["lat", "lon"]).reset_index(drop=True)


@pytest.mark.parametrize("seed", range(4))
def test_tiled_equals_whole_when_buffer_covers_the_data(tmp_path, seed):
    p = tmp_path / "dem.tif"
    _random_dem(p, seed)
    whole = analyze(str(p)).depressions
    tiled = analyze_tiled(str(p), tile=100, buffer=400).depressions
    # With no artificial cut left, tiling reproduces the whole run exactly.
    assert not whole["level_uncertain"].any() and not tiled["level_uncertain"].any()
    assert whole["edge"].sum() == tiled["edge"].sum()
    a, b = _keyed(whole.query("not edge")), _keyed(tiled.query("not edge"))
    assert len(a) > 5 and len(a) == len(b)
    for col in ("max_depth_m", "area_m2", "volume_m3"):
        np.testing.assert_allclose(a[col], b[col], atol=1e-6)


@pytest.mark.parametrize("seed", range(4))
def test_narrow_buffer_never_returns_a_wrong_unflagged_depression(tmp_path, seed):
    # With a narrow buffer a tile can see a lower lake than the whole raster does and split one
    # depression into several. Those pieces must be flagged `level_uncertain`, never presented
    # as settled.
    p = tmp_path / "dem.tif"
    _random_dem(p, seed)
    whole = _keyed(analyze(str(p)).depressions)
    tiled = _keyed(analyze_tiled(str(p), tile=100, buffer=40)
                   .depressions.query("not edge and not level_uncertain"))
    merged = whole.merge(tiled, on=["lat", "lon"], suffixes=("_w", "_t"))
    assert len(merged) == len(tiled)  # every trusted tiled depression exists in the whole run
    np.testing.assert_allclose(merged["max_depth_m_w"], merged["max_depth_m_t"], atol=1e-6)
    np.testing.assert_allclose(merged["area_m2_w"], merged["area_m2_t"])


def test_flagging_shrinks_as_buffer_grows(tmp_path):
    # `level_uncertain` is conservative: it marks every depression whose level depends on a tile
    # cut, because the data beyond the cut is unknown. More buffer only resolves flags.
    p = tmp_path / "dem.tif"
    _random_dem(p, 0)
    flagged = [int(analyze_tiled(str(p), tile=100, buffer=b)
                   .depressions["level_uncertain"].sum()) for b in (20, 60, 150, 400)]
    assert flagged == sorted(flagged, reverse=True)
