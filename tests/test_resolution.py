import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze
from h3lowpoints.resolution import (check_resolution, metres_to_cells, min_h3_resolution,
                                    resolution_tier)


def _write(path, arr, cell):
    prof = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": "float32", "crs": "EPSG:26914",
            "transform": from_origin(500000, 3350000, cell, cell), "nodata": -9999.0}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)


def _pit_dem(path, cell, n=60):
    arr = np.full((n, n), 100.0, dtype="float32")
    arr[20:35, 20:35] = 95.0
    _write(path, arr, cell)


def test_tiers():
    sizes = (1, 2, 5, 12, 30)
    assert [resolution_tier(s) for s in sizes] == ["fine", "fine", "medium", "medium", "coarse"]


def test_metres_to_cells_is_resolution_independent():
    assert metres_to_cells(5.0, 1.0) == 5
    assert metres_to_cells(5.0, 10.0) == 1   # never less than one cell
    assert metres_to_cells(5.0, 0.5) == 10


def test_min_h3_resolution_gets_coarser_with_cell_size():
    assert min_h3_resolution(1.0) > min_h3_resolution(10.0) > min_h3_resolution(30.0)
    assert min_h3_resolution(1.0) >= 11   # res 11 cells hold thousands of 1 m pixels


def test_fine_dem_gives_no_warning(recwarn):
    check_resolution(1.0, 11, 0.15)
    assert not recwarn.list


def test_h3_resolution_too_fine_for_dem_warns():
    with pytest.warns(UserWarning, match="fewer than 4 pixels"):
        check_resolution(30.0, 13, 5.0)


def test_coarse_dem_warns_about_vertical_error():
    with pytest.warns(UserWarning, match="vertical error"):
        check_resolution(30.0, 8, 0.15)


def test_tier_is_recorded_in_meta(tmp_path):
    fine, coarse = tmp_path / "fine.tif", tmp_path / "coarse.tif"
    _pit_dem(fine, 1.0)
    _pit_dem(coarse, 10.0)
    assert analyze(str(fine)).meta["resolution_tier"] == "fine"
    assert analyze(str(coarse), res=9, min_depth=1.0).meta["resolution_tier"] == "medium"


@pytest.mark.parametrize("cell", [0.5, 1.0])
def test_drain_radius_is_in_metres(tmp_path, cell):
    # The same distances must give the same answer at 0.5 m and at 1 m cells.
    n = int(60 / cell)
    arr = np.full((n, n), 10.0, dtype="float32")
    lo, hi = int(25 / cell), int(35 / cell)
    arr[lo:hi, lo:hi] = 9.0                       # the pit spans x = 25..35 m
    p = tmp_path / "d.tif"
    _write(p, arr, cell)
    near = np.array([[500000 + 35 + 4.0, 3350000 - 30]])   # 4 m beyond the pit's east edge
    far = np.array([[500000 + 35 + 8.0, 3350000 - 30]])    # 8 m beyond it
    assert analyze(str(p), drains_xy=near, drain_radius_m=5.0, drain_core_frac=0.0).depressions["drain_at_core"].all()
    assert not analyze(str(p), drains_xy=far, drain_radius_m=5.0, drain_core_frac=0.0).depressions["drain_at_core"].any()
