"""Exact tiling: `analyze_tiled` must equal `analyze` whatever the tile size or terrain.

The whole-raster analysis is the oracle. Every test builds a DEM, runs both, and compares every
depression and every H3 cell.
"""
import h3
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import box

from h3lowpoints import analyze, analyze_tiled
from h3lowpoints import tiling as T
from h3lowpoints.cli import main
from h3lowpoints.hydro.depressions import fill_depressions
from h3lowpoints.pipeline import CELL_TABLE
from h3lowpoints.sources._common import GDAL_CACHE_MB
from h3lowpoints.store import PART_DEPTH, CellParts, partition_key
from h3lowpoints.store import ids_to_int as S_ids
from h3lowpoints.hydro.tiled import (flood_from_seeds, neighbour_pairs, region_edges,
                                     solve_levels, tile_seeds)

UTM = from_origin(500000, 3350000, 1, 1)


def write_dem(path, arr, crs="EPSG:26914", transform=UTM, nodata=-9999.0):
    prof = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": "float32", "crs": crs, "transform": transform, "nodata": nodata}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(np.asarray(arr, dtype="float32"), 1)
    return str(path)


def smooth(seed, shape, sigma=2.0, trend=3.0):
    rng = np.random.default_rng(seed)
    return ndimage.gaussian_filter(rng.normal(size=shape), sigma) * 10 + np.linspace(0, trend, shape[1])


def assert_equivalent(whole, tiled, drains=False, buildings=False):
    """Same depressions (by position) with the same numbers, and the same H3 cells."""
    keep = ["lat", "lon"]
    a = whole.depressions.round({"lat": 7, "lon": 7}).sort_values(keep).reset_index(drop=True)
    b = tiled.depressions.round({"lat": 7, "lon": 7}).sort_values(keep).reset_index(drop=True)
    assert list(a.columns) == list(b.columns)
    assert len(a) == len(b), f"{len(a)} depressions whole vs {len(b)} tiled"
    if len(a) == 0:
        assert len(whole.cells) == len(tiled.cells) == 0
        assert list(whole.cells.columns) == list(tiled.cells.columns)
        return
    for col in a.columns:
        if col == "id":
            continue
        if a[col].dtype.kind == "f":
            np.testing.assert_allclose(a[col], b[col], rtol=1e-9, atol=1e-9, equal_nan=True,
                                       err_msg=col)
        else:
            assert (a[col] == b[col]).all(), col
    ca = whole.cells.sort_values("h3").reset_index(drop=True)
    cb = tiled.cells.sort_values("h3").reset_index(drop=True)
    assert list(ca["h3"]) == list(cb["h3"])
    for col in ca.columns:
        if col != "h3":
            np.testing.assert_allclose(ca[col], cb[col], rtol=1e-9, atol=1e-9, err_msg=col)
    assert whole.meta["n_depressions"] == tiled.meta["n_depressions"]
    assert whole.meta["n_edge"] == tiled.meta["n_edge"]


def check(path, tiles, **kwargs):
    whole = analyze(path, **kwargs)
    for t in tiles:
        assert_equivalent(whole, analyze_tiled(path, tile=t, **kwargs))
    return whole


# ------------------------------------------------------------------ random terrain

@pytest.mark.parametrize("shape", [(61, 97), (40, 40), (97, 13), (3, 70), (70, 3), (2, 2), (1, 1),
                                   (1, 40), (40, 1)])
@pytest.mark.parametrize("seed", [0, 1])
def test_random_terrain_any_tile_size(tmp_path, shape, seed):
    p = write_dem(tmp_path / "d.tif", smooth(seed, shape))
    tiles = [5, 16, 1000] if min(shape) > 3 else [1, 2, 3, 1000]
    check(p, tiles, res=10, min_depth=0.05)


@pytest.mark.parametrize("tile", [1, 2, 3, 4, 5, 6, 7, 9, 11])
def test_tiny_tiles_on_a_small_raster(tmp_path, tile):
    p = write_dem(tmp_path / "d.tif", smooth(5, (23, 29), sigma=1.5))
    check(p, [tile], res=10, min_depth=0.02)


@pytest.mark.parametrize("sigma", [0.7, 1.5, 4.0, 8.0])
def test_roughness_from_noise_to_broad_basins(tmp_path, sigma):
    p = write_dem(tmp_path / "d.tif", smooth(7, (90, 110), sigma=sigma))
    check(p, [13, 32, 50], res=10, min_depth=0.05)


def test_the_result_does_not_depend_on_the_tile_size(tmp_path):
    p = write_dem(tmp_path / "d.tif", smooth(11, (80, 80)))
    a = analyze_tiled(p, tile=17, res=10, min_depth=0.05)
    b = analyze_tiled(p, tile=41, res=10, min_depth=0.05)
    pd.testing.assert_frame_equal(a.depressions, b.depressions)
    pd.testing.assert_frame_equal(a.cells, b.cells)


# ------------------------------------------------------------------ plateaus and ties

@pytest.mark.parametrize("levels", [2, 4, 10])
def test_terraced_terrain_full_of_ties(tmp_path, levels):
    arr = np.round(smooth(3, (70, 70), sigma=3.0) / 10 * levels) / levels * 10
    p = write_dem(tmp_path / "d.tif", arr)
    check(p, [5, 16, 31], res=10, min_depth=0.1)


def test_flat_floor_bowl_across_a_seam_has_one_global_lowest_point(tmp_path):
    arr = np.full((60, 60), 10.0)
    arr[20:40, 20:40] = 8.0                         # flat floor, centred on the 30/30 seam
    p = write_dem(tmp_path / "d.tif", arr)
    from pyproj import Transformer

    whole = check(p, [30, 31, 29, 10], res=10, min_depth=0.5)
    assert len(whole.depressions) == 1
    # the first tied cell in row-major order is the floor's top-left corner, wherever the seams fall
    lon, lat = Transformer.from_crs("EPSG:26914", 4326, always_xy=True).transform(
        500000 + 20.5, 3350000 - 20.5)
    for t in (30, 31, 29, 10, 7):
        got = analyze_tiled(p, tile=t, res=10, min_depth=0.5).depressions.iloc[0]
        assert (got["lat"], got["lon"]) == pytest.approx((lat, lon), abs=1e-9)


def test_a_dead_flat_raster_has_no_depressions(tmp_path):
    p = write_dem(tmp_path / "d.tif", np.full((50, 50), 7.0))
    whole = check(p, [8, 1000], res=10)
    assert len(whole.depressions) == 0
    assert analyze_tiled(p, tile=8).cells.empty


def test_a_tilted_plane_has_no_depressions(tmp_path):
    y, x = np.mgrid[0:50, 0:60]
    p = write_dem(tmp_path / "d.tif", 100 - 0.1 * x - 0.05 * y)
    assert len(check(p, [7, 20], res=10).depressions) == 0


# ------------------------------------------------------------------ water that travels far

def _maze(n_rows=9, width=60, wall=10.0):
    """A serpentine corridor: water must wind through every row to reach the outlet."""
    rows = n_rows * 2 + 1
    arr = np.full((rows, width), wall)
    k = 0
    path = []
    for i in range(n_rows):
        r = 1 + 2 * i
        cols = range(1, width - 1) if i % 2 == 0 else range(width - 2, 0, -1)
        path += [(r, c) for c in cols]
        if i < n_rows - 1:
            c = width - 2 if i % 2 == 0 else 1
            path.append((r + 1, c))
    for k, (r, c) in enumerate(path):
        arr[r, c] = 5.0 - 0.002 * k                 # the corridor falls gently towards the outlet
    arr[path[-1]] = 5.0 - 0.002 * len(path)
    last = path[-1]
    arr[last[0], 0 if last[1] < width / 2 else width - 1] = 1.0   # the outlet, on the raster border
    return arr, path


@pytest.mark.parametrize("tile", [4, 7, 10, 19])
def test_serpentine_corridor_needs_the_whole_graph(tmp_path, tile):
    arr, path = _maze()
    rng = np.random.default_rng(0)
    for k in rng.choice(len(path), 25, replace=False):      # pits and sills along the corridor
        r, c = path[k]
        arr[r, c] -= rng.uniform(0.2, 1.5)
    p = write_dem(tmp_path / "d.tif", arr)
    whole = check(p, [tile], res=10, min_depth=0.1)
    assert len(whole.depressions) > 3


def test_a_lake_whose_sill_is_far_away(tmp_path):
    y, x = np.mgrid[0:100, 0:100]
    arr = 20 - 0.05 * np.hypot(x - 50, y - 50)           # a broad dome
    r = np.hypot(x - 50, y - 50)
    arr[r < 35] = 12 + 4 * (r[r < 35] / 35) ** 2          # a big basin inside it, rim at 16
    arr[r < 35] = np.minimum(arr[r < 35], 16.0)
    arr[3:7, 90:99] = 9.0                                 # a low channel...
    arr[6, 70:99] = 9.0                                   # ...cutting the rim far from the centre
    arr[(r >= 34) & (r <= 36) & (x > 70) & (y < 15)] = 9.0
    p = write_dem(tmp_path / "d.tif", arr)
    whole = check(p, [9, 25, 40], res=10, min_depth=0.2)
    assert whole.depressions["area_m2"].max() > 500


def test_deep_pits_inside_a_big_flat_lake(tmp_path):
    arr = np.full((80, 80), 10.0)
    arr[10:70, 10:70] = 4.0                            # lake floor
    arr[30:34, 30:34] = 1.0                            # a deeper hole
    arr[5, 5:75] = 4.5                                 # a low pass on the rim leads out
    arr[5, 74] = 3.0
    arr[5, 75:] = 3.0
    p = write_dem(tmp_path / "d.tif", arr)
    check(p, [6, 17, 40], res=10, min_depth=0.1)


# ------------------------------------------------------------------ nodata

def _with_nodata(arr, blobs, rng):
    arr = arr.copy()
    for _ in range(blobs):
        r, c = rng.integers(0, arr.shape[0]), rng.integers(0, arr.shape[1])
        h, w = rng.integers(1, 8), rng.integers(1, 8)
        arr[r:r + h, c:c + w] = -9999.0
    return arr


@pytest.mark.parametrize("seed", range(6))
def test_nodata_blobs_and_islands(tmp_path, seed):
    rng = np.random.default_rng(seed)
    arr = _with_nodata(smooth(seed, (70, 80)), 12, rng)
    p = write_dem(tmp_path / "d.tif", arr)
    check(p, [1000, 11, 23], res=10, min_depth=0.05)


@pytest.mark.parametrize("tile", [8, 16])
def test_nodata_exactly_on_a_seam(tmp_path, tile):
    arr = smooth(2, (64, 64))
    arr[:, tile - 1:tile + 1] = -9999.0                # a vertical cut through the seam
    arr[tile, 10:30] = -9999.0
    arr[tile - 1, 40:50] = -9999.0
    p = write_dem(tmp_path / "d.tif", arr)
    check(p, [tile], res=10, min_depth=0.05)


def test_whole_tiles_of_nodata(tmp_path):
    arr = smooth(4, (64, 64))
    arr[16:32, 16:32] = -9999.0                        # one tile entirely invalid
    arr[32:48, :16] = -9999.0
    p = write_dem(tmp_path / "d.tif", arr)
    check(p, [16, 8], res=10, min_depth=0.05)


def test_valid_cells_next_to_an_invalid_cell_in_another_tile_are_outlets(tmp_path):
    arr = np.full((20, 20), 10.0)
    arr[5:9, 8:15] = 5.0                               # a pit whose rim touches the seam at col 10
    arr[7, 10:12] = -9999.0                            # nodata on the far side drains it
    p = write_dem(tmp_path / "d.tif", arr)
    check(p, [10, 5, 20], res=10, min_depth=0.1)


def test_diagonal_neighbours_across_a_tile_corner(tmp_path):
    arr = np.full((12, 12), 10.0)
    arr[5, 5] = 5.0                                    # pit at the corner of four 6x6 tiles...
    arr[6, 6] = 5.0                                    # ...and its diagonal twin: one depression
    arr[5, 6], arr[6, 5] = 10.0, 10.0
    p = write_dem(tmp_path / "d.tif", arr)
    whole = check(p, [6, 3, 12], res=10, min_depth=0.1)
    assert len(whole.depressions) == 1


def test_everything_nodata(tmp_path):
    p = write_dem(tmp_path / "d.tif", np.full((20, 20), -9999.0))
    assert len(analyze_tiled(p, tile=7).depressions) == 0
    assert len(analyze(p).depressions) == 0


def test_a_mask_equals_nodata(tmp_path):
    arr = smooth(8, (60, 60))
    mask = np.zeros(arr.shape, dtype=bool)
    mask[10:25, 20:40] = True
    mask[40, :] = True
    dem = write_dem(tmp_path / "d.tif", arr)
    mask_path = write_dem(tmp_path / "m.tif", mask.astype("float32"), nodata=None)
    whole = analyze(dem, res=10, min_depth=0.05, mask=mask)
    for t in (9, 25):
        assert_equivalent(whole, analyze_tiled(dem, tile=t, res=10, min_depth=0.05,
                                               mask_path=mask_path))
    arr_with_holes = arr.copy()
    arr_with_holes[mask] = -9999.0
    holes = analyze(write_dem(tmp_path / "h.tif", arr_with_holes), res=10, min_depth=0.05)
    assert_equivalent(whole, holes)


# ------------------------------------------------------------------ context layers

def _drains(rng, n, tr_origin=(500000, 3350000), shape=(80, 80)):
    return np.c_[tr_origin[0] + rng.uniform(-5, shape[1] + 5, n),
                 tr_origin[1] - rng.uniform(-5, shape[0] + 5, n)]


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("radius, frac", [(5.0, 0.8), (2.0, 0.0), (9.0, 1.0)])
def test_drains_match(tmp_path, seed, radius, frac):
    rng = np.random.default_rng(seed)
    p = write_dem(tmp_path / "d.tif", smooth(seed, (80, 80)))
    drains = _drains(rng, 60)
    check(p, [13, 40], res=10, min_depth=0.05, drains_xy=drains, drain_radius_m=radius,
          drain_core_frac=frac)


def test_a_drain_next_to_a_seam_reaches_across_it(tmp_path):
    arr = np.full((40, 40), 10.0)
    arr[15:25, 12:22] = 6.0                            # pit spanning the seam at col 20
    p = write_dem(tmp_path / "d.tif", arr)
    drain = np.array([[500000 + 22.5, 3350000 - 20.5]])      # just right of the pit, in the next tile
    whole = analyze(p, res=10, drains_xy=drain, drain_radius_m=3.0)
    assert bool(whole.depressions["drain_at_core"].iloc[0])
    assert_equivalent(whole, analyze_tiled(p, tile=20, res=10, drains_xy=drain, drain_radius_m=3.0))


@pytest.mark.parametrize("seed", range(3))
def test_buildings_match(tmp_path, seed):
    rng = np.random.default_rng(seed)
    p = write_dem(tmp_path / "d.tif", smooth(seed, (80, 80)))
    footprints = []
    for _ in range(25):
        x, y = 500000 + rng.uniform(0, 75), 3350000 - rng.uniform(0, 75)
        footprints.append(box(x, y - rng.uniform(1, 9), x + rng.uniform(1, 9), y))   # some < 20 m2
    check(p, [13, 40], res=10, min_depth=0.05, buildings=footprints)


def test_buildings_and_drains_together_with_nothing_to_find(tmp_path):
    p = write_dem(tmp_path / "d.tif", np.full((30, 30), 5.0))
    whole = check(p, [8], res=10, buildings=[box(500005, 3349990, 500015, 3349980)],
                  drains_xy=np.array([[500010.0, 3349985.0]]))
    assert list(whole.depressions.columns) == [
        "id", "max_depth_m", "area_m2", "volume_m3", "lat", "lon", "h3_lowpoint", "h3_lowpoint_15",
        "edge", "deeper_than_wide", "dist_to_drain_m", "drain_at_core", "building_overlap",
        "likely_building_artifact"]
    assert list(analyze_tiled(p, tile=8, buildings=[box(1, 1, 2, 2)],
                              drains_xy=np.array([[1.0, 1.0]])).depressions.columns) == \
        list(whole.depressions.columns)


# ------------------------------------------------------------------ geographic grids

@pytest.mark.parametrize("lat_top", [30.0, 62.0, -75.0])
def test_geographic_grid(tmp_path, lat_top):
    tr = from_origin(-97.8, lat_top, 1 / 1800, 1 / 3600)       # non-square pixels
    p = write_dem(tmp_path / "g.tif", smooth(3, (90, 120)), crs="EPSG:4326", transform=tr)
    rng = np.random.default_rng(1)
    drains = np.c_[-97.8 + rng.uniform(0, 120 / 1800, 30), lat_top - rng.uniform(0, 90 / 3600, 30)]
    check(p, [11, 40], res=9, min_depth=0.05, drains_xy=drains, drain_radius_m=40.0)


def test_south_up_geographic_grid(tmp_path):
    tr = rasterio.Affine(1 / 3600, 0, 10.0, 0, 1 / 3600, 40.0)          # rows run northwards
    p = write_dem(tmp_path / "g.tif", smooth(6, (60, 60)), crs="EPSG:4326", transform=tr)
    check(p, [7, 25], res=10, min_depth=0.05)


def test_feet_projected_grid(tmp_path):
    tr = from_origin(3000000, 10000000, 10.0, 10.0)
    p = write_dem(tmp_path / "f.tif", smooth(9, (60, 60)), crs="EPSG:2278", transform=tr)
    check(p, [7, 25], res=10, min_depth=0.05)


# ------------------------------------------------------------------ parameters and errors

@pytest.mark.parametrize("res", [6, 9, 12])
@pytest.mark.parametrize("min_depth", [0.0, 0.3, 2.0, 50.0])
def test_h3_resolution_and_min_depth(tmp_path, res, min_depth):
    p = write_dem(tmp_path / "d.tif", smooth(13, (60, 70)))
    check(p, [19], res=res, min_depth=min_depth)


def test_invalid_arguments(tmp_path):
    p = write_dem(tmp_path / "d.tif", smooth(0, (20, 20)))
    for bad in (0, -5):
        with pytest.raises(ValueError, match="tile must be positive"):
            analyze_tiled(p, tile=bad)
    wrong = write_dem(tmp_path / "m.tif", np.zeros((10, 10)), nodata=None)
    with pytest.raises(ValueError, match="differ in shape"):
        analyze_tiled(p, tile=8, mask_path=wrong)


def test_cli_tile_option(tmp_path):
    p = write_dem(tmp_path / "d.tif", smooth(0, (60, 60)))
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["analyze", p, "--res", "10", "--min-depth", "0.05", "--out", str(a)]) == 0
    assert main(["analyze", p, "--res", "10", "--min-depth", "0.05", "--tile", "13", "--out", str(b),
                 "--map", str(tmp_path / "m.html")]) == 0
    x, y = pd.read_csv(a / "depressions.csv"), pd.read_csv(b / "depressions.csv")
    assert len(x) == len(y) > 0
    assert sorted(x["max_depth_m"].round(9)) == sorted(y["max_depth_m"].round(9))
    assert (tmp_path / "m.html").exists() and not (b / "_spill").exists()
    assert main(["verify", str(b)]) == 0


def test_ids_number_depressions_in_the_same_scan_order_as_analyze(tmp_path):
    p = write_dem(tmp_path / "d.tif", smooth(1, (60, 60)))
    whole = analyze(p, res=10, min_depth=0.05).depressions
    tiled = analyze_tiled(p, tile=17, res=10, min_depth=0.05).depressions
    assert list(tiled["id"]) == list(range(1, len(tiled) + 1))
    key = ["lat", "lon"]
    both = whole.round({"lat": 7, "lon": 7}).merge(tiled.round({"lat": 7, "lon": 7}), on=key,
                                                   suffixes=("_w", "_t"))
    assert len(both) == len(whole) > 5
    # analyze() ids are raster label values (with gaps), tiled ids are 1..n: same ranking
    assert both.sort_values("id_w")["id_t"].is_monotonic_increasing


# ------------------------------------------------------------------ fuzzing

@pytest.mark.parametrize("seed", range(60))
def test_random_configurations(tmp_path, seed):
    """Random shape, tile, terrain, nodata, quantisation, drains, buildings and parameters."""
    rng = np.random.default_rng(1000 + seed)
    shape = (int(rng.integers(1, 70)), int(rng.integers(1, 70)))
    arr = smooth(seed, shape, sigma=float(rng.uniform(0.5, 6)))
    if rng.random() < 0.4:
        steps = int(rng.integers(2, 12))
        arr = np.round(arr / 10 * steps) / steps * 10
    if rng.random() < 0.6:
        arr = _with_nodata(arr, int(rng.integers(1, 10)), rng)
    kwargs = {"res": int(rng.integers(8, 13)), "min_depth": float(rng.choice([0.0, 0.05, 0.3, 1.0]))}
    if rng.random() < 0.5:
        kwargs["drains_xy"] = _drains(rng, int(rng.integers(1, 40)), shape=shape)
        kwargs["drain_radius_m"] = float(rng.choice([1.0, 3.0, 8.0]))
        kwargs["drain_core_frac"] = float(rng.choice([0.0, 0.5, 0.8, 1.0]))
    if rng.random() < 0.4:
        kwargs["buildings"] = [box(500000 + rng.uniform(0, shape[1]), 3350000 - rng.uniform(0, shape[0]) - 8,
                                   500000 + rng.uniform(0, shape[1]) + 8, 3350000 - rng.uniform(0, shape[0]))
                               for _ in range(int(rng.integers(1, 8)))]
    p = write_dem(tmp_path / "d.tif", arr)
    tile = int(rng.integers(max(1, -(-max(shape) // 10)), max(shape) + 5))
    assert_equivalent(analyze(p, **kwargs), analyze_tiled(p, tile=tile, **kwargs))


# ------------------------------------------------------------------ the building blocks

def test_flood_from_seeds_reproduces_the_plain_fill():
    arr = smooth(0, (40, 50))
    valid = np.ones(arr.shape, dtype=bool)
    rows, cols, pinned, border = tile_seeds(valid, (True, True, True, True))
    assert pinned.all() and border.all()                  # every seed of a whole raster is an outlet
    level, root = flood_from_seeds(arr, valid, rows, cols, arr[rows, cols])
    np.testing.assert_array_equal(level, fill_depressions(arr, valid, np.zeros(arr.shape, bool)))
    assert root.min() == 0 and root.max() == len(rows) - 1


def test_tile_seeds_marks_real_and_artificial_borders():
    valid = np.ones((4, 5), dtype=bool)
    valid[2, 2] = False
    rows, cols, pinned, border = tile_seeds(valid, (True, False, False, False))
    seeds = {(r, c): (p, b) for r, c, p, b in zip(rows, cols, pinned, border)}
    assert seeds[(0, 3)] == (True, True)         # top side is the raster edge: pinned outlet
    assert seeds[(3, 0)] == (False, True)        # bottom side is only a tile cut: not an outlet
    assert seeds[(1, 1)] == (True, False)        # interior, next to the invalid cell
    assert seeds[(3, 3)] == (True, True)         # on the cut but also next to the invalid cell
    assert (2, 2) not in seeds                   # an invalid cell is never a seed
    assert (1, 2) in seeds and (2, 1) in seeds and (2, 3) in seeds   # neighbours of the hole


def test_region_edges_keeps_the_lowest_crossing_per_pair():
    level = np.array([[1.0, 5.0, 2.0], [1.0, 9.0, 2.0]])
    root = np.array([[0, 0, 1], [0, 0, 1]], dtype=np.int32)
    a, b, w = region_edges(level, root)
    assert (a.tolist(), b.tolist()) == ([0], [1])
    assert w.tolist() == [5.0]                    # min over crossings of max(level, level)


def test_region_edges_ignores_invalid_cells():
    level = np.ones((2, 3))
    root = np.array([[0, -1, 1], [0, -1, 1]], dtype=np.int32)
    assert len(region_edges(level, root)[0]) == 0


def test_solve_levels_is_a_minimax_path():
    # outlet 0 (z=1) - (w=4) - node 1 - (w=2) - node 2 ; node 3 also reaches 2 via w=7
    z = np.array([1.0, 0.0, 0.0, 0.0])
    pinned = np.array([True, False, False, False])
    levels = solve_levels(z, pinned, np.array([0, 1, 2]), np.array([1, 2, 3]),
                          np.array([4.0, 2.0, 7.0]))
    assert levels.tolist() == [1.0, 4.0, 4.0, 7.0]


def test_solve_levels_prefers_the_lower_bottleneck():
    z = np.zeros(3)
    pinned = np.array([True, False, True])
    levels = solve_levels(z, pinned, np.array([0, 1]), np.array([1, 2]), np.array([6.0, 3.0]))
    assert levels[1] == 3.0                        # via the second outlet


def test_solve_levels_unreachable_nodes_keep_their_elevation():
    levels = solve_levels(np.array([2.0, 8.0]), np.array([True, False]), np.empty(0, int),
                          np.empty(0, int), np.empty(0))
    assert levels.tolist() == [2.0, 8.0]


def test_neighbour_pairs_finds_all_eight_directions_and_no_row_wraps():
    width, height = 5, 4
    keys = np.array([r * width + c for r, c in [(1, 1), (1, 2), (2, 0), (2, 1), (2, 2), (0, 4), (1, 0)]])
    i, j = neighbour_pairs(keys, width, height)
    pairs = {tuple(sorted((int(keys[a]), int(keys[b])))) for a, b in zip(i, j)}
    cell = lambda r, c: r * width + c   # noqa: E731
    assert (cell(1, 1), cell(1, 2)) in pairs               # right
    assert (cell(1, 1), cell(2, 1)) in pairs               # down
    assert (cell(1, 2), cell(2, 1)) in pairs               # down-left
    assert (cell(1, 1), cell(2, 2)) in pairs               # down-right
    assert (cell(0, 4), cell(1, 0)) not in pairs           # end of one row is not next to the next
    assert (cell(1, 0), cell(2, 0)) in pairs
    assert len(pairs) == len(i)                            # each pair reported once
    assert len(neighbour_pairs(np.empty(0, np.int64), 5, 4)[0]) == 0


# ------------------------------------------------------------------ input formats and odd data

def write_raw(path, arr, dtype, nodata, crs="EPSG:26914", transform=UTM):
    prof = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": dtype, "crs": crs, "transform": transform}
    if nodata is not None:
        prof["nodata"] = nodata
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype(dtype), 1)
    return str(path)


def test_integer_dem_full_of_ties(tmp_path):
    """Whole-metre heights (SRTM-style int16 with -32768 for voids): ties everywhere."""
    arr = np.round(smooth(21, (80, 90), sigma=3.0) * 2).astype("int16")
    arr[30:36, 40:50] = -32768
    p = write_raw(tmp_path / "i.tif", arr, "int16", -32768)
    whole = check(p, [7, 25, 64], res=10, min_depth=0.5)
    assert len(whole.depressions) > 5


def test_nan_cells_without_a_nodata_value(tmp_path):
    arr = smooth(22, (60, 60)).astype("float32")
    arr[10:20, 25:30] = np.nan
    arr[0, 0] = np.nan
    p = write_raw(tmp_path / "n.tif", arr, "float32", None)
    check(p, [9, 31], res=10, min_depth=0.05)


def test_a_nodata_value_that_float32_cannot_hold_exactly(tmp_path):
    arr = smooth(23, (50, 50)).astype("float32")
    arr[5:15, 5:15] = np.float32(-9999.1)               # stored as the nearest float32
    p = write_raw(tmp_path / "x.tif", arr, "float32", -9999.1)
    whole = check(p, [11, 50], res=10, min_depth=0.05)
    assert len(whole.depressions) > 0


def test_float64_dem(tmp_path):
    arr = smooth(24, (50, 60)) + 1e-7 * np.arange(60)    # differences float32 would lose
    p = write_raw(tmp_path / "d64.tif", arr, "float64", -9999.0)
    check(p, [13], res=10, min_depth=1e-9)


def test_geodataframe_buildings(tmp_path):
    gpd = pytest.importorskip("geopandas")
    p = write_dem(tmp_path / "d.tif", smooth(25, (60, 60)))
    rng = np.random.default_rng(4)
    geoms = [box(500000 + x, 3350000 - y - 9, 500000 + x + 9, 3350000 - y)
             for x, y in rng.uniform(0, 50, (15, 2))]
    frame = gpd.GeoDataFrame(geometry=geoms, crs="EPSG:26914")
    check(p, [11, 25], res=10, min_depth=0.05, buildings=frame)


def test_a_dem_without_a_crs_is_rejected(tmp_path):
    prof = {"driver": "GTiff", "height": 10, "width": 10, "count": 1, "dtype": "float32",
            "transform": UTM}
    p = tmp_path / "nocrs.tif"
    with rasterio.open(p, "w", **prof) as dst:
        dst.write(np.zeros((10, 10), dtype="float32"), 1)
    with pytest.raises(ValueError, match="coordinate reference system"):
        analyze_tiled(str(p), tile=4)


def test_rollup_of_a_tiled_result_equals_the_wholes(tmp_path):
    p = write_dem(tmp_path / "d.tif", smooth(26, (70, 70)))
    whole = analyze(p, res=12, min_depth=0.05).at_resolution(9).sort_values("h3")
    tiled = analyze_tiled(p, tile=19, res=12, min_depth=0.05).at_resolution(9).sort_values("h3")
    assert list(whole["h3"]) == list(tiled["h3"])
    np.testing.assert_allclose(whole["depress_volume_m3"], tiled["depress_volume_m3"], rtol=1e-9)


def test_tiled_result_can_be_saved_and_drawn(tmp_path):
    pytest.importorskip("pydeck")
    p = write_dem(tmp_path / "d.tif", smooth(27, (60, 60)))
    res = analyze_tiled(p, tile=17, res=11, min_depth=0.05)
    out = res.save(tmp_path / "out")
    assert (out / "depressions.csv").exists() and (out / "meta.json").exists()
    assert res.meta["tiling"]["n_tiles"] == 16 and res.meta["tiling"]["tile"] == 17
    assert "H3HexagonLayer" in res.to_deck().to_json()


def test_verbose_reports_progress(tmp_path, capsys):
    p = write_dem(tmp_path / "d.tif", smooth(28, (30, 30)))
    analyze_tiled(p, tile=16, res=10, min_depth=0.05, verbose=True)
    out = capsys.readouterr().out
    assert "pass 1, tile 1/4" in out and "pass 2, tile 4/4" in out


def test_a_large_flat_plateau_with_one_deep_hole_spanning_many_tiles(tmp_path):
    arr = np.full((90, 90), 50.0)
    arr[5:85, 5:85] = 40.0                              # a huge basin whose rim is at 50
    arr[40:50, 40:50] = 30.0                            # deep hole at the centre
    arr[0, 45] = 45.0                                   # a low notch in the border: the basin drains to 45
    arr[1:5, 45] = 45.0
    p = write_dem(tmp_path / "d.tif", arr)
    whole = check(p, [9, 30, 31, 90], res=9, min_depth=0.5)
    assert whole.depressions["max_depth_m"].max() == pytest.approx(15.0)    # filled to 45, floor at 30


def test_a_lattice_of_one_cell_pits(tmp_path):
    arr = np.full((41, 41), 11.0)
    arr[::2, ::2] = 10.0                               # isolated pits two cells apart
    p = write_dem(tmp_path / "d.tif", arr)
    whole = check(p, [1, 7, 40, 41], res=10, min_depth=0.5)
    assert len(whole.depressions) == 19 * 19           # the interior pits; border ones are outlets


def test_a_checkerboard_drains_through_its_diagonals(tmp_path):
    y, x = np.mgrid[0:40, 0:40]
    p = write_dem(tmp_path / "d.tif", 10.0 + ((x + y) % 2) * 1.0)
    # low cells touch diagonally, so with 8-neighbour flow they all reach the border: no depressions
    assert len(check(p, [1, 7, 40], res=10, min_depth=0.5).depressions) == 0


# ------------------------------------------------------------------ the array aggregation helpers
# The tiled run sums cells with sorted plain arrays instead of pandas groupby, to keep memory down.
# pandas groupby (what it used before) is the oracle.

class _FullCoverage:
    """Stands in for a DEM footprint: every cell fully covered."""

    def coverage(self, cells):
        n = len(cells)
        return pd.DataFrame({"coverage": np.ones(n), "covered_area_m2": np.full(n, 100.0)})


def _random_parts(seed, store, n_tiles=4, n=500):
    """Random per-tile sums on real resolution-10 cells spread over many partitions."""
    rng = np.random.default_rng(seed)
    pool = T.h3_ids(rng.uniform(29, 31, 60), rng.uniform(-99, -97, 60), 10)
    frames = []
    for _ in range(n_tiles):
        rec = rng.integers(0, 40, n)
        cell = pool[rng.integers(0, len(pool), n)]
        d, a, v = rng.random(n), rng.random(n) * 5, rng.random(n) * 3
        store.add(*T._cell_summary(rec, cell, d, a, v))
        frames.append(pd.DataFrame({"rec": rec, "cell": cell, "d": d, "a": a, "v": v}))
    return pd.concat(frames, ignore_index=True)


@pytest.mark.parametrize("seed", range(5))
def test_cell_summary_matches_a_groupby(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 300))
    rec, cell = rng.integers(0, 6, n), rng.integers(0, 6, n).astype(np.uint64)
    d, a, v = rng.random(n), rng.random(n), rng.random(n)
    got = pd.DataFrame(dict(zip(["rec", "cell", "d", "a", "v"], T._cell_summary(rec, cell, d, a, v))))
    want = (pd.DataFrame({"rec": rec, "cell": cell, "d": d, "a": a, "v": v})
            .groupby(["rec", "cell"]).agg(d=("d", "max"), a=("a", "sum"), v=("v", "sum")).reset_index())
    pd.testing.assert_frame_equal(got, want, check_dtype=False, rtol=1e-12)


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize("rows", [1, 25, 10**6])
def test_cell_chunks_match_a_groupby(tmp_path, seed, on_disk, rows):
    store = CellParts(10, tmp_path / "spill" if on_disk else None)
    raw = _random_parts(seed, store)
    assert len(store.keys()) > 3                 # the cells really span several partitions
    rng = np.random.default_rng(seed + 100)
    comp = rng.integers(0, 12, 40)              # pieces joined into 12 depressions
    kept = rng.random(12) < 0.6
    lowpoints = raw["cell"].to_numpy()[rng.integers(0, len(raw), 30)]
    chunks = list(T._cell_chunks(store, comp, kept, lowpoints, _FullCoverage(), rows=rows))
    got = pd.concat(chunks, ignore_index=True)
    if rows == 1:                                # memory stays bounded: one piece per partition
        assert len(chunks) == len({int(k) for k in partition_key(S_ids(got["h3"]), 10)})
    if rows == 10**6:
        assert len(chunks) == 1
    store.close()
    raw = raw[kept[comp[raw["rec"].to_numpy()]]]
    want = raw.groupby("cell").agg(max_depth_m=("d", "max"), depress_area_m2=("a", "sum"),
                                   depress_volume_m3=("v", "sum")).reset_index()
    counts = pd.Series(lowpoints).value_counts()
    want["n_lowpoints"] = want["cell"].map(counts).fillna(0).astype("int64")
    want.insert(0, "h3", [h3.int_to_str(int(c)) for c in want.pop("cell")])
    pd.testing.assert_frame_equal(got[want.columns], want, check_dtype=False, rtol=1e-12)
    assert list(got.columns) == CELL_TABLE
    assert not (tmp_path / "spill").exists()     # the spill folder is removed


def test_cell_chunks_when_nothing_is_kept():
    store = CellParts(10)
    _random_parts(0, store)
    got = list(T._cell_chunks(store, np.zeros(40, dtype=np.int64), np.zeros(1, dtype=bool),
                              np.zeros(0, dtype=np.uint64), _FullCoverage()))
    assert got == []


def test_partition_keys_sort_like_the_cells_they_hold():
    rng = np.random.default_rng(5)
    for res in (0, 3, 6, 9, 12, 15):
        cells = np.sort(T.h3_ids(rng.uniform(-80, 80, 3000), rng.uniform(-180, 180, 3000), res))
        key = partition_key(cells, res)
        assert (key[1:] >= key[:-1]).all()
        parent = max(0, res - PART_DEPTH)
        want = [h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(c)), parent)) for c in cells]
        # cells with the same key have the same parent, and different keys mean different parents
        same = key[1:] == key[:-1]
        np.testing.assert_array_equal(same, np.array(want[1:]) == np.array(want[:-1]))


def test_runs_marks_every_change_of_any_key():
    a, b = np.array([0, 0, 0, 1, 1, 2]), np.array([5, 5, 6, 6, 6, 6])
    np.testing.assert_array_equal(T._runs(a), [0, 3, 5])
    np.testing.assert_array_equal(T._runs(a, b), [0, 2, 3, 5])


def test_tiled_reads_with_a_capped_gdal_cache(tmp_path, monkeypatch):
    seen = []
    real = T._read_tile

    def spy(*a, **k):
        seen.append(rasterio.env.getenv().get("GDAL_CACHEMAX"))
        return real(*a, **k)
    monkeypatch.setattr(T, "_read_tile", spy)
    rng = np.random.default_rng(3)
    p = write_dem(tmp_path / "d.tif", ndimage.gaussian_filter(rng.random((60, 70)) * 30, 2))
    analyze_tiled(p, tile=25, res=12)
    assert seen and set(seen) == {GDAL_CACHE_MB}
