import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze, reproject_to_utm, rollup
from h3lowpoints.viz.deck import cells_to_geojson, colour_ramp


def test_rollup_conserves_area_and_volume(dem_path):
    res = analyze(dem_path, res=12)
    coarse = res.at_resolution(10)
    assert len(coarse) < len(res.cells)
    assert coarse["depress_area_m2"].sum() == pytest.approx(res.cells["depress_area_m2"].sum())
    assert coarse["depress_volume_m3"].sum() == pytest.approx(
        res.cells["depress_volume_m3"].sum())
    assert coarse["max_depth_m"].max() == pytest.approx(res.cells["max_depth_m"].max())


def test_rollup_matches_analysis_at_the_coarse_resolution(dem_path):
    rolled = analyze(dem_path, res=12).at_resolution(10)
    direct = analyze(dem_path, res=10).cells
    # Both assign each pixel to the cell holding its centre, so the totals agree exactly.
    assert rolled["depress_area_m2"].sum() == pytest.approx(direct["depress_area_m2"].sum())


def test_rollup_rejects_a_finer_resolution(dem_path):
    cells = analyze(dem_path, res=10).cells
    with pytest.raises(ValueError, match="coarser"):
        rollup(cells, 12)


def test_geojson_export(dem_path, tmp_path):
    cells = analyze(dem_path, res=11).cells
    out = cells_to_geojson(cells, tmp_path / "cells.geojson")
    assert len(out["features"]) == len(cells)
    f = out["features"][0]
    ring = f["geometry"]["coordinates"][0]
    assert f["geometry"]["type"] == "Polygon" and ring[0] == ring[-1]
    assert set(f["properties"]) == {"max_depth_m", "depress_area_m2", "depress_volume_m3"}
    assert json.loads((tmp_path / "cells.geojson").read_text())["type"] == "FeatureCollection"


def test_colour_ramp_runs_light_to_dark():
    rgb = colour_ramp([0.0, 0.5, 1.0])
    assert rgb[0].sum() > rgb[1].sum() > rgb[2].sum()


def test_deck_builds_without_a_browser(dem_path):
    pytest.importorskip("pydeck")
    deck = analyze(dem_path, res=11).to_deck()
    assert "H3HexagonLayer" in deck.to_json()


def test_reproject_geographic_dem_to_utm(tmp_path):
    src = tmp_path / "geo.tif"
    arr = np.full((80, 80), 100.0, dtype="float32")
    arr[30:45, 30:45] = 97.0
    prof = {"driver": "GTiff", "height": 80, "width": 80, "count": 1, "dtype": "float32",
            "crs": "EPSG:4326", "transform": from_origin(-97.75, 30.30, 0.00001, 0.00001),
            "nodata": -9999.0}
    with rasterio.open(src, "w", **prof) as dst:
        dst.write(arr, 1)
    geographic = analyze(str(src)).depressions       # analyzed in degrees, no resampling
    out = reproject_to_utm(src, tmp_path / "utm.tif")
    with rasterio.open(out) as r:
        assert r.crs.to_epsg() == 32614   # UTM zone 14N
    projected = analyze(str(out)).depressions
    assert len(geographic) == len(projected) == 1
    # Native: 15 x 15 pixels, each 1e-5 degrees wide (about 1.07 m2 at this latitude).
    assert geographic["area_m2"].iloc[0] == pytest.approx(225 * 1.066, rel=0.02)
    # Resampling blurs the pit's sharp rim into extra shallow pixels, so the projected copy is
    # only roughly comparable. That blur is why geographic DEMs are analyzed natively.
    assert geographic["area_m2"].iloc[0] == pytest.approx(projected["area_m2"].iloc[0], rel=0.25)


def test_quickstart_example_runs(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "quickstart", Path(__file__).parent.parent / "examples" / "quickstart.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.main(tmp_path)
    assert len(res.depressions) == 3
    assert res.depressions["likely_building_artifact"].sum() == 1
    assert res.depressions["drain_at_core"].sum() == 1
    assert (tmp_path / "cells.csv").exists()
