import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints.cli import main


def test_cli_runs_and_writes_outputs(dem_path, tmp_path):
    out = tmp_path / "out"
    assert main(["analyze", dem_path, "--out", str(out)]) == 0
    for name in ("depressions.csv", "cells.csv", "meta.json"):
        assert (out / name).exists()


def test_cli_with_csv_drains(dem_path, tmp_path):
    pytest.importorskip("geopandas")
    csv = tmp_path / "drains.csv"
    csv.write_text("x,y\n500027.5,3349972.5\n")
    out = tmp_path / "out"
    assert main(["analyze", dem_path, "--drains", str(csv), "--out", str(out)]) == 0
    assert pd.read_csv(out / "depressions.csv").iloc[0]["cls"] == "drained"


def test_rejects_geographic_crs(tmp_path):
    p = tmp_path / "geo.tif"
    prof = {"driver": "GTiff", "height": 10, "width": 10, "count": 1, "dtype": "float32",
            "crs": "EPSG:4326", "transform": from_origin(-97.0, 30.0, 0.0001, 0.0001), "nodata": -9999.0}
    with rasterio.open(p, "w", **prof) as dst:
        dst.write(np.zeros((10, 10), dtype="float32"), 1)
    with pytest.raises(SystemExit):
        main(["analyze", str(p), "--out", str(tmp_path / "out")])
