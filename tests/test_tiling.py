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
