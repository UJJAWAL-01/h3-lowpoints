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


def test_unexplained_then_drained(tmp_path):
    p = tmp_path / "dem.tif"
    _make_dem(p)
    res = analyze(str(p))
    assert len(res.depressions) == 1
    assert res.depressions.iloc[0]["cls"] == "unexplained"
    res2 = analyze(str(p), drains_xy=np.array([[500027.5, 3349972.5]]))
    assert res2.depressions.iloc[0]["cls"] == "drained"
    assert len(res2.cells) >= 1
