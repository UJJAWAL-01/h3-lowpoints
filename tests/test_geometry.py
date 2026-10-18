"""Ground geometry: pixel areas, CRS units, distances, and the analysis on geographic grids."""
import h3
import numpy as np
import pytest
import rasterio
from pyproj import CRS, Geod
from rasterio.transform import from_origin

from h3lowpoints import analyze, analyze_array, analyze_tiled
from h3lowpoints.geometry import (EARTH_RADIUS_M, cell_size_m, crs_info, nearest_distance_m,
                                  pixel_areas_m2)

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


def _ellipsoid_cell_area(lon0, lat0, dlon, dlat):
    """Area of one lon/lat cell on the WGS 84 ellipsoid, by an independent method."""
    lons = [lon0, lon0 + dlon, lon0 + dlon, lon0]
    lats = [lat0, lat0, lat0 + dlat, lat0 + dlat]
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


# ---------------------------------------------------------------- pixel areas

@pytest.mark.parametrize("lat", [0.0, 15.0, 30.3, 45.0, 60.0, 75.0, 85.0, -33.0, -80.0])
def test_geographic_pixel_area_matches_the_ellipsoid(lat):
    px = 1 / 3600
    tr = from_origin(10.0, lat + px, px, px)       # a single row whose lower edge is `lat`
    got = pixel_areas_m2(tr, WGS84, 1)[0]
    want = _ellipsoid_cell_area(10.0, lat, px, px)
    assert got == pytest.approx(want, rel=2e-4)    # exact ellipsoid vs a geodesic polygon


def test_area_shrinks_towards_the_pole_as_on_the_ellipsoid():
    px = 0.01
    rows = pixel_areas_m2(from_origin(0, 60, px, px), WGS84, 6000)   # 60 N down to the equator
    assert (np.diff(rows) > 0).all()                                 # larger towards the equator
    want = _ellipsoid_cell_area(0, 59.99, px, px) / _ellipsoid_cell_area(0, 0.0, px, px)
    assert rows[0] / rows[-1] == pytest.approx(want, rel=1e-3)       # about 0.505, not cos 60


def test_one_degree_cell_at_the_equator():
    area = pixel_areas_m2(from_origin(0, 0.5, 1.0, 1.0), WGS84, 1)[0]
    assert area == pytest.approx(12_308_778_361, rel=1e-4)    # 12,308.8 km2 on WGS 84


def test_south_up_and_north_up_grids_give_the_same_areas():
    px = 0.001
    north = pixel_areas_m2(from_origin(5, 40, px, px), WGS84, 50)
    south = pixel_areas_m2(rasterio.Affine(px, 0, 5, 0, px, 39.95), WGS84, 50)   # positive e
    np.testing.assert_allclose(np.sort(north), np.sort(south), rtol=1e-9)


@pytest.mark.parametrize("lat_top", [90.0, -89.5, 89.9999])
def test_polar_rows_are_finite_and_positive(lat_top):
    px = 0.0005
    sign = -1 if lat_top > 0 else 1
    tr = rasterio.Affine(px, 0, 0, 0, sign * px, lat_top)
    rows = pixel_areas_m2(tr, WGS84, 100)
    assert np.isfinite(rows).all() and (rows > 0).all()


def test_latitudes_beyond_the_poles_are_rejected():
    with pytest.raises(ValueError, match="outside"):
        pixel_areas_m2(from_origin(0, 95, 0.1, 0.1), WGS84, 10)


def test_projected_areas_follow_the_linear_unit():
    metre = pixel_areas_m2(from_origin(0, 0, 2, 2), CRS.from_epsg(26914), 3)
    foot = pixel_areas_m2(from_origin(0, 0, 2, 2), CRS.from_epsg(2278), 3)     # US survey feet
    np.testing.assert_allclose(metre, 4.0)
    np.testing.assert_allclose(foot, 4.0 * 0.3048006096 ** 2, rtol=1e-6)


def test_rotated_rasters_are_rejected():
    with pytest.raises(ValueError, match="Rotated"):
        pixel_areas_m2(rasterio.Affine(1, 0.1, 0, 0.1, -1, 0), CRS.from_epsg(26914), 3)


def test_missing_or_unusable_crs_is_rejected():
    with pytest.raises(ValueError, match="no coordinate reference system"):
        crs_info(None)
    with pytest.raises(ValueError, match="projected or geographic"):
        crs_info(CRS.from_epsg(4979).to_2d() if False else CRS.from_proj4("+proj=geocent +datum=WGS84"))


def test_crs_info_accepts_rasterio_and_string_crs():
    assert crs_info(rasterio.crs.CRS.from_epsg(4326)) == (True, None)
    assert crs_info("EPSG:26914") == (False, 1.0)
    assert crs_info(rasterio.crs.CRS.from_epsg(2278))[1] == pytest.approx(0.3048006096)


def test_cell_size_is_the_side_of_the_middle_rows_square():
    assert cell_size_m(np.array([4.0, 9.0, 16.0])) == pytest.approx(3.0)


# ---------------------------------------------------------------- distances

@pytest.mark.parametrize("a, b", [
    ((-97.74, 30.27), (-97.70, 30.30)),    # a few km
    ((2.35, 48.85), (13.40, 52.52)),       # Paris to Berlin
    ((-170.0, 10.0), (175.0, -20.0)),      # across the antimeridian
    ((0.0, 89.0), (180.0, 89.0)),          # over the pole
])
def test_great_circle_distance_matches_the_ellipsoid(a, b):
    got = nearest_distance_m(np.array([a]), np.array([b]), WGS84)[0]
    want = GEOD.inv(a[0], a[1], b[0], b[1])[2]
    assert got == pytest.approx(want, rel=1e-6)    # the nearest source's distance is geodesic


def test_distance_picks_the_nearest_source():
    sources = np.array([[0.0, 0.0], [10.0, 0.0]])
    d = nearest_distance_m(sources, np.array([[1.0, 0.0], [9.0, 0.0], [5.0, 0.0]]), WGS84)
    assert d[0] == pytest.approx(d[1]) and d[2] > d[0]


def test_projected_distance_is_scaled_by_the_unit():
    feet = CRS.from_epsg(2278)
    d = nearest_distance_m(np.array([[0.0, 0.0]]), np.array([[300.0, 400.0]]), feet)[0]
    assert d == pytest.approx(500.0 * 0.3048006096)


# ---------------------------------------------------------------- analysis on geographic grids

def _bowl(n=120, depth=3.0):
    y, x = np.mgrid[0:n, 0:n]
    r = np.hypot(x - n / 2, y - n / 2)
    arr = 100.0 + 0.001 * x                       # tilted plain
    arr[r < 20] -= depth * (1 - r[r < 20] / 20)   # a cone-shaped pit, 3 m deep
    arr[r < 20] = np.maximum(arr[r < 20], 0)      # (kept simple)
    return arr.astype("float32")


def _write(path, arr, crs, tr):
    prof = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": "float32", "crs": crs, "transform": tr, "nodata": -9999.0}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)


@pytest.mark.parametrize("lat", [0.0, 30.0, 52.0, 70.0])
def test_depression_area_equals_pixels_times_ground_area(tmp_path, lat):
    px = 1 / 3600
    arr = np.full((90, 90), 50.0, dtype="float32")
    arr[40:50, 40:55] = 45.0                       # 10 x 15 pixel flat-floored pit
    tr = from_origin(-98.0, lat + 90 * px, px, px)
    res = analyze_array(arr, tr, WGS84, -9999.0, res=8).depressions
    assert len(res) == 1
    rows = pixel_areas_m2(tr, WGS84, 90)
    want = rows[40:50].sum() * 15
    assert res["area_m2"].iloc[0] == pytest.approx(want, rel=1e-9)
    assert res["volume_m3"].iloc[0] == pytest.approx(want * 5.0, rel=1e-6)
    assert res["max_depth_m"].iloc[0] == pytest.approx(5.0)


def test_geographic_matches_utm_on_the_same_terrain(tmp_path):
    """Same ground, two grids: results must agree within the geometry's accuracy."""
    px = 0.0001                                    # ~11 m N-S at 30 N
    arr = np.full((200, 200), 100.0, dtype="float32")
    arr[80:120, 70:130] = 96.0                     # a 4 m deep flat-floored pit
    geo = tmp_path / "geo.tif"
    _write(geo, arr, "EPSG:4326", from_origin(-97.8, 30.4, px, px))
    from h3lowpoints import reproject_to_utm
    utm = reproject_to_utm(geo, tmp_path / "utm.tif")
    a, b = analyze(str(geo), res=9).depressions, analyze(str(utm), res=9).depressions
    assert len(a) == len(b) == 1
    assert a["max_depth_m"].iloc[0] == pytest.approx(b["max_depth_m"].iloc[0], abs=1e-3)
    assert a["area_m2"].iloc[0] == pytest.approx(b["area_m2"].iloc[0], rel=0.05)
    assert a["volume_m3"].iloc[0] == pytest.approx(b["volume_m3"].iloc[0], rel=0.05)


def test_feet_projected_dem_reports_square_metres(tmp_path):
    foot = 0.3048006096
    arr = np.full((60, 60), 50.0, dtype="float32")
    arr[20:30, 20:30] = 49.0                       # 10 x 10 pixels, each 10 ft wide
    p = tmp_path / "ft.tif"
    _write(p, arr, "EPSG:2278", from_origin(3000000, 10000000, 10.0, 10.0))
    dep = analyze(str(p)).depressions
    assert dep["area_m2"].iloc[0] == pytest.approx(100 * (10 * foot) ** 2, rel=1e-6)


def test_lowest_point_lands_in_the_right_h3_cell_on_a_geographic_grid(tmp_path):
    px = 1 / 3600
    arr = np.full((100, 100), 50.0, dtype="float32")
    arr[30, 40] = 40.0                             # a single deepest pixel
    tr = from_origin(-97.8, 30.4, px, px)
    dep = analyze_array(arr, tr, WGS84, -9999.0, res=10).depressions.iloc[0]
    lon, lat = tr @ (40.5, 30.5)
    assert dep["lon"] == pytest.approx(lon) and dep["lat"] == pytest.approx(lat)
    assert dep["h3_lowpoint"] == h3.latlng_to_cell(lat, lon, 10)


def test_drain_distance_and_core_on_a_geographic_grid():
    px = 1 / 3600
    arr = np.full((80, 80), 50.0, dtype="float32")
    arr[30:40, 30:40] = 48.0
    tr = from_origin(-97.8, 30.4, px, px)
    lon, lat = tr @ (35.0, 35.0)
    near = np.array([[lon, lat]])
    far = np.array([[lon + 0.002, lat]])           # ~190 m east
    a = analyze_array(arr, tr, WGS84, -9999.0, res=9, drains_xy=near).depressions.iloc[0]
    b = analyze_array(arr, tr, WGS84, -9999.0, res=9, drains_xy=far).depressions.iloc[0]
    assert bool(a["drain_at_core"]) and not bool(b["drain_at_core"])
    want = GEOD.inv(b["lon"], b["lat"], lon + 0.002, lat)[2]    # lowest point to the far drain
    assert b["dist_to_drain_m"] == pytest.approx(want, rel=1e-6)
    assert want > 250


def test_geographic_tiled_equals_whole(tmp_path):
    from scipy import ndimage

    rng = np.random.default_rng(3)
    n = 300
    arr = (ndimage.gaussian_filter(rng.normal(size=(n, n)), 2.0) * 10
           + np.linspace(0, 4, n)).astype("float32")
    p = tmp_path / "g.tif"
    _write(p, arr, "EPSG:4326", from_origin(-97.8, 60.0, 1 / 1800, 1 / 3600))
    whole = analyze(str(p), res=9).depressions
    tiled = analyze_tiled(str(p), tile=100, buffer=400, res=9).depressions
    key = ["lat", "lon"]
    a = whole.round({"lat": 7, "lon": 7}).sort_values(key).reset_index(drop=True)
    b = tiled.round({"lat": 7, "lon": 7}).sort_values(key).reset_index(drop=True)
    assert len(a) > 5 and len(a) == len(b)
    for col in ("max_depth_m", "area_m2", "volume_m3"):
        np.testing.assert_allclose(a[col], b[col], rtol=1e-7)


def test_cells_conserve_area_and_volume_on_a_geographic_grid():
    px = 1 / 3600
    arr = np.full((120, 120), 50.0, dtype="float32")
    arr[20:90, 20:90] = 47.0                       # spans several H3 cells at res 10
    tr = from_origin(-97.8, 70.2, px, px)          # high latitude: strong area gradient
    res = analyze_array(arr, tr, WGS84, -9999.0, res=11)
    assert len(res.cells) > 3
    assert res.cells["depress_area_m2"].sum() == pytest.approx(res.depressions["area_m2"].sum())
    assert res.cells["depress_volume_m3"].sum() == pytest.approx(res.depressions["volume_m3"].sum())


def test_resolution_tier_uses_ground_size_not_degrees():
    px = 1 / 3600                                  # ~30 m N-S, less E-W at 60 N
    arr = np.full((40, 40), 50.0, dtype="float32")
    arr[10:20, 10:20] = 40.0
    with pytest.warns(UserWarning, match="vertical error"):
        res = analyze_array(arr, from_origin(10.0, 60.0, px, px), WGS84, -9999.0, res=8)
    assert res.meta["geographic"] is True
    assert res.meta["resolution_tier"] in ("medium", "coarse")
    assert 10 < res.meta["cell_size_m"] < 40


def test_authalic_radius_is_the_published_value():
    assert EARTH_RADIUS_M == pytest.approx(6371007.1809, abs=1e-3)
