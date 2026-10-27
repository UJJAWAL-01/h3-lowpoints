"""US and global data readers: USGS 3DEP (30 m, 10 m, 1 m lidar), land cover, design rainfall.

Offline tests build fake tiles that copy the real layout (names, corners, overlaps, CRS) so the
readers can be checked exactly. Tests marked `network` use the real services (H3LP_NETWORK=1).
"""
import io
import urllib.error

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from h3lowpoints.cli import main
from h3lowpoints.sources import _common, landcover, rainfall, usgs

PX1 = 1 / 3600          # 3DEP 1 arc-second pixel
PX13 = 1 / 10800        # 3DEP 1/3 arc-second pixel


def f(lon, lat):
    """A smooth height that differs at every pixel, so mosaics can be checked exactly."""
    return 100.0 + (lon + 98.0) * 1000.0 + (lat - 30.0) * 700.0


def write(path, arr, crs, transform, nodata, dtype="float32"):
    prof = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": dtype, "crs": crs, "transform": transform, "nodata": nodata}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype(dtype), 1)
    return path


def fake_3dep_tile(folder, lat, lon, cols, rows, code="1", px=PX1, func=f, overlap_error=0.37):
    """A window of the real tile layout: tile n{lat+1}w{lon} with its 6-pixel overlap.

    `cols` / `rows` are (first, last) pixel offsets from the tile's west / north edge; the real
    tile spans offsets -6 .. 3606 (1 arc-second), so negative offsets reach into the overlap.
    Like the real data, the overlap does not quite agree with the neighbour: its values are off
    by `overlap_error`, so a mosaic that lets a neighbour's overlap win is caught.
    """
    west, north = lon + cols[0] * px, lat + 1 - rows[0] * px
    nx, ny = cols[1] - cols[0], rows[1] - rows[0]
    xs = west + (np.arange(nx) + 0.5) * px
    ys = north - (np.arange(ny) + 0.5) * px
    arr = func(xs[None, :], ys[:, None])
    outside = ((xs[None, :] < lon) | (xs[None, :] > lon + 1) | (ys[:, None] < lat) | (ys[:, None] > lat + 1))
    arr = np.where(outside, arr + overlap_error, arr)
    name = f"USGS_{code}_{usgs.tile_name(lat, lon)}.tif"
    return write(folder / name, arr, "EPSG:4269", from_origin(west, north, px, px), usgs.NODATA)


def pixel_centres(path):
    with rasterio.open(path) as r:
        a = r.read(1)
        xs = r.transform.c + (np.arange(r.width) + 0.5) * r.transform.a
        ys = r.transform.f + (np.arange(r.height) + 0.5) * r.transform.e
        return a, xs, ys, r


# ------------------------------------------------------------------ shared helpers

@pytest.mark.parametrize("bbox", [(1, 0, 0, 1), (0, 1, 1, 0), (0, 0, 0, 1), (-181, 0, 0, 1),
                                  (0, 0, 1, 91), ("a", 0, 1, 1), (0, 0, 1)])
def test_bad_boxes_are_refused(bbox):
    with pytest.raises(ValueError):
        _common.check_bbox(bbox)


@pytest.mark.parametrize("bbox, size, want", [
    ((-97.9, 30.1, -97.6, 30.4), 1, [(30, -98)]),
    ((-99.02, 29.98, -98.98, 30.02), 3, [(27, -102), (27, -99), (30, -102), (30, -99)]),
    ((-0.5, -0.5, 0.5, 0.5), 3, [(-3, -3), (-3, 0), (0, -3), (0, 0)]),
    ((-98.0, 30.0, -97.0, 31.0), 1, [(30, -98)]),
])
def test_degree_tiles(bbox, size, want):
    assert sorted(_common.degree_tiles(bbox, size)) == sorted(want)


def test_box_area_is_about_right():
    assert _common.bbox_area_km2((-98, 30, -97, 31)) == pytest.approx(96.4 * 110.6, rel=0.01)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_url_exists_only_treats_404_as_missing(monkeypatch):
    monkeypatch.setattr(_common, "_sleep", lambda s: None)       # retries without waiting
    def answer(code):
        def fake(request, timeout=None):
            if code == 200:
                return _Resp(b"")
            if code == "down":
                raise urllib.error.URLError("network is down")
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)
        return fake

    monkeypatch.setattr(_common.urllib.request, "urlopen", answer(200))
    assert _common.url_exists("https://example.org/a.tif") is True
    monkeypatch.setattr(_common.urllib.request, "urlopen", answer(404))
    assert _common.url_exists("https://example.org/a.tif") is False
    monkeypatch.setattr(_common.urllib.request, "urlopen", answer(503))
    with pytest.raises(OSError, match="503"):
        _common.url_exists("https://example.org/a.tif")
    monkeypatch.setattr(_common.urllib.request, "urlopen", answer("down"))
    with pytest.raises(OSError):                     # a broken network is never "missing data"
        _common.url_exists("https://example.org/a.tif")


def test_passing_failures_are_retried_and_404_is_not(monkeypatch):
    waits, calls = [], {"n": 0}
    monkeypatch.setattr(_common, "_sleep", waits.append)

    def flaky(fails, error):
        def fake(request, timeout=None):
            calls["n"] += 1
            if calls["n"] <= fails:
                raise error(request) if callable(error) else error
            return _Resp(b"tile list")
        return fake

    reset = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
    monkeypatch.setattr(_common.urllib.request, "urlopen", flaky(2, reset))
    assert _common.url_exists("https://example.org/a.tif") is True       # the case that stopped a build
    assert waits == [1, 2] and calls["n"] == 3
    waits.clear(), calls.update(n=0)
    monkeypatch.setattr(_common.urllib.request, "urlopen",
                        flaky(3, lambda r: urllib.error.HTTPError(r.full_url, 503, "busy", {}, None)))
    assert _common.read_text("https://example.org/list.txt") == "tile list" and waits == [1, 2, 4]
    waits.clear(), calls.update(n=0)
    monkeypatch.setattr(_common.urllib.request, "urlopen",
                        flaky(99, lambda r: urllib.error.HTTPError(r.full_url, 404, "no", {}, None)))
    assert _common.url_exists("https://example.org/a.tif") is False and calls["n"] == 1 and not waits
    calls.update(n=0)
    monkeypatch.setattr(_common.urllib.request, "urlopen", flaky(99, reset))
    with pytest.raises(ConnectionResetError):                            # a lasting outage still fails
        _common.read_bytes("https://example.org/a.tif")
    assert calls["n"] == len(_common.RETRY_DELAYS) + 1


# ------------------------------------------------------------------ 3DEP 30 m and 10 m

@pytest.mark.parametrize("lat, lon, name", [
    (30, -98, "n31w098"),          # Austin: named by its north-west corner
    (40, -75, "n41w075"),
    (-15, -171, "s14w171"),        # American Samoa
    (13, 144, "n14e144"),          # Guam
    (-1, -60, "n00w060"),
])
def test_3dep_tile_names(lat, lon, name):
    assert usgs.tile_name(lat, lon) == name


def test_3dep_urls():
    assert usgs.tile_url(30, -98) == ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/"
                                      "TIFF/current/n31w098/USGS_1_n31w098.tif")
    assert usgs.tile_url(30, -98, 10).endswith("/13/TIFF/current/n31w098/USGS_13_n31w098.tif")


def test_mosaic_across_a_tile_corner_with_overlaps_is_exact(tmp_path):
    # four tiles meet at (31 N, 97 W); each window includes its real 6-pixel overlap
    fake_3dep_tile(tmp_path, 30, -98, (3560, 3606), (-6, 40))       # south-west of the corner
    fake_3dep_tile(tmp_path, 30, -97, (-6, 40), (-6, 40))           # south-east
    fake_3dep_tile(tmp_path, 31, -98, (3560, 3606), (3560, 3606))   # north-west
    fake_3dep_tile(tmp_path, 31, -97, (-6, 40), (3560, 3606))       # north-east
    out = usgs.fetch_3dep((-97 - 30 * PX1, 31 - 30 * PX1, -97 + 30 * PX1, 31 + 30 * PX1),
                          tmp_path / "o.tif", source=tmp_path)
    a, xs, ys, r = pixel_centres(out)
    assert r.crs.to_epsg() == 4269 and r.nodata == usgs.NODATA and a.shape == (60, 60)
    np.testing.assert_allclose(a, f(xs[None, :], ys[:, None]), atol=1e-3)   # no seam, no shift


def test_ten_metre_product(tmp_path):
    fake_3dep_tile(tmp_path, 30, -98, (100, 160), (100, 160), code="13", px=PX13)
    out = usgs.fetch_3dep((-98 + 110 * PX13, 31 - 150 * PX13, -98 + 150 * PX13, 31 - 110 * PX13),
                          tmp_path / "o.tif", resolution=10, source=tmp_path)
    a, xs, ys, r = pixel_centres(out)
    assert r.res[0] == pytest.approx(PX13) and a.shape == (40, 40)
    np.testing.assert_allclose(a, f(xs[None, :], ys[:, None]), atol=1e-3)


def test_missing_tile_is_nodata_and_all_missing_is_an_error(tmp_path):
    fake_3dep_tile(tmp_path, 30, -98, (3560, 3606), (-6, 40))
    out = usgs.fetch_3dep((-97 - 30 * PX1, 31 - 30 * PX1, -97 + 30 * PX1, 31 - 1 * PX1),
                          tmp_path / "o.tif", source=tmp_path)
    a, xs, ys, _ = pixel_centres(out)
    # only a tile's own 1-degree cell is used, so beyond its edge there is no data
    assert (a[:, :30] != usgs.NODATA).all() and (a[:, 30:] == usgs.NODATA).all()
    np.testing.assert_allclose(a[:, :30], f(xs[None, :30], ys[:, None]), atol=1e-3)
    with pytest.raises(ValueError, match="No 3DEP tiles"):
        usgs.fetch_3dep((-30.5, 0.5, -30.2, 0.8), tmp_path / "x.tif", source=tmp_path)


def test_3dep_guards(tmp_path):
    with pytest.raises(ValueError, match="touches 100 tiles"):
        usgs.fetch_3dep((-100, 30, -90, 40), tmp_path / "x.tif", source=tmp_path)
    with pytest.raises(ValueError, match="30 or 10"):
        usgs.fetch_3dep((-98, 30, -97.9, 30.1), tmp_path / "x.tif", resolution=1)


# ------------------------------------------------------------------ 1 m lidar

UTM14 = "EPSG:26914"
UTM15 = "EPSG:26915"
BOX = (-97.752, 30.266, -97.748, 30.270)


def fake_survey(folder, name, date, value, crs=UTM14, pad=150, half=None):
    """A 1 m tile in `crs` around BOX (or its west/east half), filled with `value`."""
    tr = Transformer.from_crs(4326, crs, always_xy=True)
    xs, ys = tr.transform([BOX[0], BOX[2], BOX[0], BOX[2]], [BOX[1], BOX[1], BOX[3], BOX[3]])
    left, right = int(min(xs)) - pad, int(max(xs)) + pad
    bottom, top = int(min(ys)) - pad, int(max(ys)) + pad
    if half == "west":
        right = (left + right) // 2
    elif half == "east":
        left = (left + right) // 2
    arr = np.full((top - bottom, right - left), float(value))
    path = write(folder / f"{name}.tif", arr, crs, from_origin(left, top, 1, 1), -3.4028230607370965e38)
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform([left, right], [bottom, top])
    return {"title": name, "publicationDate": date, "downloadURL": str(path),
            "boundingBox": {"minX": min(lon), "maxX": max(lon), "minY": min(lat), "maxY": max(lat)}}


def test_newest_survey_wins_where_surveys_overlap(tmp_path):
    old = fake_survey(tmp_path, "old_2012", "2014-05-01", 100)
    new = fake_survey(tmp_path, "new_2019", "2020-03-30", 200, half="west")
    out = usgs.fetch_lidar(BOX, tmp_path / "o.tif", items=[old, new])     # order given does not matter
    a, xs, ys, r = pixel_centres(out)
    assert r.crs.to_string() == UTM14 and r.res == (1.0, 1.0) and r.nodata == usgs.LIDAR_NODATA
    west = xs < (xs.min() + xs.max()) / 2 - 50
    east = xs > (xs.min() + xs.max()) / 2 + 50
    assert (a[:, west] == 200).all() and (a[:, east] == 100).all()


def test_output_is_on_the_surveys_own_pixel_grid(tmp_path):
    item = fake_survey(tmp_path, "s", "2020-01-01", 7)
    out = usgs.fetch_lidar(BOX, tmp_path / "o.tif", items=[item])
    with rasterio.open(out) as r, rasterio.open(item["downloadURL"]) as src:
        assert (r.transform.c - src.transform.c) % 1 == 0 and (r.transform.f - src.transform.f) % 1 == 0
        assert (r.read(1) == 7).all()


def test_a_survey_in_another_utm_zone_is_reprojected(tmp_path):
    centre = fake_survey(tmp_path, "zone14", "2018-01-01", 100, half="west")
    other = fake_survey(tmp_path, "zone15", "2016-01-01", 300, crs=UTM15)
    out = usgs.fetch_lidar(BOX, tmp_path / "o.tif", items=[other, centre])
    a, *_ , r = pixel_centres(out)
    assert r.crs.to_string() == UTM14                  # the zone of the survey at the box's centre
    assert (a == 100).any() and (np.isclose(a, 300)).any() and (a != usgs.LIDAR_NODATA).all()


def test_lidar_guards(tmp_path):
    with pytest.raises(ValueError, match="km2"):
        usgs.fetch_lidar((-98, 30, -97, 31), tmp_path / "x.tif", items=[])
    with pytest.raises(ValueError, match="No 1 m lidar"):
        usgs.fetch_lidar(BOX, tmp_path / "x.tif", items=[])
    far = fake_survey(tmp_path, "far", "2020-01-01", 5)
    far["downloadURL"] = str(write(tmp_path / "far.tif", np.full((10, 10), 5.0), UTM14,
                                   from_origin(100000, 100000, 1, 1), -9999.0))
    with pytest.raises(ValueError, match="no data inside"):
        usgs.fetch_lidar(BOX, tmp_path / "x.tif", items=[far])


def test_catalogue_order_is_newest_first_and_reproducible():
    items = [{"title": "b", "publicationDate": "2019-01-01"}, {"title": "a", "publicationDate": "2021-06-01"},
             {"title": "c", "publicationDate": "2019-01-01"}, {"title": "d", "publicationDate": None}]
    assert [i["title"] for i in usgs.sort_newest_first(items)] == ["a", "c", "b", "d"]


# ------------------------------------------------------------------ land cover

def test_worldcover_names_and_tiles():
    assert landcover.worldcover_tile_name(30, -99) == "ESA_WorldCover_10m_2021_v200_N30W099_Map"
    assert landcover.worldcover_tile_name(-3, 6) == "ESA_WorldCover_10m_2021_v200_S03E006_Map"


def test_worldcover_mosaic(tmp_path):
    px = 1 / 12000
    for lat, lon, west in ((27, -102, -99 - 20 * px), (27, -99, -99.0)):
        arr = np.full((20, 20), 50 if lon == -102 else 10, dtype="uint8")
        write(tmp_path / f"{landcover.worldcover_tile_name(lat, lon)}.tif", arr, "EPSG:4326",
              from_origin(west, 30.0, px, px), 0, dtype="uint8")
    out = landcover.fetch_worldcover((-99 - 20 * px, 30 - 20 * px, -99 + 20 * px, 30.0),
                                     tmp_path / "o.tif", source=tmp_path)
    with rasterio.open(out) as r:
        a = r.read(1)
        assert r.dtypes[0] == "uint8" and a.shape == (20, 40)
        assert (a[:, :20] == 50).all() and (a[:, 20:] == 10).all()


def test_impervious_request_is_well_formed():
    url = landcover.impervious_request((-97.8, 30.2, -97.7, 30.3), 2019)
    assert "mrlc_download__NLCD_2019_Impervious_L48" in url and "format=image%2Fgeotiff" in url
    numbers = [int(v) for v in __import__("re").findall(r"%28(-?\d+)%2C(-?\d+)%29", url)[0]]
    assert all(v % 30 == 0 for v in numbers)                 # snapped to the 30 m grid
    with pytest.raises(ValueError, match="2021"):
        landcover.impervious_request((-97.8, 30.2, -97.7, 30.3), 2020)


def test_zero_percent_impervious_is_data_not_nodata():
    raw = np.array([[0, 1, 50, 100], [127, 255, 0, 99]])
    np.testing.assert_array_equal(landcover.clean_impervious(raw),
                                  [[0, 1, 50, 100], [255, 255, 0, 99]])


def test_impervious_from_a_service_answer_that_tags_zero_as_nodata(tmp_path):
    arr = np.array([[0, 0, 40], [100, 127, 0]], dtype="uint8")
    raw = write(tmp_path / "svc.tif", arr, "EPSG:5070", from_origin(-100000, 800000, 30, 30), 0,
                dtype="uint8").read_bytes()
    out = landcover.fetch_impervious((-97.8, 30.2, -97.7, 30.3), tmp_path / "o.tif", data=raw)
    with rasterio.open(out) as r:
        assert r.nodata == 255
        np.testing.assert_array_equal(r.read(1), [[0, 0, 40], [100, 255, 0]])
    with pytest.raises(ValueError, match="did not return a GeoTIFF"):
        landcover.fetch_impervious((-97.8, 30.2, -97.7, 30.3), tmp_path / "x.tif",
                                   data=b"<ServiceException>out of range</ServiceException>")
    with pytest.raises(ValueError, match="km2"):
        landcover.fetch_impervious((-110, 30, -90, 45), tmp_path / "x.tif", data=raw)


# ------------------------------------------------------------------ rainfall

ATLAS14 = """Point precipitation frequency estimates (millimeters)
NOAA Atlas 14 Volume 11 Version 2
Data type: Precipitation depth
Time series type: Partial duration
Project area: Texas
Latitude: 30.27 Degree
Longitude: -97.74 Degree


PRECIPITATION FREQUENCY ESTIMATES
by duration for ARI (years):, 1,2,5,10,25,50,100,200,500,1000
5-min:, 11,13,17,20,25,29,33,37,43,48
60-min:, 41,50,64,76,93,107,122,139,165,186
24-hr:, 80,105,140,173,225,269,321,383,477,557

Date/time (GMT):  Sun Oct 18 19:00:22 2026
"""


def test_atlas14_table():
    t = rainfall.parse_atlas14(ATLAS14)
    assert list(t.index) == ["5-min", "60-min", "24-hr"]
    assert list(t.columns) == [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]
    assert t.loc["24-hr", 100] == 321 and t.attrs["units"] == "mm"
    assert rainfall.rainfall_depth(0, 0, table=t) == 76          # default: 60-min, 10 years
    with pytest.raises(ValueError, match="duration must be"):
        rainfall.rainfall_depth(0, 0, "1-hr", table=t)
    with pytest.raises(ValueError, match="ari_years must be"):
        rainfall.rainfall_depth(0, 0, "60-min", 7, table=t)


def test_atlas14_outside_coverage_and_garbage():
    with pytest.raises(ValueError, match="Washington and Oregon"):
        rainfall.parse_atlas14("result = 'none';\nErrorMsg = 'Error 3.0: Selected location is not "
                               "within a project area';")
    with pytest.raises(ValueError, match="Unexpected answer"):
        rainfall.parse_atlas14("<html>maintenance</html>")


# ------------------------------------------------------------------ command line

def test_cli_fetch_3dep_from_a_folder(tmp_path, capsys):
    fake_3dep_tile(tmp_path, 30, -98, (3560, 3606), (-6, 40))
    out = tmp_path / "dem.tif"
    assert main(["fetch-dem", "--from", "3dep", "--source", str(tmp_path), "--out", str(out), "--bbox",
                 str(-97 - 30 * PX1), str(31 - 30 * PX1), str(-97 - 1 * PX1), str(31 - 1 * PX1)]) == 0
    assert out.exists() and "pixels" in capsys.readouterr().out


def test_cli_rainfall(monkeypatch, capsys):
    monkeypatch.setattr(rainfall, "design_rainfall", lambda lat, lon: rainfall.parse_atlas14(ATLAS14))
    assert main(["rainfall", "30.27", "-97.74"]) == 0
    assert "321" in capsys.readouterr().out
    assert main(["rainfall", "30.27", "-97.74", "--duration", "24-hr", "--years", "100"]) == 0
    assert capsys.readouterr().out.strip() == "321 mm"
    with pytest.raises(SystemExit, match="duration must be"):
        main(["rainfall", "30.27", "-97.74", "--duration", "1-hr"])


def test_cli_rejects_unknown_sources():
    with pytest.raises(SystemExit):
        main(["fetch-dem", "--from", "srtm", "--bbox", "0", "0", "1", "1", "--out", "x.tif"])


# ------------------------------------------------------------------ the real services

@pytest.mark.network
@pytest.mark.parametrize("resolution", [30, 10])
def test_live_3dep(tmp_path, resolution):
    out = usgs.fetch_3dep((-97.80, 30.24, -97.70, 30.32), tmp_path / "d.tif", resolution=resolution)
    a, xs, ys, r = pixel_centres(out)
    assert r.crs.to_epsg() == 4269 and (a != usgs.NODATA).all()
    assert 100 < a.min() < 200 and 200 < a.max() < 350                 # Austin's hills


@pytest.mark.network
def test_live_3dep_seam_pixels_equal_the_source_tiles(tmp_path):
    """Across the 31 N / 97 W corner, every pixel must equal the original tile's value there."""
    out = usgs.fetch_3dep((-97.02, 30.98, -96.98, 31.02), tmp_path / "d.tif")
    a, xs, ys, r = pixel_centres(out)
    rng = np.random.default_rng(0)
    rows, cols = rng.integers(0, a.shape[0], 60), rng.integers(0, a.shape[1], 60)
    with _common.gdal_env():
        for lat, lon in _common.degree_tiles((-97.02, 30.98, -96.98, 31.02)):
            with rasterio.open(_common.remote(usgs.tile_url(lat, lon))) as src:
                inside = [(i, j) for i, j in zip(rows, cols)
                          if lon < xs[j] < lon + 1 and lat < ys[i] < lat + 1]
                values = [v[0] for v in src.sample([(xs[j], ys[i]) for i, j in inside])]
                np.testing.assert_allclose([a[i, j] for i, j in inside], values)


@pytest.mark.network
def test_live_lidar_and_finest_resolution_analysis(tmp_path):
    from h3lowpoints import analyze

    out = usgs.fetch_lidar((-97.752, 30.266, -97.746, 30.271), tmp_path / "l.tif")
    a, xs, ys, r = pixel_centres(out)
    assert r.crs.to_string().startswith("EPSG:269") and r.res == (1.0, 1.0)
    assert (a != usgs.LIDAR_NODATA).mean() > 0.99 and 100 < np.median(a) < 250
    res = analyze(str(out), min_depth=0.15)
    assert res.res == 14 and len(res.depressions) > 0
    assert res.cells["n_lowpoints"].sum() == len(res.depressions)


@pytest.mark.network
def test_live_landcover(tmp_path):
    out = landcover.fetch_worldcover((-97.80, 30.24, -97.70, 30.32), tmp_path / "w.tif")
    with rasterio.open(out) as r:
        codes = set(np.unique(r.read(1)).tolist())
    assert 50 in codes and codes <= set(landcover.WORLDCOVER_CLASSES) | {0}       # built-up present
    out = landcover.fetch_impervious((-97.80, 30.24, -97.70, 30.32), tmp_path / "i.tif")
    with rasterio.open(out) as r:
        a = r.read(1)
    assert r.nodata == 255 and (a == 0).any() and 20 < a[a != 255].mean() < 80
    with pytest.raises(ValueError, match="conterminous US only"):
        landcover.fetch_impervious((2.30, 48.84, 2.36, 48.88), tmp_path / "p.tif")


@pytest.mark.network
def test_live_rainfall():
    t = rainfall.design_rainfall(30.27, -97.74)
    assert t.attrs["units"] == "mm" and 250 < t.loc["24-hr", 100] < 400        # Austin, 100-year day
    with pytest.raises(ValueError, match="Washington and Oregon"):
        rainfall.design_rainfall(47.61, -122.33)                               # Seattle


def test_gdal_block_cache_is_256_megabytes():
    """rasterio hands GDAL_CACHEMAX to GDAL in bytes: 256 alone would be a 256-byte cache."""
    from rasterio._env import get_gdal_config

    with _common.gdal_env():
        assert get_gdal_config("GDAL_CACHEMAX") == 256 * 1024 * 1024


def test_nlcd_error_pages_are_retried_and_never_taken_for_no_data(monkeypatch, tmp_path):
    """An HTML page from the NLCD service is its web server failing for a moment: asked again, it
    answers. If it keeps failing, that is an error, never "no data here"."""
    arr = np.full((4, 5), 20, dtype="uint8")
    with rasterio.MemoryFile() as mem:
        with mem.open(driver="GTiff", height=4, width=5, count=1, dtype="uint8", crs="EPSG:5070",
                      transform=from_origin(0, 120, 30, 30), nodata=255) as dst:
            dst.write(arr, 1)
        tiff = mem.read()
    page = b'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"><html><title>502</title></html>'
    answers, waits = [page, page, tiff], []
    monkeypatch.setattr(landcover, "read_bytes", lambda url: answers.pop(0))
    monkeypatch.setattr(_common, "_sleep", waits.append)
    out = landcover.fetch_impervious((-95.4, 29.7, -95.3, 29.8), tmp_path / "i.tif")
    with rasterio.open(out) as src:
        assert (src.read(1) == 20).all()
    assert waits == [5, 15]
    monkeypatch.setattr(landcover, "read_bytes", lambda url: page)
    with pytest.raises(OSError, match="error page"):
        landcover.fetch_land_cover((-95.4, 29.7, -95.3, 29.8), tmp_path / "l.tif")
