import numpy as np
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze


def _make_dem(path):
    arr = np.full((60, 60), 10.0, dtype="float32")
    arr[20:35, 20:35] = 9.5
    prof = dict(driver="GTiff", height=60, width=60, count=1, dtype="float32", crs="EPSG:26914",
                transform=from_origin(500000, 3350000, 1, 1), nodata=-9999.0)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)


def test_drain_at_the_deepest_part_is_reported(tmp_path):
    p = tmp_path / "dem.tif"
    _make_dem(p)
    res = analyze(str(p))
    assert len(res.depressions) == 1
    assert "drain_at_core" not in res.depressions   # no drain data given: no claim made
    inside = analyze(str(p), drains_xy=np.array([[500027.5, 3349972.5]])).depressions.iloc[0]
    # the reported lowest point of a flat floor is its first cell, here a corner ~10 m away
    assert bool(inside["drain_at_core"]) and inside["dist_to_drain_m"] < 15
    outside = analyze(str(p), drains_xy=np.array([[500055.5, 3349972.5]])).depressions.iloc[0]
    assert not bool(outside["drain_at_core"]) and outside["dist_to_drain_m"] > 20


def test_building_overlap(tmp_path):
    from shapely.geometry import box

    p = tmp_path / "dem.tif"
    _make_dem(p)
    res = analyze(str(p), buildings=[box(500018, 3349962, 500038, 3349982)])
    row = res.depressions.iloc[0]
    assert row["building_overlap"] > 0.99
    assert bool(row["likely_building_artifact"])
    res2 = analyze(str(p))
    assert "building_overlap" not in res2.depressions   # no footprints given: no claim made
