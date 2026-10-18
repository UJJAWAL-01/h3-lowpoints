import numpy as np

from h3lowpoints.hydro.depressions import lowest_points


def test_lowest_point_is_window_independent():
    big = np.zeros((60, 80))
    big[20:30, 40:55] = 0.4  # flat floor: every cell is tied for deepest
    r1, c1 = lowest_points(big, (big > 0).astype(int), np.array([1]))
    small = big[10:50, 30:70]  # the same depression seen through a smaller window
    r2, c2 = lowest_points(small, (small > 0).astype(int), np.array([1]))
    assert (r1[0], c1[0]) == (20, 40)
    assert (r2[0] + 10, c2[0] + 30) == (20, 40)
