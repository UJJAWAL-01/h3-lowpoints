"""Coverage: the share of each H3 cell that the DEM covers.

The oracle assigns every pixel (and a ring of virtual pixels beyond the raster) to its H3 cell,
which is slow but obviously right; the fast path must agree with it cell by cell.
"""
import h3
import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from h3lowpoints import analyze, analyze_array, analyze_tiled
from h3lowpoints import coverage as cov
from h3lowpoints.coverage import BLOCK, Footprint, mark_invalid_blocks
from h3lowpoints.geometry import pixel_areas_m2
from h3lowpoints.h3agg.aggregate import h3_ids, lonlat

UTM = CRS.from_epsg(26914)
WGS84 = CRS.from_epsg(4326)


def ring_width(transform, crs, res, shape):
    """Rows and columns of virtual pixels that hold any cell that touches the raster whole."""
    geographic = crs.is_geographic
    lat = transform.f + transform.e * shape[0] / 2
    if geographic:
        dy, dx = abs(transform.e) * 111_132.0, abs(transform.a) * 111_320.0 * np.cos(np.radians(lat))
    else:
        unit = crs.linear_units_factor[1]
        dy, dx = abs(transform.e) * unit, abs(transform.a) * unit
    diameter = 2 * h3.average_hexagon_edge_length(res, unit="m")
    return int(np.ceil(2.5 * diameter / dy)) + 3, int(np.ceil(2.5 * diameter / dx)) + 3


def brute_coverage(invalid, transform, crs, res, pad=None):
    """{cell: coverage} for every cell with a pixel position in the padded grid."""
    h, w = invalid.shape
    pad_r, pad_c = pad if isinstance(pad, tuple) else ring_width(transform, crs, res, invalid.shape)
    rows, cols = np.arange(-pad_r, h + pad_r), np.arange(-pad_c, w + pad_c)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    x, y = transform @ (cc + 0.5, rr + 0.5)
    lon, lat = lonlat(crs, np.asarray(x), np.asarray(y))
    ids = h3_ids(lat.ravel(), lon.ravel(), res).reshape(rr.shape)
    area = pixel_areas_m2(transform @ rasterio.Affine.translation(0, -pad_r), crs, h + 2 * pad_r)[:, None]
    area = np.broadcast_to(area, rr.shape)
    valid = np.zeros(rr.shape, dtype=bool)
    valid[pad_r:pad_r + h, pad_c:pad_c + w] = ~invalid
    out = {}
    for cell in np.unique(ids):
        m = ids == cell
        out[h3.int_to_str(int(cell))] = float(area[m & valid].sum() / area[m].sum())
    return out


def blobs(shape, n, seed):
    rng = np.random.default_rng(seed)
    inv = np.zeros(shape, dtype=bool)
    for _ in range(n):
        r, c = rng.integers(0, shape[0]), rng.integers(0, shape[1])
        inv[r:r + rng.integers(2, 25), c:c + rng.integers(2, 25)] = True
    return inv


def check_against_brute_force(invalid, transform, crs, res, tol=1e-12):
    want = brute_coverage(invalid, transform, crs, res)
    cells = sorted(want)
    got = Footprint.from_array(invalid, transform, crs).coverage(cells)["coverage"].to_numpy()
    np.testing.assert_allclose(got, [want[c] for c in cells], rtol=0, atol=tol)
    return want


# ------------------------------------------------------------------ against the oracle

@pytest.mark.parametrize("seed", range(4))
def test_projected_grid_with_nodata_blobs(seed):
    invalid = blobs((110, 140), 6, seed)
    want = check_against_brute_force(invalid, from_origin(500000, 3350000, 1, 1), UTM, 12)
    assert min(want.values()) < 0.5 and max(want.values()) == 1.0     # partial and full cells both occur


def test_a_fully_valid_raster_is_partial_only_at_its_edge():
    invalid = np.zeros((120, 150), dtype=bool)
    want = check_against_brute_force(invalid, from_origin(500000, 3350000, 1, 1), UTM, 12)
    full = [c for c, v in want.items() if v == 1.0]
    partial = [c for c, v in want.items() if 0 < v < 1]
    outside = [c for c, v in want.items() if v == 0.0]          # cells wholly in the virtual ring
    assert full and partial and outside
    assert len(full) > 10                                       # most of the interior is fully covered


@pytest.mark.parametrize("lat_top", [0.5, 30.3, 60.0, 82.0, -45.0])
def test_geographic_grids_at_many_latitudes(lat_top):
    px = 1 / 3600
    invalid = blobs((90, 120), 4, int(abs(lat_top)))
    check_against_brute_force(invalid, from_origin(-97.8, lat_top, px, px), WGS84, 11, tol=1e-9)


@pytest.mark.parametrize("lon_left", [179.97, -180.0, 179.0])
def test_near_the_antimeridian(lon_left):
    px = 1 / 3600
    invalid = blobs((80, 100), 3, 7)
    check_against_brute_force(invalid, from_origin(lon_left, 40.0, px, px), WGS84, 11, tol=1e-9)


def test_near_the_pole():
    px = 1 / 1800
    invalid = blobs((80, 100), 3, 8)
    check_against_brute_force(invalid, from_origin(10.0, 88.0, px, px), WGS84, 11, tol=1e-9)


def test_feet_projected_grid():
    invalid = blobs((90, 110), 4, 3)
    check_against_brute_force(invalid, from_origin(3000000, 10000000, 10.0, 10.0),
                              CRS.from_epsg(2278), 12)


def test_non_square_pixels():
    invalid = blobs((90, 110), 4, 5)
    check_against_brute_force(invalid, from_origin(500000, 3350000, 1.0, 2.5), UTM, 11)


def test_sampling_big_cells_stays_within_a_few_percent(monkeypatch):
    monkeypatch.setattr(cov, "SAMPLE_LIMIT", 300)          # force the sub-lattice on every partial cell
    invalid = blobs((150, 170), 6, 2)
    tr = from_origin(500000, 3350000, 1, 1)
    want = brute_coverage(invalid, tr, UTM, 11)
    cells = sorted(want)
    got = Footprint.from_array(invalid, tr, UTM).coverage(cells)["coverage"].to_numpy()
    assert np.abs(got - [want[c] for c in cells]).max() < 0.06
    assert np.abs(got - [want[c] for c in cells]).mean() < 0.01


# ------------------------------------------------------------------ the block bitmap

@pytest.mark.parametrize("origin", [(0, 0), (5, 9), (16, 16), (31, 47), (1, 0)])
def test_block_bitmap_matches_a_direct_scan(origin):
    rng = np.random.default_rng(1)
    full = rng.random((200, 220)) < 0.002                 # sparse invalid pixels
    r0, c0 = origin
    window = full[r0:r0 + 77, c0:c0 + 93]
    blocks = np.zeros((-(-200 // BLOCK), -(-220 // BLOCK)), dtype=bool)
    mark_invalid_blocks(blocks, window, origin)
    want = np.zeros_like(blocks)
    for br in range(blocks.shape[0]):
        for bc in range(blocks.shape[1]):
            rs, cs = slice(br * BLOCK, (br + 1) * BLOCK), slice(bc * BLOCK, (bc + 1) * BLOCK)
            sub = np.zeros_like(full)
            sub[r0:r0 + 77, c0:c0 + 93] = window
            want[br, bc] = sub[rs, cs].any()
    np.testing.assert_array_equal(blocks, want)


def test_blocks_from_several_tiles_equal_blocks_from_the_whole():
    rng = np.random.default_rng(2)
    full = rng.random((130, 150)) < 0.003
    whole = np.zeros((-(-130 // BLOCK), -(-150 // BLOCK)), dtype=bool)
    mark_invalid_blocks(whole, full, (0, 0))
    tiled = np.zeros_like(whole)
    for r0 in range(0, 130, 37):
        for c0 in range(0, 150, 41):
            mark_invalid_blocks(tiled, full[r0:r0 + 37, c0:c0 + 41], (r0, c0))
    np.testing.assert_array_equal(whole, tiled)


# ------------------------------------------------------------------ in the results

def _dem(path, invalid, depth=True):
    arr = np.full(invalid.shape, 10.0, dtype="float32")
    if depth:
        for r in range(8, invalid.shape[0] - 8, 20):
            for c in range(8, invalid.shape[1] - 8, 20):
                arr[r:r + 6, c:c + 6] = 8.0
    arr[invalid] = -9999.0
    prof = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": "float32", "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1),
            "nodata": -9999.0}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)
    return str(path)


def test_cells_table_carries_coverage(tmp_path):
    invalid = blobs((100, 120), 5, 4)
    res = analyze(_dem(tmp_path / "d.tif", invalid), res=12, min_depth=0.5)
    cells = res.cells
    assert {"covered_area_m2", "coverage"} <= set(cells.columns)
    assert ((cells["coverage"] > 0) & (cells["coverage"] <= 1)).all()
    nominal = np.array([h3.cell_area(c, unit="m^2") for c in cells["h3"]])
    np.testing.assert_allclose(cells["covered_area_m2"], cells["coverage"] * nominal)
    assert (cells["coverage"] < 1).any() and (cells["coverage"] == 1).any()


def test_coverage_in_a_result_matches_the_oracle(tmp_path):
    invalid = blobs((100, 120), 5, 6)
    res = analyze(_dem(tmp_path / "d.tif", invalid), res=12, min_depth=0.5)
    want = brute_coverage(invalid, from_origin(500000, 3350000, 1, 1), UTM, 12)
    got = dict(zip(res.cells["h3"], res.cells["coverage"]))
    np.testing.assert_allclose([got[c] for c in got], [want[c] for c in got], atol=1e-12)


@pytest.mark.parametrize("tile", [17, 40, 500])
def test_tiled_coverage_equals_whole(tmp_path, tile):
    invalid = blobs((100, 120), 5, 9)
    p = _dem(tmp_path / "d.tif", invalid)
    whole = analyze(p, res=12, min_depth=0.5).cells.sort_values("h3").reset_index(drop=True)
    tiled = analyze_tiled(p, tile=tile, res=12, min_depth=0.5).cells.sort_values("h3").reset_index(drop=True)
    assert list(whole.columns) == list(tiled.columns)
    np.testing.assert_allclose(whole["coverage"], tiled["coverage"], rtol=0, atol=1e-12)
    np.testing.assert_allclose(whole["covered_area_m2"], tiled["covered_area_m2"], rtol=1e-12)


def test_at_resolution_measures_coverage_afresh(tmp_path):
    invalid = blobs((100, 120), 5, 11)
    res = analyze(_dem(tmp_path / "d.tif", invalid), res=12, min_depth=0.5)
    coarse = res.at_resolution(10)
    assert {"coverage", "covered_area_m2"} <= set(coarse.columns)
    want = brute_coverage(invalid, from_origin(500000, 3350000, 1, 1), UTM, 10)
    for cell, c in zip(coarse["h3"], coarse["coverage"]):
        assert c == pytest.approx(want[cell], abs=1e-12)
    # partial coverage is why the total of a coarse table is not the sum of covered fine cells
    assert (coarse["coverage"] < 1).any()


def test_tiled_result_can_roll_up_with_coverage(tmp_path):
    invalid = blobs((100, 120), 5, 12)
    p = _dem(tmp_path / "d.tif", invalid)
    a = analyze(p, res=12, min_depth=0.5).at_resolution(10).sort_values("h3").reset_index(drop=True)
    b = analyze_tiled(p, tile=33, res=12, min_depth=0.5).at_resolution(10).sort_values("h3").reset_index(drop=True)
    np.testing.assert_allclose(a["coverage"], b["coverage"], atol=1e-12)


def test_a_mask_reduces_coverage(tmp_path):
    p = _dem(tmp_path / "d.tif", np.zeros((100, 120), dtype=bool))
    mask = np.zeros((100, 120), dtype=bool)
    mask[:, 60:] = True                                  # the right half is ignored (say, a lake)
    whole = analyze(p, res=12, min_depth=0.5, mask=mask).cells
    assert (whole["coverage"] < 1).any()
    mask_tif = tmp_path / "m.tif"
    prof = {"driver": "GTiff", "height": 100, "width": 120, "count": 1, "dtype": "uint8",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1)}
    with rasterio.open(mask_tif, "w", **prof) as dst:
        dst.write(mask.astype("uint8"), 1)
    tiled = analyze_tiled(p, tile=30, res=12, min_depth=0.5, mask_path=str(mask_tif)).cells
    a, b = whole.sort_values("h3"), tiled.sort_values("h3")
    np.testing.assert_allclose(a["coverage"], b["coverage"], atol=1e-12)


def test_no_depressions_still_returns_a_footprint(tmp_path):
    p = _dem(tmp_path / "d.tif", np.zeros((40, 40), dtype=bool), depth=False)
    res = analyze(p, res=12)
    assert len(res.cells) == 0 and res.footprint is not None
    assert len(res.at_resolution(10)) == 0


def test_empty_cell_list():
    fp = Footprint.from_array(np.zeros((20, 20), dtype=bool), from_origin(0, 0, 1, 1), UTM)
    assert len(fp.coverage([])) == 0


def test_analyze_array_exposes_the_footprint():
    arr = np.full((60, 60), 10.0)
    arr[20:30, 20:30] = 8.0
    arr[:, 50:] = np.nan
    res = analyze_array(arr, from_origin(500000, 3350000, 1, 1), UTM, res=12, min_depth=0.5)
    assert res.footprint.shape == (60, 60) and res.footprint.blocks.any()


# ------------------------------------------------------------------ the fast paths
# The summed-area table and chunking keep memory flat for millions of cells; they must not change
# a single answer.

def _direct_partial(fp, box):
    r0, r1, c0, c1 = (int(v) for v in box)
    h, w = fp.shape
    if r0 < 0 or c0 < 0 or r1 > h or c1 > w:
        return True
    return bool(fp.blocks[r0 // BLOCK:(r1 - 1) // BLOCK + 1, c0 // BLOCK:(c1 - 1) // BLOCK + 1].any())


@pytest.mark.parametrize("seed", range(4))
def test_partial_mask_matches_a_direct_scan(seed):
    rng = np.random.default_rng(seed)
    shape = (int(rng.integers(20, 200)), int(rng.integers(20, 200)))
    invalid = rng.random(shape) < 0.002
    fp = Footprint.from_array(invalid, from_origin(500000, 3350000, 1, 1), UTM)
    r0 = rng.integers(-10, shape[0], 2000)
    c0 = rng.integers(-10, shape[1], 2000)
    boxes = np.column_stack([r0, r0 + rng.integers(1, 60, 2000), c0, c0 + rng.integers(1, 60, 2000)])
    got = fp._partial_mask(boxes)
    want = [_direct_partial(fp, b) for b in boxes]
    np.testing.assert_array_equal(got, want)
    assert got.any() and not got.all()


@pytest.mark.parametrize("chunk", [1, 7, 50])
def test_chunked_coverage_equals_one_pass(chunk):
    invalid = blobs((150, 170), 6, 3)
    tr = from_origin(500000, 3350000, 1, 1)
    cells = sorted(brute_coverage(invalid, tr, UTM, 12))
    fp = Footprint.from_array(invalid, tr, UTM)
    whole = fp.coverage(cells, chunk=len(cells))
    parts = fp.coverage(cells, chunk=chunk)
    assert (whole["coverage"] < 1).any()
    np.testing.assert_array_equal(whole.to_numpy(), parts.to_numpy())


def test_the_dem_is_not_opened_when_no_cell_is_partial():
    opened = []
    invalid = np.zeros((300, 300), dtype=bool)
    tr = from_origin(500000, 3350000, 1, 1)
    fp = Footprint.from_array(invalid, tr, UTM)
    real = fp._opener
    fp._opener = lambda: (opened.append(1), real())[1]
    def cell_at(x, y):
        lon, lat = lonlat(UTM, np.array([x]), np.array([y]))
        return h3.latlng_to_cell(float(lat[0]), float(lon[0]), 12)
    assert fp.coverage([cell_at(500150.0, 3349850.0)])["coverage"].iloc[0] == 1.0 and not opened
    fp.coverage([cell_at(500000.5, 3349999.5)])               # a corner cell leaves the raster
    assert opened
