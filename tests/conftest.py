import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


@pytest.fixture
def dem_path(tmp_path):
    arr = np.full((60, 60), 10.0, dtype="float32")
    arr[20:35, 20:35] = 9.5
    prof = {"driver": "GTiff", "height": 60, "width": 60, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1), "nodata": -9999.0}
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)
    return str(path)


def pytest_collection_modifyitems(config, items):
    """Tests marked `network` need internet access; run them with H3LP_NETWORK=1."""
    import os

    if os.environ.get("H3LP_NETWORK") == "1":
        return
    skip = pytest.mark.skip(reason="needs internet: set H3LP_NETWORK=1 to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
