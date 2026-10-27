"""Soil hydrologic groups from gNATSGO, and curve numbers from NLCD and soil.

Offline tests build fake soil tiles (10 m map-unit grids in the Albers projection gNATSGO uses,
with overviews) and a fake map-unit table, and answer the catalogue and token requests as the
real services do. The groups must land on the requested grid pixel for pixel. Tests marked
`network` read the real survey.
"""
import json

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin

from h3lowpoints.cli import main
from h3lowpoints.runoff import curve_numbers, fetch_curve_numbers
from h3lowpoints.sources import soils

ALBERS = 'PROJCS["USA_Contiguous_Albers_Equal_Area_Conic_USGS_version",GEOGCS["NAD83",DATUM["North_American_Datum_1983",SPHEROID["GRS 1980",6378137,298.257222101]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Albers_Conic_Equal_Area"],PARAMETER["latitude_of_center",23],PARAMETER["longitude_of_center",-96],PARAMETER["standard_parallel_1",29.5],PARAMETER["standard_parallel_2",45.5],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1]]'


def fake_tile(path, x0, y0, n=600, seed=0):
    """A 10 m map-unit tile: blocks of 50 x 50 m (5 pixels) of map units 1 to 9, with
    overviews, as gNATSGO's tiles are made."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(1, 10, (n // 5 + 1, n // 5 + 1))
    data = np.kron(blocks, np.ones((5, 5), dtype=np.int64))[:n, :n].astype("int32")
    data[:7, :] = soils.MUKEY_NODATA
    prof = {"driver": "GTiff", "height": n, "width": n, "count": 1, "dtype": "int32",
            "crs": ALBERS, "transform": from_origin(x0, y0, 10, 10), "nodata": soils.MUKEY_NODATA,
            "tiled": True, "blockxsize": 256, "blockysize": 256}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(data, 1)
        dst.build_overviews([2, 4], Resampling.nearest)
    return path, data


def table_bytes(tmp_path):
    groups = ["A", "B", "C", "D", "A/D", "B/D", "C/D", None, "D"]
    frame = pd.DataFrame({"mukey": np.arange(1, 10, dtype=np.int64), "hydgrpdcd": groups,
                          "muname": [f"unit {i}" for i in range(1, 10)]})
    frame = pd.concat([frame, frame.iloc[[7]]])                 # a repeated row, as in the real table
    path = tmp_path / "t.parquet"
    frame.to_parquet(path, index=False)
    return path.read_bytes()


@pytest.fixture
def fake_survey(tmp_path, monkeypatch):
    """Two tiles side by side near Houston, answers for the catalogue, the token and the table."""
    x0, y0 = 30000.0, 760000.0
    a, da = fake_tile(tmp_path / "a.tif", x0, y0, seed=1)
    b, db = fake_tile(tmp_path / "b.tif", x0 + 6000, y0, seed=2)
    table = table_bytes(tmp_path)
    asked = []

    def get(url):
        asked.append(url)
        if url.startswith(soils.TOKEN):
            return json.dumps({"token": "sv=x&sig=y", "msft:expiry": "later"}).encode()
        if url.startswith(soils.TABLE):
            return table
        if url.startswith(soils.STAC):
            return json.dumps({"features": [{"assets": {"mukey": {"href": "A"}}},
                                            {"assets": {"mukey": {"href": "B"}}}]}).encode()
        raise AssertionError(url)
    monkeypatch.setattr(soils, "_token", {"value": None, "until": 0.0})
    opener = {"A": str(a), "B": str(b)}.get
    return {"get": get, "opener": opener, "asked": asked, "x0": x0, "y0": y0, "data": (da, db)}


def test_groups_land_on_the_nlcd_grid_pixel_for_pixel(tmp_path, fake_survey):
    from pyproj import Transformer

    x0, y0 = fake_survey["x0"], fake_survey["y0"]
    # an "NLCD" grid: 30 m, corners 15 m past multiples of 30, spanning both tiles
    like = tmp_path / "nlcd.tif"
    prof = {"driver": "GTiff", "height": 150, "width": 350, "count": 1, "dtype": "uint8",
            "crs": "EPSG:5070", "transform": from_origin(x0 + 1005, y0 - 435, 30, 30), "nodata": 0}
    with rasterio.open(like, "w", **prof) as dst:
        dst.write(np.full((150, 350), 82, dtype="uint8"), 1)
    out = soils.fetch_soil_groups(None, tmp_path / "g.tif", cache_dir=tmp_path / "cache", like=like,
                                  get=fake_survey["get"], opener=fake_survey["opener"])
    with rasterio.open(out) as src, rasterio.open(like) as ref:
        assert src.transform == ref.transform and src.shape == ref.shape
        got = src.read(1)
    # each pixel centre's map unit, looked up by hand in the fake tiles
    da, db = fake_survey["data"]
    rows, cols = np.mgrid[0:150, 0:350]
    x = x0 + 1005 + (cols + 0.5) * 30
    y = y0 - 435 - (rows + 0.5) * 30
    r = ((y0 - y) // 10).astype(int)
    ca, cb = np.clip(((x - x0) // 10).astype(int), 0, 599), np.clip(((x - x0 - 6000) // 10).astype(int), 0, 599)
    mukey = np.where(x < x0 + 6000, da[r, ca], db[r, cb])
    expected = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 4], dtype=np.uint8)[np.clip(mukey, 0, 9)]
    # every pixel exactly: these centres all lie on the 10 m grid's lines, and take the soil
    # pixel to their east and south (what the floor division above picks)
    assert (got == expected).all()
    assert set(np.unique(got)) <= set(range(9))
    assert len([u for u in fake_survey["asked"] if u.startswith(soils.TOKEN)]) == 1   # token reused
    del Transformer


@pytest.mark.parametrize("shift", [(1, 0), (7, 3), (40, 11)])
def test_a_place_gets_the_same_group_whatever_box_it_is_read_in(tmp_path, fake_survey, shift):
    """Planted bug: reading an overview, or a fractional window, resamples the grid at an offset
    that depends on the box, so the same pixel could get a neighbouring unit."""
    x0, y0 = fake_survey["x0"], fake_survey["y0"]
    grids = []
    for name, (dc, dr), (w, h) in (("a", (0, 0), (300, 120)), ("b", shift, (210, 90))):
        like = tmp_path / f"nlcd_{name}.tif"
        prof = {"driver": "GTiff", "height": h, "width": w, "count": 1, "dtype": "uint8",
                "crs": "EPSG:5070", "nodata": 0,
                "transform": from_origin(x0 + 1005 + 30 * dc, y0 - 435 - 30 * dr, 30, 30)}
        with rasterio.open(like, "w", **prof) as dst:
            dst.write(np.full((h, w), 82, dtype="uint8"), 1)
        out = soils.fetch_soil_groups(None, tmp_path / f"g_{name}.tif", cache_dir=tmp_path / "c",
                                      like=like, get=fake_survey["get"], opener=fake_survey["opener"])
        with rasterio.open(out) as src:
            grids.append(src.read(1))
    a, b = grids
    dc, dr = shift
    assert (a[dr:dr + b.shape[0], dc:dc + b.shape[1]] == b[:a.shape[0] - dr, :a.shape[1] - dc]).all()


def test_default_grid_follows_nlcd_edges(tmp_path, fake_survey):
    from pyproj import Transformer

    x0, y0 = fake_survey["x0"], fake_survey["y0"]
    lon0, lat0 = Transformer.from_crs("EPSG:5070", 4326, always_xy=True).transform(x0 + 2000, y0 - 2000)
    lon1, lat1 = Transformer.from_crs("EPSG:5070", 4326, always_xy=True).transform(x0 + 9000, y0 - 4000)
    out = soils.fetch_soil_groups((lon0, lat1, lon1, lat0), tmp_path / "g.tif", cache_dir=tmp_path,
                                  get=fake_survey["get"], opener=fake_survey["opener"])
    with rasterio.open(out) as src:
        t = src.transform
        assert (t.c - 15) % 30 == 0 and (t.f - 15) % 30 == 0 and t.a == 30
        assert set(np.unique(src.read(1))) - {0} <= set(range(1, 9))


def test_unknown_units_and_no_data():
    keys = np.array([1, 2, 3], dtype=np.int64)
    codes = np.array([1, 4, 8], dtype=np.uint8)
    got = soils.to_groups(np.array([1, 2, 3, 4, soils.MUKEY_NODATA]), keys, codes)
    assert got.tolist() == [1, 4, 8, 0, 0]


def test_curve_numbers_from_nlcd_and_soil(tmp_path, monkeypatch):
    """fetch_curve_numbers joins the three inputs on one grid and refuses grids that differ."""
    from h3lowpoints import runoff

    def raster(path, value, dtype, nodata, shift=0):
        prof = {"driver": "GTiff", "height": 4, "width": 5, "count": 1, "dtype": dtype,
                "crs": "EPSG:5070", "transform": from_origin(15 + shift, 795, 30, 30), "nodata": nodata}
        with rasterio.open(path, "w", **prof) as dst:
            dst.write(np.full((4, 5), value, dtype=dtype), 1)
        return path
    monkeypatch.setattr("h3lowpoints.sources.landcover.fetch_land_cover",
                        lambda bbox, out, year=2021: raster(out, 82, "uint8", 0))
    monkeypatch.setattr("h3lowpoints.sources.landcover.fetch_impervious",
                        lambda bbox, out, year=2021: raster(out, 20, "uint8", 255))
    monkeypatch.setattr("h3lowpoints.sources.soils.fetch_soil_groups",
                        lambda bbox, out, cache_dir, like: raster(out, 3, "uint8", 0))
    out = runoff.fetch_curve_numbers((-95.5, 29.6, -95.4, 29.7), tmp_path / "cn.tif", cache_dir=tmp_path)
    with rasterio.open(out) as src:
        assert np.allclose(src.read(1), 0.2 * 98 + 0.8 * 85)       # row crops on C, 20% paved
        assert np.allclose(src.read(1), curve_numbers(82, 20, 3))
    monkeypatch.setattr("h3lowpoints.sources.soils.fetch_soil_groups",
                        lambda bbox, out, cache_dir, like: raster(out, 3, "uint8", 0, shift=30))
    with pytest.raises(ValueError, match="one grid"):
        runoff.fetch_curve_numbers((-95.5, 29.6, -95.4, 29.7), tmp_path / "cn2.tif", cache_dir=tmp_path)


@pytest.mark.network
def test_real_soil_groups_and_curve_numbers(tmp_path):
    box = (-95.45, 29.70, -95.40, 29.75)                    # Houston: clay soils
    out = fetch_curve_numbers(box, tmp_path / "cn.tif", cache_dir=tmp_path)
    with rasterio.open(tmp_path / "cn_soil_groups.tif") as g, rasterio.open(out) as c:
        groups = g.read(1)
        assert (groups == 4).mean() > 0.5                    # mostly group D
        cn = c.read(1)
        assert np.nanmedian(cn) > 85
    assert main(["fetch-landcover", "--from", "soil-groups", "--bbox", *map(str, box),
                 "--out", str(tmp_path / "g.tif"), "--cache-dir", str(tmp_path)]) == 0
