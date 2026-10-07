import numpy as np

from h3lowpoints.classify import touches_edge
from h3lowpoints.hydro.depressions import depth_from_array, label_depressions


def test_edge_adjacent_depression_is_flagged():
    arr = np.full((40, 40), 10.0)
    arr[1:6, 20:25] = 9.5      # cut off by the top border
    arr[20:25, 20:25] = 9.5    # interior
    depth = depth_from_array(arr)
    labels, st = label_depressions(depth, 1.0, 0.15)
    ids = np.asarray(st["id"]).astype(int)
    assert len(ids) == 2
    assert touches_edge(labels, ids).sum() == 1
