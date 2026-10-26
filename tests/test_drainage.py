"""Drained or landlocked: distances to mapped channels and roads, channel share, land cover.

Distances are compared with shapely's own distance, point by point. The cells a line passes
through are compared with an exact test against each hexagon's outline: every cell the line
crosses for at least the sampling step must be found, and none it misses. The susceptibility
table holds the index and the drainage columns together, written in either order. Damaged
tables must be caught by `verify`. The readers are checked on fake answers in the services'
real formats; tests marked `network` use the real services.
"""
import io
import json
import zipfile

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pytest
import rasterio
import shapely
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon

from h3lowpoints import analyze_array, read_result
from h3lowpoints.cli import main
from h3lowpoints.clip import clip_result
from h3lowpoints.drainage import DRAINAGE_COLUMNS, cells_along, drainage_flags
from h3lowpoints.sources import landcover, nhd, roads
from h3lowpoints.susceptibility import susceptibility, write_susceptibility
from h3lowpoints.verify import check_saved, check_tables
from test_store import FORMATS
from test_tiling import smooth

CRS = "EPSG:32614"
UTM = from_origin(500000, 3350000, 10, 10)


@pytest.fixture(scope="module")
def result():
    z = smooth(5, (150, 150), sigma=3.0, trend=2.0)
    res = analyze_array(z, UTM, CRS, res=11, min_depth=0.05, catchments=True, details=True)
    assert len(res.depressions) > 30
    return res


def points_utm(res, lat="lat", lon="lon"):
    x, y = Transformer.from_crs(4326, CRS, always_xy=True).transform(
        res.depressions[lon].to_numpy(), res.depressions[lat].to_numpy())
    return np.asarray(x), np.asarray(y)


def lines_through(x, y, k, angle=0.3):
    """Straight lines through the first k points, as a GeoDataFrame in UTM."""
    dx, dy = 400 * np.cos(angle), 400 * np.sin(angle)
    return gpd.GeoDataFrame(geometry=[LineString([(a - dx, b - dy), (a + dx, b + dy)])
                                      for a, b in zip(x[:k], y[:k])], crs=CRS)


def test_distances_match_shapely_and_the_rule_holds(result):
    x, y = points_utm(result)
    streams = lines_through(x + 15, y, 3)                     # 15 m east of three lowest points
    canals = lines_through(x - 200, y + 50, 2, angle=1.2)
    sx, sy = points_utm(result, "spill_lat", "spill_lon")
    road = lines_through(sx[3:6], sy[3:6], 3, angle=2.0)
    flags = drainage_flags(result, streams=streams, canals=canals.to_crs(4326), roads=road,
                           crs=CRS, max_distance_m=300)
    pts = shapely.points(x, y)
    for col, lines in (("stream_m", streams), ("canal_m", canals)):
        exact = np.array([lines.distance(p).min() for p in pts])
        expected = np.where(exact <= 300, exact, np.nan)
        assert np.allclose(flags[col], expected, equal_nan=True, atol=1e-6), col
    assert (flags["stream_m"][:3] <= 15 + 1e-6).all()
    assert (flags["spill_road_m"][3:6] < 1e-6).all()
    assert flags["drained"][:6].all()
    assert (flags["landlocked"] == ~flags["drained"]).all()
    assert list(flags.columns) == ["id"] + DRAINAGE_COLUMNS
    assert flags.attrs["settings"]["drainage"]["near_m"] == 30.0


def test_cells_along_finds_every_cell_a_line_really_crosses():
    res = 11
    lines = np.array([LineString([(500000, 3349000), (501500, 3349700)]),
                      LineString([(500300, 3348200), (500320, 3349900), (501000, 3349950)])])
    found = set(cells_along(lines, CRS, res).tolist())
    step = h3.average_hexagon_edge_length(res, unit="m") / 3
    to_utm = Transformer.from_crs(4326, CRS, always_xy=True)
    near = set()
    for line in lines:                                       # candidates: cells along a fine sampling
        for d in np.linspace(0, line.length, int(line.length) + 1):
            p = line.interpolate(d)
            lon, lat = Transformer.from_crs(CRS, 4326, always_xy=True).transform(p.x, p.y)
            near |= set(h3.grid_disk(h3.latlng_to_cell(lat, lon, res), 1))
    for cell in near:
        hexagon = Polygon([to_utm.transform(lo, la) for la, lo in h3.cell_to_boundary(cell)])
        inside = sum(line.intersection(hexagon).length for line in lines)
        if inside >= step:
            assert h3.str_to_int(cell) in found, cell
        if inside == 0:
            assert h3.str_to_int(cell) not in found, cell


def test_channel_share_is_the_area_share_in_crossed_cells(result):
    x, y = points_utm(result)
    streams = lines_through(x, y, 5)
    flags = drainage_flags(result, streams=streams, crs=CRS)
    crossed = set(cells_along(np.asarray(streams.geometry.values), CRS, 11).tolist())
    dc = result.depression_cells
    wet = dc["h3"].map(lambda c: h3.str_to_int(c) in crossed)
    expected = (dc["area_m2"] * wet).groupby(dc["id"]).sum() / dc.groupby("id")["area_m2"].sum()
    got = flags.set_index("id")["channel_share"]
    assert np.allclose(got.loc[expected.index], expected)
    assert (got.iloc[:5] > 0).all()
    plain = analyze_array(smooth(5, (150, 150), sigma=3.0, trend=2.0), UTM, CRS, res=11,
                          min_depth=0.05, catchments=True)
    assert drainage_flags(plain, streams=streams, crs=CRS)["channel_share"].isna().all()


def test_land_cover_is_read_at_the_lowest_point(result, tmp_path):
    to_albers = Transformer.from_crs(4326, "EPSG:5070", always_xy=True)
    xs, ys = to_albers.transform(result.depressions["lon"].to_numpy(), result.depressions["lat"].to_numpy())
    west, north = np.floor(min(xs) / 30) * 30 - 300, np.ceil(max(ys) / 30) * 30 + 300
    w, h = int((max(xs) - west) / 30) + 20, int((north - min(ys)) / 30) + 20
    rng = np.random.default_rng(0)
    arr = rng.choice(list(landcover.NLCD_CLASSES), size=(h, w)).astype("uint8")
    arr[:, : w // 2] = 0                                      # no data on the western half
    prof = {"driver": "GTiff", "height": h, "width": w, "count": 1, "dtype": "uint8",
            "crs": "EPSG:5070", "transform": from_origin(west, north, 30, 30), "nodata": 0}
    with rasterio.open(tmp_path / "lc.tif", "w", **prof) as dst:
        dst.write(arr, 1)
    flags = drainage_flags(result, roads=lines_through(*points_utm(result), 1),
                           land_cover=tmp_path / "lc.tif", crs=CRS)
    col = np.floor((np.asarray(xs) - west) / 30).astype(int)
    row = np.floor((north - np.asarray(ys)) / 30).astype(int)
    assert (flags["land_cover"].to_numpy() == arr[row, col]).all()
    assert (flags["land_cover"] == 0).any() and (flags["land_cover"] > 0).any()


def test_settings_are_checked(result):
    with pytest.raises(ValueError, match="at least one"):
        drainage_flags(result)
    with pytest.raises(ValueError, match="near_m"):
        drainage_flags(result, roads=lines_through(*points_utm(result), 1), min_share=0)


@pytest.mark.parametrize("fmt", FORMATS)
def test_index_and_drainage_share_one_table_in_either_order(result, tmp_path, fmt):
    folder = result.save(tmp_path / "r", format=fmt)
    x, y = points_utm(result)
    flags = drainage_flags(folder, streams=lines_through(x, y, 4), crs=CRS)
    index = susceptibility(folder, runoff_coef=0.4, rain={2: 20.0, 10: 40.0, 100: 80.0})
    write_susceptibility(folder, flags)
    write_susceptibility(folder, index)
    a = read_result(folder)
    assert set(DRAINAGE_COLUMNS) <= set(a.susceptibility) and "fills_in_years" in a.susceptibility
    assert a.meta["susceptibility"]["drainage"]["near_m"] == 30.0
    assert a.meta["susceptibility"]["rain"] == "given per storm"
    assert check_saved(folder) == []
    # redoing the index without storms drops the old storm columns, keeps the drainage ones
    write_susceptibility(folder, susceptibility(folder, runoff_coef=0.5))
    b = read_result(folder).susceptibility
    assert "fills_in_years" not in b and b["drained"].equals(a.susceptibility["drained"])
    assert (b["runoff_coef"] == 0.5).all()
    assert check_saved(folder) == []
    # and the other way round
    folder2 = result.save(tmp_path / "r2", format=fmt)
    write_susceptibility(folder2, index)
    write_susceptibility(folder2, flags)
    c = read_result(folder2)
    pd.testing.assert_frame_equal(c.susceptibility, a.susceptibility, check_dtype=False)
    clipped = clip_result(folder2, shapely.box(-180, -90, 180, 90), tmp_path / "c")
    assert check_saved(clipped) == []


def damaged(table, how):
    t = table.copy()
    if how == "drained":
        t.loc[0, "drained"] = not t.loc[0, "drained"]
        t.loc[0, "landlocked"] = not t.loc[0, "landlocked"]
    elif how == "landlocked":
        t.loc[0, "landlocked"] = t.loc[0, "drained"]
    elif how == "distance":
        t.loc[0, "stream_m"] = -1.0
    elif how == "far":
        t.loc[0, "canal_m"] = 5000.0
    elif how == "share":
        t.loc[0, "channel_share"] = 1.5
    return t


@pytest.mark.parametrize("how", ["drained", "landlocked", "distance", "far", "share"])
def test_verify_catches_damage(result, how):
    x, y = points_utm(result)
    flags = drainage_flags(result, streams=lines_through(x, y, 4), canals=lines_through(x, y, 2),
                           crs=CRS)
    meta = {**result.meta, "susceptibility": flags.attrs["settings"]}
    args = (result.depressions, result.cells, meta, result.catchments, result.depression_cells)
    assert check_tables(*args, flags) == []
    assert check_tables(*args, damaged(flags, how)) != []


def test_cli_drainage(result, tmp_path):
    folder = result.save(tmp_path / "r")
    x, y = points_utm(result)
    lines_through(x, y, 4).to_file(tmp_path / "streams.gpkg")
    assert main(["drainage", str(folder), "--streams", str(tmp_path / "streams.gpkg")]) == 0
    assert read_result(folder).susceptibility["drained"].sum() >= 4
    with pytest.raises(SystemExit, match="at least one"):
        main(["drainage", str(folder)])
    with pytest.raises(SystemExit, match="cache-dir"):
        main(["drainage", str(folder), "--tiger"])


# ------------------------------------------------------------------ readers, offline

def geojson_page(n, start=0, more=False):
    feats = [{"type": "Feature", "properties": {"ftype": 460 if i % 2 else 336, "fcode": 46006},
              "geometry": {"type": "LineString",
                           "coordinates": [[-95.4 + i * 1e-4, 29.7], [-95.39 + i * 1e-4, 29.71]]}}
             for i in range(start, start + n)]
    page = {"type": "FeatureCollection", "features": feats}
    if more:
        page["exceededTransferLimit"] = True
    return json.dumps(page).encode()


def test_fetch_flowlines_reads_every_page():
    asked = []

    def get(url):
        asked.append(url)
        offset = int(dict(p.split("=") for p in url.split("?")[1].split("&"))["resultOffset"])
        return geojson_page(2000, offset, more=True) if offset == 0 else geojson_page(5, offset)
    lines = nhd.fetch_flowlines((-95.5, 29.6, -95.3, 29.8), get=get)
    assert len(asked) == 2 and len(lines) == 2005
    assert list(lines.columns) == ["ftype", "fcode", "geometry"] and lines.crs.to_epsg() == 4326
    with pytest.raises(OSError, match="answered"):
        nhd.fetch_flowlines((-95.5, 29.6, -95.3, 29.8), get=lambda u: b'{"error": {"code": 500}}')


def test_state_flowlines_reads_the_zip_with_filters(tmp_path):
    lines = gpd.GeoDataFrame({"ftype": [460, 336, 428, 566], "fcode": [46006, 33600, 42800, 56600]},
                             geometry=[LineString([(-77.0 + i, 38.9), (-76.99 + i, 38.91)]) for i in range(4)],
                             crs=4269)
    gpkg = tmp_path / "NHD_H_Test_State_GPKG.gpkg"
    lines.to_file(gpkg, layer="NHDFlowline", driver="GPKG")
    cache = tmp_path / "cache"
    cache.mkdir()
    with zipfile.ZipFile(cache / "NHD_H_Test_State_GPKG.zip", "w") as z:
        z.write(gpkg, gpkg.name)
    got = nhd.state_flowlines("Test", cache_dir=cache)
    assert sorted(got["ftype"]) == [336, 428, 460, 566] and got.crs.to_epsg() == 4326
    some = nhd.state_flowlines("Test", cache_dir=cache, ftypes=nhd.CANAL_FTYPES,
                               bbox=(-77.5, 38.5, -74.5, 39.5))
    assert sorted(some["ftype"]) == [336, 428]


def test_roads_from_counties(tmp_path):
    road = gpd.GeoDataFrame({"MTFCC": ["S1400", "S1200"]},
                            geometry=[LineString([(-95.4, 29.7), (-95.3, 29.8)]),
                                      LineString([(-90.0, 30.0), (-90.1, 30.1)])], crs=4269)
    folder = tmp_path / "shp"
    folder.mkdir()
    road.to_file(folder / "tl_2024_48201_roads.shp")
    with zipfile.ZipFile(tmp_path / "tl_2024_48201_roads.zip", "w") as z:
        for f in folder.iterdir():
            z.write(f, f.name)
    answer = json.dumps({"features": [{"attributes": {"GEOID": "48201"}}]}).encode()
    got = roads.fetch_roads((-95.5, 29.6, -95.2, 29.9), cache_dir=tmp_path, get=lambda u: answer)
    assert list(got["MTFCC"]) == ["S1400"] and got.crs.to_epsg() == 4326
    with pytest.raises(ValueError, match="no US county"):
        roads.fetch_roads((10, 10, 11, 11), cache_dir=tmp_path,
                          get=lambda u: json.dumps({"features": []}).encode())


def test_fetch_land_cover_keeps_known_classes(tmp_path):
    arr = np.array([[0, 11, 21], [82, 250, 95]], dtype="uint8")
    buf = io.BytesIO()
    with rasterio.MemoryFile() as mem:
        with mem.open(driver="GTiff", height=2, width=3, count=1, dtype="uint8", crs="EPSG:5070",
                      transform=from_origin(0, 60, 30, 30), nodata=0) as dst:
            dst.write(arr, 1)
        buf.write(mem.read())
    out = landcover.fetch_land_cover((-95.4, 29.7, -95.3, 29.8), tmp_path / "lc.tif", data=buf.getvalue())
    with rasterio.open(out) as src:
        assert src.read(1).tolist() == [[0, 11, 21], [82, 0, 95]] and src.nodata == 0
    assert "NLCD_2021_Land_Cover_L48" in landcover.land_cover_request((-95.4, 29.7, -95.3, 29.8))


# ------------------------------------------------------------------ readers, network

@pytest.mark.network
def test_real_nhd_roads_and_land_cover(tmp_path):
    box = (-95.40, 29.70, -95.37, 29.72)                     # central Houston, Buffalo Bayou
    lines = nhd.fetch_flowlines(box)
    assert len(lines) > 0 and set(lines["ftype"]) & set(nhd.STREAM_FTYPES)
    rd = roads.fetch_roads(box, cache_dir=tmp_path)
    assert len(rd) > 100
    out = landcover.fetch_land_cover(box, tmp_path / "lc.tif")
    with rasterio.open(out) as src:
        values = set(np.unique(src.read(1)).tolist())
    assert values & {21, 22, 23, 24}
