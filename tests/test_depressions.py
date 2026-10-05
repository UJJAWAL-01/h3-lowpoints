import numpy as np

from h3lowpoints.hydro.depressions import depth_from_array


def test_pit_depth():
    arr = np.full((30, 30), 10.0)
    arr[10:15, 10:15] = 9.5
    assert abs(depth_from_array(arr).max() - 0.5) < 1e-3


def test_flat_has_no_depth():
    assert depth_from_array(np.full((20, 20), 5.0)).max() == 0.0


def test_drain_removes_depression():
    arr = np.full((30, 30), 10.0)
    arr[10:15, 10:15] = 9.5
    drains = np.zeros(arr.shape, dtype=bool)
    drains[12, 12] = True
    assert depth_from_array(arr, drains=drains).max() == 0.0
