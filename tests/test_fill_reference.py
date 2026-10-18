"""Check the priority-flood fill against an independent method.

Morphological reconstruction by erosion (Soille) fills depressions by a different route: it
lowers an upper bound until it can go no lower without dropping below the terrain. Agreement on
random terrain is strong evidence the fill is right.
"""
import numpy as np
import pytest
from scipy import ndimage

from h3lowpoints.hydro.depressions import depth_from_array, label_depressions


def reference_depth(arr):
    """Depth by iterated 8-neighbour erosion; the border is the only outlet."""
    marker = np.full(arr.shape, arr.max() + 1.0)
    marker[0, :], marker[-1, :], marker[:, 0], marker[:, -1] = (
        arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1])
    while True:
        eroded = np.maximum(ndimage.grey_erosion(marker, size=(3, 3)), arr)
        eroded[0, :], eroded[-1, :], eroded[:, 0], eroded[:, -1] = (
            arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1])
        if np.array_equal(eroded, marker):
            return marker - arr
        marker = eroded


def random_terrain(seed, shape=(60, 70), sigma=2.0):
    rng = np.random.default_rng(seed)
    return ndimage.gaussian_filter(rng.normal(size=shape), sigma) * 10 + np.linspace(0, 3, shape[1])


@pytest.mark.parametrize("seed", range(8))
def test_fill_matches_reference_on_random_terrain(seed):
    arr = random_terrain(seed)
    np.testing.assert_allclose(depth_from_array(arr), reference_depth(arr), atol=1e-9)


def test_nodata_is_an_outlet():
    arr = np.full((20, 20), 10.0)
    arr[5:10, 5:10] = 9.0
    mask = np.zeros(arr.shape, dtype=bool)
    mask[7, 4] = True  # a void touching the pit lets the water out
    assert depth_from_array(arr, mask).max() == 0.0


def test_corner_touching_pits_are_one_depression():
    arr = np.full((30, 30), 10.0)
    arr[5:10, 5:10] = 9.0
    arr[10:15, 10:15] = 9.0
    _, st = label_depressions(depth_from_array(arr), 1.0, 0.15)
    assert len(st["id"]) == 1


def _two_basins(outlet):
    """Basin A (floor 8.0) joins basin B (floor 8.5) over a 9.0 saddle; B drains over `outlet`."""
    arr = np.full((40, 60), 12.0)
    arr[11:29, 11:24] = 8.0
    arr[11:29, 26:44] = 8.5
    arr[11:29, 24:26] = 9.0
    arr[20, 44:] = outlet
    return arr


def test_basins_whose_lake_rises_above_the_saddle_are_one_depression():
    # B overflows at 9.5, above the saddle, so one lake covers both and the saddle is submerged.
    depth = depth_from_array(_two_basins(outlet=9.5))
    _, st = label_depressions(depth, 1.0, 0.15)
    assert len(st["id"]) == 1
    assert depth[20, 24] == pytest.approx(0.5)


def test_basins_whose_lake_stops_at_the_saddle_are_two_depressions():
    # B overflows at 9.0, exactly the saddle height, so the saddle stays dry between two lakes.
    _, st = label_depressions(depth_from_array(_two_basins(outlet=9.0)), 1.0, 0.15)
    assert len(st["id"]) == 2
