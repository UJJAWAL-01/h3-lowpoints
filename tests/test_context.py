import numpy as np
import pytest
from rasterio.transform import from_origin
from scipy import ndimage

from h3lowpoints.context.drains import drains_at_core


def drains_at_core_loop(labels, depth, ids, max_depth, drains_xy, transform, core_frac, radius):
    """The original, readable implementation: one window lookup per drain."""
    h, w = labels.shape
    lut = np.zeros(int(labels.max()) + 1)
    lut[ids] = max_depth
    core = np.where((labels > 0) & (depth >= core_frac * lut[labels]), labels, 0)
    inv = ~transform
    found = set()
    for x, y in drains_xy:
        c, r = inv @ (x, y)
        r, c = int(np.floor(r)), int(np.floor(c))
        if r < 0 or c < 0 or r >= h or c >= w:
            continue
        win = core[max(0, r - radius):r + radius + 1, max(0, c - radius):c + radius + 1]
        found.update(np.unique(win[win > 0]).tolist())
    return np.isin(ids, list(found))


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("radius", [1, 3, 5])
def test_vectorised_drains_match_the_loop(seed, radius):
    rng = np.random.default_rng(seed)
    depth = np.clip(ndimage.gaussian_filter(rng.normal(size=(80, 90)), 3) * 6, 0, None)
    labels, n = ndimage.label(depth > 0, structure=np.ones((3, 3)))
    ids = np.arange(1, n + 1)
    mx = np.asarray(ndimage.maximum(depth, labels, ids))
    tr = from_origin(1000.0, 2000.0, 1.0, 1.0)
    xy = np.c_[rng.uniform(995, 1095, 60), rng.uniform(1915, 2005, 60)]   # a few fall outside
    want = drains_at_core_loop(labels, depth, ids, mx, xy, tr, 0.8, radius)
    got = drains_at_core(labels, depth, ids, mx, xy, tr, 0.8, radius)
    np.testing.assert_array_equal(got, want)
    assert n > 1   # the comparison is only meaningful with several depressions
