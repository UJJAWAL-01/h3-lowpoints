"""Tiled hooks: water history and flood events counted tile by tile must equal the in-memory
functions on the whole region, for any tile size, in memory and written to disk."""
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage

from h3lowpoints import analyze, analyze_tiled, read_result
from h3lowpoints.floods import FloodHook, flood_events
from h3lowpoints.sources import jrc
from h3lowpoints.water import WaterHook, water_history

ARC = 1 / 3600
DEM_T = from_origin(-95.2, 30.05, ARC, ARC)          # 1 arc-second, like 3DEP 30 m
SHAPE = (150, 200)
JRC_TILE = 100


def write(path, arr, transform, crs, nodata=None, dtype=None):
    arr = np.asarray(arr, dtype=dtype or arr.dtype)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype=arr.dtype, crs=crs, transform=transform,
                       nodata=nodata) as dst:
        dst.write(arr, 1)
    return str(path)


@pytest.fixture
def region(tmp_path, monkeypatch):
    """A DEM with pits and a nodata lake, synthetic JRC tiles over it, and three flood maps."""
    rng = np.random.default_rng(7)
    z = ndimage.gaussian_filter(rng.normal(size=SHAPE), 3) * 8 + np.linspace(0, 2, SHAPE[1])
    z[60:80, 90:120] = -9999.0                              # open water, masked as nodata
    dem = write(tmp_path / "dem.tif", z, DEM_T, "EPSG:4326", nodata=-9999.0, dtype="float32")

    monkeypatch.setattr(jrc, "TILE", JRC_TILE)
    src = tmp_path / "jrc"
    src.mkdir()
    row0, col0 = int((jrc.NORTH - 30.06) / jrc.PIXEL), int((180 - 95.21) / jrc.PIXEL)
    r0, c0 = row0 // JRC_TILE * JRC_TILE, col0 // JRC_TILE * JRC_TILE
    rows, cols = 4 * JRC_TILE, 4 * JRC_TILE
    ext = ndimage.gaussian_filter(rng.normal(size=(rows, cols)), 4) > 0.05
    layers = {"extent": ext.astype("uint8"),
              "occurrence": np.where(ext, rng.integers(1, 100, (rows, cols)), 0).astype("uint8")}
    for y in jrc.YEARS:
        v = rng.choice([0, 1, 2, 3], size=(rows, cols), p=[0.15, 0.55, 0.2, 0.1])
        if y in (1990, 2001):                                # two missing years over part of it
            v[: rows // 2] = 0
        layers[("yearly", y)] = np.where(ext, v, 0).astype("uint8")
    for m in jrc.MONTHS:
        layers[("monthly_recurrence", m)] = np.where(ext, rng.integers(0, 100, (rows, cols)),
                                                     0).astype("int16")
    for key, arr in layers.items():
        layer, kw = (key, {}) if isinstance(key, str) else (key[0], {"year" if key[0] == "yearly"
                                                                      else "month": key[1]})
        for i in range(0, rows, JRC_TILE):
            for j in range(0, cols, JRC_TILE):
                path = jrc.local_path(src, layer, r0 + i, c0 + j, **kw)   # server layout
                path.parent.mkdir(parents=True, exist_ok=True)
                write(path, arr[i:i + JRC_TILE, j:j + JRC_TILE], None, None)

    maps = {}
    utm = from_origin(270000, 3328000, 20, 20)              # EPSG:32615, over part of the DEM
    for k in range(2):
        a = rng.choice([0, 1, 255], size=(150, 220), p=[0.6, 0.25, 0.15]).astype("uint8")
        maps[f"utm{k}"] = write(tmp_path / f"utm{k}.tif", a, utm, "EPSG:32615", nodata=255)
    geo = from_origin(-95.19, 30.045, 0.0002, 0.0002)
    a = rng.choice([0, 1, 255], size=(180, 250), p=[0.5, 0.3, 0.2]).astype("uint8")
    maps["geo"] = write(tmp_path / "geo.tif", a, geo, "EPSG:4326", nodata=255)
    return dem, src, maps


def by_place(table, deps):
    """A per-depression table keyed by the depression's lowest point instead of its id."""
    key = deps[["id", "lat", "lon"]].round({"lat": 9, "lon": 9})
    out = table.merge(key, on="id").drop(columns="id")
    return out.sort_values(["lat", "lon"] + (["scene"] if "scene" in out else [])) \
        .reset_index(drop=True)


def assert_same(a, b):
    assert list(a.columns) == list(b.columns) and len(a) == len(b)
    for c in a.columns:
        if a[c].dtype.kind == "f":
            np.testing.assert_allclose(a[c], b[c], rtol=1e-9, atol=1e-9, equal_nan=True,
                                       err_msg=c)
        else:                                   # missing values compare equal (None or NaN)
            na, nb = a[c].isna().to_numpy(), b[c].isna().to_numpy()
            assert (na == nb).all(), c
            assert (a[c][~na].astype(str).to_numpy() == b[c][~nb].astype(str).to_numpy()).all(), c


@pytest.mark.parametrize("tile", [64, 37])
def test_tiled_water_history_equals_the_whole(region, tile):
    dem, src, _ = region
    whole = analyze(dem, min_depth=0.05)
    assert len(whole.depressions) > 20
    expected = water_history(whole, source=src)
    tiled = analyze_tiled(dem, tile=tile, min_depth=0.05, hooks=[WaterHook(source=src)])
    got = tiled.tables["water_history"]
    assert_same(by_place(expected, whole.depressions), by_place(got, tiled.depressions))
    assert (expected.water_class != "never_seen_wet").any()      # the test has wet depressions
    assert got.attrs["settings"]["method"] == "outline (tiled)"


@pytest.mark.parametrize("tile", [64, 37])
def test_tiled_flood_events_equal_the_whole(region, tile):
    dem, _, maps = region
    whole = analyze(dem, min_depth=0.05)
    summary, seen = flood_events(whole, maps)
    assert summary.scenes_flooded.sum() > 0 and summary.scenes_observed.sum() > 0
    tiled = analyze_tiled(dem, tile=tile, min_depth=0.05, hooks=[FloodHook(maps, workers=2)])
    assert_same(by_place(summary, whole.depressions),
                by_place(tiled.tables["flood_events"], tiled.depressions))
    assert_same(by_place(seen, whole.depressions),
                by_place(tiled.tables["flood_seen"], tiled.depressions))


def test_hooks_write_their_tables_and_resume(region, tmp_path):
    dem, src, maps = region
    out = tmp_path / "out"
    hooks = [WaterHook(source=src), FloodHook(maps)]
    res = analyze_tiled(dem, tile=50, min_depth=0.05, hooks=hooks, out_dir=out, format="parquet")
    assert (out / "water_history.parquet").exists() and (out / "flood_seen.parquet").exists()
    back = read_result(out)
    whole = analyze(dem, min_depth=0.05)
    assert_same(by_place(water_history(whole, source=src), whole.depressions),
                by_place(back.tables["water_history"], back.depressions))
    assert_same(by_place(flood_events(whole, maps)[0], whole.depressions),
                by_place(back.tables["flood_events"], back.depressions))
    assert res.path == out


def test_hooks_survive_a_run_split_into_parts(region, tmp_path, monkeypatch):
    from h3lowpoints import tiling as T

    dem, src, maps = region
    clock = {"t": 0.0}
    monkeypatch.setattr(T.time, "monotonic", lambda: clock["t"])
    real1, real2 = T._pass1_tile, T._pass2_tile

    def tick(real):
        def run(*a, **k):
            clock["t"] += 3600.0                    # every tile "takes" an hour
            return real(*a, **k)
        return run
    monkeypatch.setattr(T, "_pass1_tile", tick(real1))
    monkeypatch.setattr(T, "_pass2_tile", tick(real2))
    out, parts = tmp_path / "run", 0
    while True:
        try:
            analyze_tiled(dem, tile=50, min_depth=0.05, out_dir=out, format="parquet", hours=2.5,
                          hooks=[WaterHook(source=src), FloodHook(maps)])
            break
        except T.Paused:
            parts += 1
        assert parts < 40
    assert parts >= 3
    back = read_result(out)
    whole = analyze(dem, min_depth=0.05)
    assert_same(by_place(water_history(whole, source=src), whole.depressions),
                by_place(back.tables["water_history"], back.depressions))
    assert_same(by_place(flood_events(whole, maps)[1], whole.depressions),
                by_place(back.tables["flood_seen"], back.depressions))


# ---------------------------------------------------------------- the road-crossing fix, tiled

def dammed_valleys(tmp_path):
    """Valleys draining east, each dammed by road embankments, with streams along them, roads
    across them (some on tile seams), a bridge, and noise so that pits abound."""
    import geopandas as gpd
    from shapely.geometry import LineString

    rng = np.random.default_rng(3)
    h, w = 120, 160
    r, c = np.mgrid[0:h, 0:w].astype(float)
    z = 50 - 0.03 * c + 0.4 * np.abs(((r + 10) % 40) - 20) + rng.normal(0, 0.05, (h, w))
    roads_c = [37, 64, 100, 128]                           # 64 and 128 lie on tile seams
    for col in roads_c:
        z[:, col - 1:col + 2] += 3.0
    z[55:62, 70:76] = -9999.0                               # a little nodata
    dem = write(tmp_path / "valleys.tif", z.astype("float32"), DEM_T, "EPSG:4326", nodata=-9999.0)

    def pt(row, col):
        return DEM_T @ (col + 0.5, row + 0.5)
    streams = [LineString([pt(row, 0), pt(row, w - 1)]) for row in (10, 50, 90)]
    roads = [LineString([pt(0, col), pt(h - 1, col)]) for col in roads_c]
    bridges = [LineString([pt(44, 58), pt(56, 58)])]        # across the pond behind column 64
    crs = "EPSG:4326"
    return dem, (gpd.GeoDataFrame(geometry=roads, crs=crs), gpd.GeoDataFrame(geometry=streams, crs=crs),
                 gpd.GeoDataFrame(geometry=bridges, crs=crs))


def same_depressions(whole, tiled):
    a = whole.depressions.round({"lat": 9, "lon": 9}).sort_values(["lat", "lon"]).reset_index(drop=True)
    b = tiled.depressions.round({"lat": 9, "lon": 9}).sort_values(["lat", "lon"]).reset_index(drop=True)
    assert list(a.columns) == list(b.columns) and len(a) == len(b)
    assert_same(a.drop(columns="id"), b.drop(columns="id"))


@pytest.mark.parametrize("tile", [64, 45, 1000])
def test_tiled_road_fix_equals_the_whole(tmp_path, tile):
    dem, lines = dammed_valleys(tmp_path)
    whole = analyze(dem, min_depth=0.15, breach_crossings=lines)
    d = whole.depressions
    assert d.road_dam.sum() >= 3 and (~d.road_dam).sum() >= 3 and d.bridge_crossing.any()
    tiled = analyze_tiled(dem, tile=tile, min_depth=0.15, breach_crossings=lines)
    same_depressions(whole, tiled)
    for k in ("n_crossings", "n_crossings_cut", "n_pixels_lowered", "n_road_dam",
              "n_bridge_crossing", "n_bridges"):
        assert whole.meta["breach"][k] == tiled.meta["breach"][k], k


@pytest.mark.parametrize("tile", [25, 1000])
def test_tiled_road_fix_caps_a_cut_pit_like_the_whole(tmp_path, tile):
    """The terrain of test_confidence.test_a_cut_pit_inside_a_pond_adds_no_depth, tiled."""
    import geopandas as gpd
    from rasterio.transform import from_origin
    from scipy.ndimage import gaussian_filter
    from shapely.geometry import LineString

    n, t, crs = 60, from_origin(500000, 3300000, 30, 30), "EPSG:32615"
    rng = np.random.default_rng(15)
    z = 100 + gaussian_filter(rng.normal(size=(n, n)), 1.5) * 10
    mask = gaussian_filter(rng.normal(size=(n, n)), 2) > 0.25

    def xy(row, col):
        return t @ (col + 0.5, row + 0.5)
    streams = [LineString([xy(rng.integers(0, n), 0), xy(rng.integers(0, n), n - 1)]) for _ in range(4)]
    roads = [LineString([xy(0, rng.integers(0, n)), xy(n - 1, rng.integers(0, n))]) for _ in range(4)]
    lines = (gpd.GeoDataFrame(geometry=roads, crs=crs), gpd.GeoDataFrame(geometry=streams, crs=crs))
    dem = write(tmp_path / "rough.tif", z, t, crs)
    msk = write(tmp_path / "mask.tif", mask.astype("uint8"), t, crs)
    whole = analyze(dem, mask=mask, min_depth=0.15, res=9, breach_crossings=lines)
    tiled = analyze_tiled(dem, mask_path=msk, tile=tile, min_depth=0.15, res=9, breach_crossings=lines)
    same_depressions(whole, tiled)
    d = tiled.depressions
    assert (d.breached_depth_m <= d.max_depth_m + 1e-12).all()


def test_tiled_road_fix_survives_a_split_run(tmp_path, monkeypatch):
    from h3lowpoints import tiling as T

    dem, lines = dammed_valleys(tmp_path)
    whole = analyze(dem, min_depth=0.15, breach_crossings=lines)
    clock = {"t": 0.0}
    monkeypatch.setattr(T.time, "monotonic", lambda: clock["t"])
    real1, real2 = T._pass1_tile, T._pass2_tile

    def tick(real):
        def run(*a, **k):
            clock["t"] += 3600.0
            return real(*a, **k)
        return run
    monkeypatch.setattr(T, "_pass1_tile", tick(real1))
    monkeypatch.setattr(T, "_pass2_tile", tick(real2))
    out, parts = tmp_path / "run", 0
    while True:
        try:
            analyze_tiled(dem, tile=45, min_depth=0.15, breach_crossings=lines, out_dir=out,
                          format="parquet", hours=2.5)
            break
        except T.Paused:
            parts += 1
        assert parts < 60
    assert parts >= 5
    back = read_result(out)
    same_depressions(whole, back)
    assert back.meta["breach"]["n_road_dam"] == whole.meta["breach"]["n_road_dam"]


def test_clip_keeps_the_hook_tables_of_the_depressions_it_keeps(region, tmp_path):
    from shapely.geometry import box

    from h3lowpoints.clip import clip_result

    dem, src, maps = region
    out = tmp_path / "out"
    analyze_tiled(dem, tile=64, min_depth=0.05, hooks=[WaterHook(source=src), FloodHook(maps)],
                  out_dir=out, format="parquet")
    clipped = clip_result(out, box(-95.2, 30.0, -95.16, 30.05), tmp_path / "clipped")
    back = read_result(clipped)
    ids = set(back.depressions["id"])
    assert 0 < len(ids) < len(read_result(out).depressions)
    for name in ("water_history", "flood_events", "flood_seen"):
        assert set(back.tables[name]["id"]) <= ids
    assert set(back.tables["water_history"]["id"]) == ids


def test_verify_checks_the_evidence_tables(region, tmp_path):
    from h3lowpoints.verify import check_saved

    dem, src, maps = region
    out = tmp_path / "out"
    analyze_tiled(dem, tile=64, min_depth=0.05, hooks=[WaterHook(source=src), FloodHook(maps)],
                  out_dir=out, format="parquet")
    assert check_saved(out) == []
    meta = read_result(out).meta
    assert meta["tables"]["water_history"]["method"] == "outline (tiled)"
    w = pd.read_parquet(out / "water_history.parquet")
    w.loc[0, "wet_years"] = 99
    w.to_parquet(out / "water_history.parquet")
    f = pd.read_parquet(out / "flood_events.parquet")
    f.loc[0, "scenes_flooded"] = f.loc[0, "scenes_observed"] + 1
    f.to_parquet(out / "flood_events.parquet")
    problems = " ".join(check_saved(out))
    assert "wet_years outside" in problems and "scenes_flooded" in problems


def test_verify_checks_the_road_columns(tmp_path):
    from h3lowpoints.verify import check_saved

    dem, lines = dammed_valleys(tmp_path)
    out = tmp_path / "out"
    analyze_tiled(dem, tile=64, min_depth=0.15, breach_crossings=lines, out_dir=out,
                  format="parquet")
    assert check_saved(out) == []
    d = pd.read_parquet(out / "depressions.parquet")
    d.loc[0, "road_dam"] = not d.loc[0, "road_dam"]
    d.to_parquet(out / "depressions.parquet")
    assert any("road_dam" in p for p in check_saved(out))
