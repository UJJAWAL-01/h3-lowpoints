"""Clipping a saved result to a boundary: consistent tables, exact membership, any format."""
import io
import json
import zipfile

import h3
import pandas as pd
import pytest
from shapely.geometry import Point, box

from h3lowpoints import analyze_tiled, read_result
from h3lowpoints.cli import main
from h3lowpoints.clip import cells_inside, clip_result
from h3lowpoints.sources import boundaries
from h3lowpoints.verify import check_saved
from test_store import FORMATS
from test_verify import CORRUPTIONS, dem


@pytest.fixture
def saved(tmp_path):
    out = tmp_path / "full"
    analyze_tiled(dem(tmp_path / "d.tif", seed=2), tile=40, res=12, min_depth=0.1, out_dir=out)
    return out


def half(folder):
    """A box covering the western half of the result's lowest points (lon/lat)."""
    d = read_result(folder).depressions
    mid = float(d["lon"].median())
    return box(-180, -90, mid, 90)


def test_membership_is_the_cell_centre(saved):
    cells = list(read_result(saved).cells["h3"])
    geom = half(saved)
    got = cells_inside(cells, geom)
    want = [geom.contains(Point(h3.cell_to_latlng(c)[::-1])) for c in cells]      # shapely, one by one
    assert list(got) == want and got.any() and not got.all()


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_clipped_result_is_consistent(saved, tmp_path, fmt):
    full = read_result(saved)
    geom = half(saved)
    out = clip_result(saved, geom, tmp_path / "clipped", format=fmt, name="west")
    clipped = read_result(out)
    inside = cells_inside(list(full.cells["h3"]), geom)
    pd.testing.assert_frame_equal(clipped.cells, full.cells[inside].reset_index(drop=True),
                                  check_dtype=False)
    kept = set(clipped.cells["h3"])
    want = full.depressions[full.depressions["h3_lowpoint"].isin(kept)].reset_index(drop=True)
    pd.testing.assert_frame_equal(clipped.depressions, want, check_dtype=False)
    assert 0 < len(clipped.depressions) < len(full.depressions)
    assert clipped.meta["clip"]["boundary"] == "west"
    assert clipped.meta["clip"]["depressions_before"] == len(full.depressions)
    assert clipped.meta["n_depressions"] == len(clipped.depressions)
    assert clipped.meta["output"] == {"format": fmt, "n_cells": len(clipped.cells)}
    assert check_saved(out) == []                         # every invariant a clip keeps holds
    again = clip_result(out, geom, tmp_path / "again", format=fmt)   # clipping twice changes nothing
    pd.testing.assert_frame_equal(read_result(again).cells, clipped.cells)


@pytest.mark.parametrize("name", ["count off", "repeat an id", "coverage above 1", "cells at mixed res",
                                  "lowpoint outside the table", "NaN depth"])
def test_a_clipped_result_is_still_checked(saved, tmp_path, name):
    from h3lowpoints.pipeline import Result

    clipped = read_result(clip_result(saved, half(saved), tmp_path / "clipped"))
    corrupt, message = CORRUPTIONS[name]
    d, c, m = corrupt(clipped.depressions.copy(), clipped.cells.copy(), dict(clipped.meta))
    Result(d, c, m).save(tmp_path / "bad")
    assert any(message in p for p in check_saved(tmp_path / "bad")), name


def test_a_boundary_file_in_another_crs(saved, tmp_path):
    import geopandas as gpd

    geom = half(saved)
    gpd.GeoDataFrame(geometry=[geom.intersection(box(-120, 20, -60, 50))], crs=4326).to_crs(3857).to_file(
        tmp_path / "b.gpkg")
    a = read_result(clip_result(saved, tmp_path / "b.gpkg", tmp_path / "from_file"))
    b = read_result(clip_result(saved, geom, tmp_path / "from_geom"))
    assert set(a.cells["h3"]) == set(b.cells["h3"])
    assert a.meta["clip"]["boundary"] == "b.gpkg"


def test_nothing_inside_and_bad_arguments(saved, tmp_path):
    out = clip_result(saved, box(0, 0, 1, 1), tmp_path / "empty")
    r = read_result(out)
    assert len(r.cells) == len(r.depressions) == 0 and check_saved(out) == []
    with pytest.raises(ValueError, match="another folder"):
        clip_result(saved, box(0, 0, 1, 1), saved)


def _census_zip(tmp_path):
    import geopandas as gpd

    states = gpd.GeoDataFrame(
        {"STUSPS": ["TX", "OK", "AK", "HI", "PR"]},
        geometry=[box(-106, 26, -94, 36), box(-103, 34, -94, 37), box(-170, 55, -140, 70),
                  box(-160, 19, -154, 22), box(-67, 18, -65, 18.5)], crs=4269)
    states.to_file(tmp_path / "cb_states.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in tmp_path.glob("cb_states.*"):
            z.write(f, f.name)
    return buf.getvalue()


def test_the_lower_48_leave_out_alaska_hawaii_and_territories(tmp_path):
    import shapely

    geom = boundaries.us_lower48(tmp_path / "us48.gpkg", data=_census_zip(tmp_path))
    assert shapely.contains_xy(geom, -97.0, 31.0) and shapely.contains_xy(geom, -98.0, 36.5)
    assert not shapely.contains_xy(geom, -150.0, 61.0) and not shapely.contains_xy(geom, -157.0, 21.0)
    assert not shapely.contains_xy(geom, -66.0, 18.2)
    assert (tmp_path / "us48.gpkg").exists()


def test_cli_clip_to_a_boundary_file(saved, tmp_path, capsys):
    import geopandas as gpd

    gpd.GeoDataFrame(geometry=[half(saved)], crs=4326).to_file(tmp_path / "west.geojson")
    assert main(["clip", str(saved), "--boundary", str(tmp_path / "west.geojson"),
                 "--out", str(tmp_path / "c")]) == 0
    printed = capsys.readouterr().out
    assert "depressions and" in printed and "all invariants hold" in printed
    assert json.loads((tmp_path / "c" / "meta.json").read_text())["clip"]["boundary"] == "west.geojson"


@pytest.mark.network
def test_live_census_lower48(tmp_path):
    import shapely

    geom = boundaries.us_lower48()
    for lon, lat, inside in ((-98.5, 39.5, True), (-79.4, 43.7, False), (-149.9, 61.2, False),
                             (-157.8, 21.3, False), (-99.1, 19.4, False), (-77.04, 38.9, True)):
        assert bool(shapely.contains_xy(geom, lon, lat)) is inside, (lon, lat)
