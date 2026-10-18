import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze
from h3lowpoints.resolution import (check_resolution, metres_to_cells, finest_h3_resolution,
                                    resolution_tier)


def _write(path, arr, cell):
    prof = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": "float32", "crs": "EPSG:26914",
            "transform": from_origin(500000, 3350000, cell, cell), "nodata": -9999.0}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)


def _pit_dem(path, cell, n=60):
    arr = np.full((n, n), 100.0, dtype="float32")
    arr[20:35, 20:35] = 95.0
    _write(path, arr, cell)


def test_tiers():
    sizes = (1, 2, 5, 12, 30)
    assert [resolution_tier(s) for s in sizes] == ["fine", "fine", "medium", "medium", "coarse"]


def test_metres_to_cells_is_resolution_independent():
    assert metres_to_cells(5.0, 1.0) == 5
    assert metres_to_cells(5.0, 10.0) == 1   # never less than one cell
    assert metres_to_cells(5.0, 0.5) == 10


def test_finest_h3_resolution_gets_coarser_with_cell_size():
    assert finest_h3_resolution(1.0) > finest_h3_resolution(10.0) > finest_h3_resolution(30.0)
    assert finest_h3_resolution(1.0) >= 11   # res 11 cells hold thousands of 1 m pixels


def test_fine_dem_gives_no_warning(recwarn):
    check_resolution(1.0, 11, 0.15)
    assert not recwarn.list


def test_h3_resolution_too_fine_for_dem_warns():
    with pytest.warns(UserWarning, match="fewer than 4 pixels"):
        check_resolution(30.0, 13, 5.0)


def test_coarse_dem_warns_about_vertical_error():
    with pytest.warns(UserWarning, match="vertical error"):
        check_resolution(30.0, 8, 0.15)


def test_tier_is_recorded_in_meta(tmp_path):
    fine, coarse = tmp_path / "fine.tif", tmp_path / "coarse.tif"
    _pit_dem(fine, 1.0)
    _pit_dem(coarse, 10.0)
    assert analyze(str(fine)).meta["resolution_tier"] == "fine"
    assert analyze(str(coarse), res=9, min_depth=1.0).meta["resolution_tier"] == "medium"


@pytest.mark.parametrize("cell", [0.5, 1.0])
def test_drain_radius_is_in_metres(tmp_path, cell):
    # The same distances must give the same answer at 0.5 m and at 1 m cells.
    n = int(60 / cell)
    arr = np.full((n, n), 10.0, dtype="float32")
    lo, hi = int(25 / cell), int(35 / cell)
    arr[lo:hi, lo:hi] = 9.0                       # the pit spans x = 25..35 m
    p = tmp_path / "d.tif"
    _write(p, arr, cell)
    near = np.array([[500000 + 35 + 4.0, 3350000 - 30]])   # 4 m beyond the pit's east edge
    far = np.array([[500000 + 35 + 8.0, 3350000 - 30]])    # 8 m beyond it
    assert analyze(str(p), drains_xy=near, drain_radius_m=5.0, drain_core_frac=0.0).depressions["drain_at_core"].all()
    assert not analyze(str(p), drains_xy=far, drain_radius_m=5.0, drain_core_frac=0.0).depressions["drain_at_core"].any()


# ------------------------------------------------------------------ res="auto" and the finest cells

@pytest.mark.parametrize("size_m, want", [(0.5, 14), (1.0, 14), (2.0, 13), (10.0, 11), (30.0, 10),
                                          (90.0, 9), (1000.0, 7)])
def test_auto_picks_the_finest_resolution_with_four_pixels_per_cell(size_m, want):
    import h3

    from h3lowpoints.resolution import MIN_PIXELS_PER_H3_CELL, resolve_resolution

    got = resolve_resolution("auto", size_m)
    assert got == want
    need = MIN_PIXELS_PER_H3_CELL * size_m ** 2
    assert h3.average_hexagon_area(got, unit="m^2") >= need                     # enough pixels here ...
    if got < 15:
        assert h3.average_hexagon_area(got + 1, unit="m^2") < need              # ... but not one finer


@pytest.mark.parametrize("value, want", [("auto", 14), (" AUTO ", 14), ("12", 12), (0, 0), (15, 15),
                                         (np.int64(9), 9)])
def test_resolution_values_that_are_accepted(value, want):
    from h3lowpoints.resolution import resolve_resolution

    assert resolve_resolution(value, 1.0) == want


@pytest.mark.parametrize("value", [16, -1, 11.5, True, "x", "", None, "12.0"])
def test_resolution_values_that_are_refused(value):
    from h3lowpoints.resolution import resolve_resolution

    with pytest.raises(ValueError, match="integer from 0 to 15"):
        resolve_resolution(value, 1.0)


def test_analyze_defaults_to_the_finest_resolution(tmp_path):
    import h3

    p = tmp_path / "fine.tif"
    _pit_dem(p, 1.0)
    res = analyze(str(p))
    assert res.res == 14 and res.meta["res_requested"] == "auto"
    assert {h3.get_resolution(c) for c in res.cells["h3"]} == {14}
    assert h3.get_resolution(res.depressions["h3_lowpoint"].iloc[0]) == 14


def test_auto_on_a_30_m_dem_needs_no_warning_about_resolution(tmp_path, recwarn):
    p = tmp_path / "coarse.tif"
    _pit_dem(p, 30.0)
    res = analyze(str(p), min_depth=1.0)
    assert res.res == 10
    assert not [w for w in recwarn.list if "fewer than" in str(w.message)]


def test_an_explicit_resolution_still_wins(tmp_path):
    p = tmp_path / "fine.tif"
    _pit_dem(p, 1.0)
    res = analyze(str(p), res=9)
    assert res.res == 9 and res.meta["res_requested"] == 9


def test_lowest_point_carries_the_finest_h3_cell(tmp_path):
    import h3

    p = tmp_path / "fine.tif"
    _pit_dem(p, 1.0)
    for r in ("auto", 8):
        dep = analyze(str(p), res=r).depressions
        for _, d in dep.iterrows():
            assert h3.get_resolution(d["h3_lowpoint_15"]) == 15
            assert d["h3_lowpoint_15"] == h3.latlng_to_cell(d["lat"], d["lon"], 15)
            assert d["h3_lowpoint"] == h3.latlng_to_cell(d["lat"], d["lon"], h3.get_resolution(d["h3_lowpoint"]))


def _many_pits(path, cell=1.0, n=120):
    rng = np.random.default_rng(3)
    arr = np.full((n, n), 50.0, dtype="float32")
    for _ in range(40):
        r, c = rng.integers(2, n - 8, 2)
        arr[r:r + rng.integers(1, 6), c:c + rng.integers(1, 6)] -= rng.uniform(0.3, 3)
    _write(path, arr, cell)


def test_lowest_point_counts_add_up_at_every_resolution(tmp_path):
    p = tmp_path / "pits.tif"
    _many_pits(p)
    res = analyze(str(p), res=13, min_depth=0.2)
    n = len(res.depressions)
    assert n > 10 and res.cells["n_lowpoints"].sum() == n
    # every lowest point is in a listed cell, and the count there matches
    counts = res.depressions["h3_lowpoint"].value_counts()
    listed = res.cells.set_index("h3")["n_lowpoints"]
    assert (listed.reindex(counts.index) == counts).all()
    for r in range(12, 5, -1):
        assert res.at_resolution(r)["n_lowpoints"].sum() == n


def test_depressed_share_is_area_over_covered_area(tmp_path):
    p = tmp_path / "pits.tif"
    _many_pits(p)
    cells = analyze(str(p), res=12, min_depth=0.2).cells
    want = np.minimum(1, cells["depress_area_m2"] / cells["covered_area_m2"])
    np.testing.assert_allclose(cells["depressed_share"], want)
    assert ((cells["depressed_share"] > 0) & (cells["depressed_share"] <= 1)).all()


def test_cell_table_columns_are_fixed_even_when_empty(tmp_path):
    from h3lowpoints.pipeline import CELL_TABLE

    flat = tmp_path / "flat.tif"
    _write(flat, np.full((30, 30), 5.0, dtype="float32"), 1.0)
    p = tmp_path / "pits.tif"
    _many_pits(p)
    assert list(analyze(str(flat)).cells.columns) == CELL_TABLE
    assert list(analyze(str(p), min_depth=0.2).cells.columns) == CELL_TABLE


def test_tiled_runs_resolve_auto_the_same_way(tmp_path):
    from h3lowpoints import analyze_tiled

    p = tmp_path / "pits.tif"
    _many_pits(p)
    whole = analyze(str(p), min_depth=0.2)
    tiled = analyze_tiled(str(p), tile=37, min_depth=0.2)
    assert whole.res == tiled.res == 14
    a = whole.depressions.sort_values("h3_lowpoint_15").reset_index(drop=True)
    b = tiled.depressions.sort_values("h3_lowpoint_15").reset_index(drop=True)
    assert list(a["h3_lowpoint_15"]) == list(b["h3_lowpoint_15"])
    assert whole.cells["n_lowpoints"].sum() == tiled.cells["n_lowpoints"].sum() == len(a)


# ------------------------------------------------------------------ the resolution guide

def test_resolution_guide_for_a_dem(tmp_path):
    from h3lowpoints import resolution_guide

    p = tmp_path / "fine.tif"
    _pit_dem(p, 1.0)
    g = resolution_guide(str(p))
    assert g["finest"] == 14 and g["tier"] == "fine" and g["summary_only"] == [0, 1, 2, 3]
    uses = {lv["res"]: lv["use"] for lv in g["levels"]}
    assert uses[0] == uses[3] == "summary only"
    assert uses[4] == uses[14] == "supported" and uses[15] == "too fine for this DEM"
    px = [lv["pixels_per_cell"] for lv in g["levels"]]
    assert px == sorted(px, reverse=True) and px[14] >= 4 > px[15]


def test_cli_accepts_auto_and_numbers(tmp_path, capsys):
    from h3lowpoints.cli import main

    p = tmp_path / "fine.tif"
    _pit_dem(p, 1.0)
    assert main(["analyze", str(p), "--out", str(tmp_path / "a")]) == 0
    assert "H3 resolution 14" in capsys.readouterr().out
    assert main(["analyze", str(p), "--res", "9", "--out", str(tmp_path / "b")]) == 0
    assert "H3 resolution 9" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="integer from 0 to 15"):
        main(["analyze", str(p), "--res", "fine", "--out", str(tmp_path / "c")])
