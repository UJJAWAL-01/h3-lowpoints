"""Catchments and spill links computed tile by tile must equal the whole-raster answer.

`analyze(..., catchments=True)` is the oracle (itself checked pixel by pixel in
`test_routing.py`). Every test builds a DEM, runs both, and compares each depression's catchment
area, spill target and spill point, and every row of the catchment table, after matching
depressions by their lowest point (tiled and whole number them differently).
"""
import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin

from h3lowpoints import analyze, analyze_tiled, read_result
from h3lowpoints import tiling as T
from h3lowpoints.cli import main
from h3lowpoints.pipeline import CATCHMENT_COLUMNS
from h3lowpoints.verify import check_saved
from test_store import FORMATS
from test_tiling import _maze, smooth, write_dem

KW = dict(res=12, min_depth=0.2, catchments=True)


def same_routing(whole, tiled):
    da, db = whole.depressions, tiled.depressions
    assert len(da) == len(db), f"{len(da)} depressions whole vs {len(db)} tiled"
    a2b = dict(zip(da["h3_lowpoint_15"], da["id"]))
    a2b = {a2b[k]: i for k, i in zip(db["h3_lowpoint_15"], db["id"])}
    a2b[0] = 0
    da = da.set_index("h3_lowpoint_15").sort_index()
    db = db.set_index("h3_lowpoint_15").sort_index()
    np.testing.assert_allclose(da["catchment_area_m2"], db["catchment_area_m2"], rtol=1e-9)
    assert (da["spill_to"].map(a2b).to_numpy() == db["spill_to"].to_numpy()).all()
    np.testing.assert_array_equal(da["spill_lat"], db["spill_lat"])
    np.testing.assert_array_equal(da["spill_lon"], db["spill_lon"])
    ca = whole.catchments.assign(id=whole.catchments["id"].map(a2b))
    ca = ca.sort_values(["h3", "id"]).reset_index(drop=True)
    cb = tiled.catchments.sort_values(["h3", "id"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(ca[["id", "h3"]], cb[["id", "h3"]], check_dtype=False)
    np.testing.assert_allclose(ca["area_m2"], cb["area_m2"], rtol=1e-9)


def check(path, tiles, **kwargs):
    kwargs = {**KW, **kwargs}
    whole = analyze(path, **kwargs)
    for t in tiles:
        same_routing(whole, analyze_tiled(path, tile=t, **kwargs))
    return whole


def terrain(seed, kind, shape=(70, 90)):
    z = smooth(seed, shape)
    if kind == "flats":
        z = np.round(z)                                # flats and ties crossing every seam
    elif kind == "half":
        z = np.round(z * 2) / 2
    elif kind == "nodata":
        z[15:32, 50:66] = -9999.0
    return z


@pytest.mark.parametrize("kind", ["smooth", "flats", "half", "nodata"])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_random_terrain_any_tile_size(tmp_path, seed, kind):
    p = write_dem(tmp_path / "d.tif", terrain(seed, kind))
    whole = check(p, [7, 16, 33])
    assert len(whole.depressions) > 10


@pytest.mark.parametrize("tile", [4, 7, 19])
def test_a_flat_corridor_through_every_tile(tmp_path, tile):
    """A perfectly flat winding corridor: every flat pixel's nearest way off lies tiles away."""
    arr, path = _maze()
    for r, c in path[:-1]:
        arr[r, c] = 5.0                                # flat, falling only at its very end
    arr[3:6, 30:33] = 9.0                              # a basin on the wall, spilling into it
    arr[4, 31] = 8.0
    p = write_dem(tmp_path / "d.tif", arr)
    whole = check(p, [tile], res=10, min_depth=0.1)
    assert len(whole.depressions) >= 1


def test_a_lake_that_overflows_through_a_tile_without_any_sink(tmp_path):
    """The lake lies in one tile, its long level saddle crosses the next tile and its first way
    out lies in a third: the overflow point must still be found (a regression)."""
    arr = np.full((30, 60), 10.0)
    arr[5:25, 3:15] = 6.0                              # the lake, in the first tile
    arr[14, 15:45] = 8.0                               # a level saddle through the next tiles
    arr[14, 45:60] = np.linspace(7.9, 7.0, 15)         # then down to the map edge
    p = write_dem(tmp_path / "d.tif", arr)
    whole = check(p, [16, 20], res=10, min_depth=0.5)
    lake = whole.depressions.iloc[0]
    assert lake["spill_to"] == 0 and lake["spill_lon"] > whole.depressions["lon"].iloc[0]


def test_geographic_grid(tmp_path):
    p = write_dem(tmp_path / "d.tif", terrain(3, "half"), crs="EPSG:4326",
                  transform=from_origin(-98.0, 45.0, 0.0003, 0.0002))
    check(p, [9, 30], res=11)


def test_a_mask_routes_like_nodata(tmp_path):
    z = terrain(4, "smooth")
    mask = np.zeros(z.shape, dtype=np.uint8)
    mask[20:40, 10:30] = 1
    masked = z.copy()
    masked[mask.astype(bool)] = -9999.0
    a = analyze(write_dem(tmp_path / "nodata.tif", masked), **KW)
    p = write_dem(tmp_path / "d.tif", z)
    m = write_dem(tmp_path / "m.tif", mask, nodata=None)
    same_routing(a, analyze_tiled(p, tile=17, mask_path=m, **KW))


def test_without_catchments_the_tiled_output_is_unchanged(tmp_path):
    p = write_dem(tmp_path / "d.tif", terrain(5, "smooth"))
    plain = analyze_tiled(p, tile=20, res=12, min_depth=0.2)
    routed = analyze_tiled(p, tile=20, **KW)
    assert plain.catchments is None
    pd.testing.assert_frame_equal(plain.depressions, routed.depressions.drop(columns=CATCHMENT_COLUMNS))
    pd.testing.assert_frame_equal(plain.cells, routed.cells)


@pytest.mark.parametrize("fmt", FORMATS)
def test_written_to_disk_equals_in_memory(tmp_path, fmt):
    p = write_dem(tmp_path / "d.tif", terrain(6, "half"))
    mem = analyze_tiled(p, tile=20, **KW)
    disk = analyze_tiled(p, tile=20, out_dir=tmp_path / "out", format=fmt, **KW).load()
    pd.testing.assert_frame_equal(mem.depressions, disk.depressions, check_dtype=False)
    pd.testing.assert_frame_equal(mem.catchments, disk.catchments, check_dtype=False)
    assert (tmp_path / "out" / f"catchments.{fmt}").exists()
    assert not (tmp_path / "out" / "_spill_catchments").exists()
    assert check_saved(tmp_path / "out") == []


@pytest.mark.parametrize("where, n", [("_pass2_tile", 1), ("_pass2_tile", 9),
                                      ("_settle_routing", 1), ("_write", 1)])
def test_a_crash_then_continuing_gives_the_uninterrupted_result(tmp_path, monkeypatch, where, n):
    p = write_dem(tmp_path / "d.tif", terrain(7, "half"))
    reference = analyze_tiled(p, tile=20, out_dir=tmp_path / "ref", **KW).load()
    real, calls = getattr(T, where), {"n": 0}

    def crash(*a, **k):
        calls["n"] += 1
        if calls["n"] == n:
            raise RuntimeError("simulated crash")
        return real(*a, **k)
    monkeypatch.setattr(T, where, crash)
    with pytest.raises(RuntimeError, match="simulated"):
        analyze_tiled(p, tile=20, out_dir=tmp_path / "run", **KW)
    monkeypatch.setattr(T, where, real)
    got = analyze_tiled(p, tile=20, out_dir=tmp_path / "run", **KW).load()
    pd.testing.assert_frame_equal(got.depressions, reference.depressions)
    pd.testing.assert_frame_equal(got.catchments, reference.catchments)


def test_a_run_started_without_catchments_cannot_continue_with_them(tmp_path, monkeypatch):
    p = write_dem(tmp_path / "d.tif", terrain(8, "smooth"))
    real = T._pass2_tile
    monkeypatch.setattr(T, "_pass2_tile", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        analyze_tiled(p, tile=20, out_dir=tmp_path / "run", res=12, min_depth=0.2)
    monkeypatch.setattr(T, "_pass2_tile", real)
    with pytest.raises(ValueError, match="catchments"):
        analyze_tiled(p, tile=20, out_dir=tmp_path / "run", **KW)


def test_command_line_option(tmp_path):
    p = write_dem(tmp_path / "d.tif", terrain(9, "smooth"))
    assert main(["analyze", p, "--catchments", "--tile", "25", "--res", "12", "--min-depth", "0.2",
                 "--out", str(tmp_path / "out")]) == 0
    got = read_result(tmp_path / "out")
    same_routing(analyze(p, **KW), got)


# ------------------------------------------------------------------ checks of saved results

def _saved(tmp_path):
    p = write_dem(tmp_path / "d.tif", terrain(10, "half"))
    return analyze_tiled(p, tile=25, out_dir=tmp_path / "out", **KW).path


def _corrupt(folder, name, change):
    from h3lowpoints.store import read_table, write_table

    table = change(read_table(folder, name))
    write_table(table, folder / f"{name}.csv")


CORRUPTIONS = {
    "the catchment table does not add up": ("catchments", lambda c: c.assign(area_m2=c["area_m2"] * 1.01)),
    "smaller than its own depression": ("depressions", lambda d: d.assign(catchment_area_m2=d["area_m2"] / 2)),
    "spills into itself": ("depressions", lambda d: d.assign(spill_to=d["id"])),
    "loop": ("depressions", lambda d: d.assign(spill_to=d["id"].shift(-1).fillna(d["id"].iloc[0]).astype("int64"))),
    "not in the table": ("depressions", lambda d: d.assign(spill_to=d["id"].max() + 7)),
    "names depressions": ("catchments", lambda c: c.assign(id=c["id"] + 100000)),
}


@pytest.mark.parametrize("message", list(CORRUPTIONS))
def test_verify_catches_broken_catchments(tmp_path, message):
    folder = _saved(tmp_path)
    assert check_saved(folder) == []
    _corrupt(folder, *CORRUPTIONS[message])
    assert any(message in p for p in check_saved(folder)), check_saved(folder)


def test_clip_keeps_whole_catchments_of_kept_depressions(tmp_path):
    import shapely

    from h3lowpoints.clip import clip_result

    folder = _saved(tmp_path)
    full = read_result(folder)
    west = shapely.box(-180, -90, float(full.depressions["lon"].median()), 90)
    out = clip_result(folder, west, tmp_path / "clipped")
    got = read_result(out)
    kept = set(got.depressions["id"])
    want = full.catchments[full.catchments["id"].isin(kept)].reset_index(drop=True)
    pd.testing.assert_frame_equal(got.catchments, want, check_dtype=False)
    assert check_saved(out) == []


@pytest.mark.network
def test_real_terrain_houston(tmp_path):
    """USGS 3DEP 30 m over west Houston (1.36 million pixels): tiled equals whole."""
    from h3lowpoints.sources.usgs import fetch_3dep

    p = fetch_3dep((-95.65, 29.6, -95.3, 29.9), tmp_path / "houston.tif")
    whole = analyze(p, min_depth=0.5, catchments=True)
    for tile in (100, 333, 512):
        same_routing(whole, analyze_tiled(p, tile=tile, min_depth=0.5, catchments=True))
    assert len(whole.depressions) > 2000
