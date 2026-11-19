"""Seen wet: the JRC reader's grid and the water-history rules, on synthetic data with known answers.

No network: JRC tiles are written locally (with a small tile size) and read through `source=`.
"""
import numpy as np
import pandas as pd
import pytest
import rasterio

from h3lowpoints import water
from h3lowpoints.sources import jrc

YEARS = jrc.YEARS
Y = len(YEARS)


# ---------------------------------------------------------------- reader: grid and file names

def test_grid_window_snaps_to_the_global_grid():
    row0, col0, rows, cols = jrc.grid_window((-95.2, 29.9, -95.05, 30.05))
    assert (row0, col0) == ((80 - 30.05) / 0.00025, (180 - 95.2) / 0.00025)
    assert (rows, cols) == (600, 600)
    t = jrc.grid_transform(row0, col0)
    assert t.c == pytest.approx(-95.2) and t.f == pytest.approx(30.05)


def test_grid_window_clips_to_the_data_and_refuses_boxes_outside():
    row0, _, rows, _ = jrc.grid_window((10, 79, 11, 85))
    assert row0 == 0 and rows == 4000
    with pytest.raises(ValueError, match="outside the data"):
        jrc.grid_window((10, -70, 11, -65))


def test_file_names_match_the_server():
    assert jrc.file_name("occurrence", 200000, 320000) == "occurrence-0000200000-0000320000.tif"
    assert (jrc.file_name("yearly", 0, 40000, year=1985)
            == "yearlyClassification1985-0000000000-0000040000.tif")
    assert jrc.file_name("monthly_recurrence", 0, 0, month=7) == "0000000000-0000000000.tif"
    assert jrc.tile_url("monthly_recurrence", 0, 0, month=7).endswith(
        "MonthlyRecurrence/LATEST/tiles/monthlyRecurrence7/0000000000-0000000000.tif")
    with pytest.raises(ValueError):
        jrc.file_name("yearly", 0, 0, year=2030)


def test_read_window_mosaics_tiles_and_leaves_missing_tiles_at_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(jrc, "TILE", 100)
    for (r, c), value in {(0, 0): 1, (0, 100): 2, (100, 0): 3}.items():   # (100, 100) missing
        arr = np.full((100, 100), value, dtype="uint8")
        arr[0, 0] = 9
        with rasterio.open(tmp_path / jrc.file_name("extent", r, c), "w", driver="GTiff",
                           height=100, width=100, count=1, dtype="uint8") as dst:
            dst.write(arr, 1)
    out = jrc.read_window("extent", 90, 90, 20, 20, source=tmp_path)
    assert (out[:10, :10] == 1).all() and (out[:10, 10:] == 2).all()
    assert (out[10:, :10] == 3).all() and (out[10:, 10:] == 0).all()
    assert jrc.read_window("extent", 0, 100, 1, 1, source=tmp_path)[0, 0] == 9


# ---------------------------------------------------------------- the rules

PX = 700.0          # m2 of a JRC pixel near 30 degrees: 1 ha is about 14.3 pixels


def counts_for(n_pix, ext_n, water_by_year, perm_by_year=None, occ=0.0):
    """Counts for one depression from per-year arrays (length Y)."""
    c = water.Counts(np.array([1]), Y)
    c.n_pix[:] = n_pix
    c.ext_n[:] = ext_n
    c.occ_sum[:] = occ * n_pix
    c.water[:, 0] = water_by_year
    c.permanent[:, 0] = 0 if perm_by_year is None else perm_by_year
    return c


def one(counts, clear=None, views_res=5):
    clear = np.ones((Y, 1), bool) if clear is None else np.asarray(clear, bool).reshape(Y, 1)
    return water.apply_rules(counts, YEARS, clear, np.array([views_res]), np.array([PX])).iloc[0]


def test_never_seen_wet_with_the_views_of_its_area():
    row = one(counts_for(50, 0, np.zeros(Y)))
    assert row.water_class == "never_seen_wet" and row.views_res == 5
    assert row.clear_years == Y and row.wet_years == 0 and row.first_wet_year == -1


def test_unknown_views_stay_never_seen_wet_and_are_flagged():
    row = one(counts_for(50, 0, np.zeros(Y)), views_res=0)
    assert row.water_class == "never_seen_wet" and row.views_res == 0
    assert np.isnan(row.clear_years)


def test_cloudy_years_of_the_area_do_not_count():
    clear = np.ones(Y, bool)
    clear[:25] = False
    wet = np.zeros(Y)
    wet[:25] = 30                               # water in years that are not clear does not count
    row = one(counts_for(40, 10, wet), clear=clear)
    assert row.clear_years == Y - 25 and row.wet_years == 0
    assert row.water_class == "too_few_views"


def test_too_small_wins_over_everything():
    assert one(counts_for(2, 2, np.full(Y, 2.0))).water_class == "too_small"


def test_a_permanent_year_is_also_a_wet_year():
    """Found in the national run (744 too_small depressions): one water pixel reaches the
    permanent share of a 1-pixel depression but not the 2-pixel wet minimum."""
    row = one(counts_for(1, 1, np.ones(Y), perm_by_year=np.ones(Y)))
    assert row.water_class == "too_small" and row.wet_years == 0 and row.permanent_years == 0
    row = one(counts_for(3, 3, np.full(Y, 2.0), perm_by_year=np.full(Y, 2.0)))
    assert row.permanent_years == row.wet_years == Y        # 3 pixels and up: unchanged


def test_some_years_seasonal_and_permanent():
    wet = np.zeros(Y)
    wet[[3, 10, 20]] = 10                       # 10 of 40 pixels in three years
    row = one(counts_for(40, 12, wet))
    assert row.water_class == "some_years" and row.wet_years == 3
    assert (row.first_wet_year, row.last_wet_year) == (YEARS[3], YEARS[20])
    assert row.max_wet_share == pytest.approx(0.25)
    wet = np.zeros(Y)
    wet[::2] = 10                               # every other year: 19 of 38
    assert one(counts_for(40, 12, wet)).water_class == "seasonal"
    perm = np.full(Y, 30.0)
    assert one(counts_for(40, 35, perm, perm_by_year=perm)).water_class == "permanent"


def test_wet_needs_ten_per_cent_or_a_hectare_and_two_pixels():
    wet = np.zeros(Y)
    wet[0] = 3                                  # 3 of 40 pixels: below 10 % and below 1 ha
    assert one(counts_for(40, 5, wet)).wet_years == 0
    wet[0] = 4
    assert one(counts_for(40, 5, wet)).wet_years == 1
    wet = np.zeros(Y)
    wet[0] = 1                                  # 1 of 5 pixels: 20 %, but one pixel
    assert one(counts_for(5, 5, wet)).wet_years == 0
    big = np.zeros(Y)                           # a 5,000-pixel basin, water in its floor only
    big[0] = 14
    assert one(counts_for(5000, 300, big)).wet_years == 0          # under 1 ha, under 10 %
    big[0] = 15
    assert one(counts_for(5000, 300, big)).wet_years == 1          # 1.05 ha


def test_trend_detects_a_rise():
    wet = np.linspace(0, 30, Y)
    row = one(counts_for(40, 40, wet))
    assert row.trend_per_decade > 0 and row.trend_p < 0.001
    steady = one(counts_for(40, 40, np.where(np.arange(Y) % 2 == 0, 10.0, 0.0)))
    assert abs(steady.trend_per_decade) < 1e-9 and steady.trend_p > 0.5


def test_occurrence_ever_wet_and_wettest_month():
    c = counts_for(40, 10, np.zeros(Y), occ=12.5)
    c.mon_sum[0, 6] = 10 * 80                   # July
    row = one(c)
    assert row.occurrence_mean == pytest.approx(12.5)
    assert row.ever_wet_share == pytest.approx(0.25) and row.wettest_month == 7


def test_area_views_judge_years_by_the_share_of_extent_pixels_with_data():
    t = jrc.grid_transform(*jrc.grid_window((-95.2, 29.9, -95.19, 29.91))[:2])
    extent = np.zeros((40, 40), bool)
    extent[:10, :10] = True                                     # 100 extent pixels
    yearly = {y: np.where(extent, 1, 0).astype("uint8") for y in YEARS}
    yearly[YEARS[0]][:6, :10] = 0                               # 60 % without data: not clear
    yearly[YEARS[1]][:4, :10] = 0                               # 40 % without data: clear
    views = water.area_views(extent, yearly, t, YEARS)
    cell, flags = next(iter(views[5].items()))
    assert not flags[0] and flags[1] and flags.sum() == Y - 1
    clear, res = water.lookup_area([29.905], [-95.195], views, Y)
    assert res[0] == 5 and clear[:, 0].sum() == Y - 1
    _, res = water.lookup_area([10.0], [10.0], views, Y)
    assert res[0] == 0


# ---------------------------------------------------------------- counting

def test_count_by_label_matches_a_hand_count():
    lab = np.array([[1, 1, 0], [2, 2, 2], [7, 0, 0]])          # 7 is not a listed depression
    extent = np.array([[1, 0, 0], [1, 1, 0], [1, 0, 0]], bool)
    occ = np.array([[50, 0, 9], [20, 40, 0], [9, 9, 9]], "uint8")
    yearly = {y: np.array([[3, 0, 0], [2, 0, 1], [3, 0, 0]], "uint8") for y in YEARS}
    yearly[YEARS[0]] = np.array([[0, 0, 0], [2, 2, 0], [3, 0, 0]], "uint8")
    monthly = [np.full((3, 3), m, "int16") for m in range(12)]
    c = water.count_by_label(lab, np.array([1, 2]), extent, occ, yearly, monthly, YEARS)
    assert list(c.n_pix) == [2, 3] and list(c.ext_n) == [1, 2]
    assert list(c.occ_sum) == [50, 60]
    assert list(c.water[0]) == [0, 2] and list(c.permanent[1]) == [1, 0]
    assert c.mon_sum[1, 5] == 2 * 5


def test_cells_method_equals_outline_when_each_cell_is_one_depression():
    # two "cells" of four pixels, each wholly one depression: weights are exact
    pixel_cells = np.array([10, 10, 10, 10, 20, 20, 20, 20], dtype=np.uint64)
    pixel_area = np.full(8, 100.0)
    extent = np.array([1, 1, 0, 0, 0, 0, 0, 0], bool)
    occ = np.array([80, 60, 0, 0, 0, 0, 0, 0], "uint8")
    yearly = {y: np.array([3, 2, 1, 1, 0, 0, 0, 0], "uint8") for y in YEARS}
    dc = pd.DataFrame({"id": [1, 2], "h3": np.array([10, 20], np.uint64), "area_m2": [400.0, 400.0]})
    by_cells = water.count_by_cells(dc, np.array([10, 20], np.uint64), pixel_cells, pixel_area,
                                    extent, occ, yearly, None, YEARS, np.array([1, 2]))
    lab = np.array([1, 1, 1, 1, 2, 2, 2, 2]).reshape(2, 4)
    by_label = water.count_by_label(lab, np.array([1, 2]), extent.reshape(2, 4),
                                    occ.reshape(2, 4), {y: v.reshape(2, 4) for y, v in yearly.items()},
                                    None, YEARS)
    for name in ("n_pix", "ext_n", "occ_sum", "water", "permanent"):
        assert np.allclose(getattr(by_cells, name), getattr(by_label, name)), name


def test_area_views_judge_years_against_the_area_median():
    # most extent pixels first mapped after 2014: they hold 0 in earlier years, which are clear
    t = jrc.grid_transform(*jrc.grid_window((-95.2, 29.9, -95.19, 29.91))[:2])
    extent = np.zeros((40, 40), bool)
    extent[:10, :10] = True
    late = np.zeros((40, 40), bool)
    late[:6, :10] = True                                        # 60 of 100 pixels
    yearly = {y: np.where(extent & ~late if y < 2015 else extent, 1, 0).astype("uint8")
              for y in YEARS}
    yearly[1990] = np.where(extent, 0, 0).astype("uint8")
    yearly[1990][9, :5] = 1                                     # a missing year: 5 % with data
    views = water.area_views(extent, yearly, t, YEARS)
    flags = next(iter(views[5].values()))
    assert flags[YEARS.index(1984)] and flags[YEARS.index(2015)]
    assert not flags[YEARS.index(1990)] and flags.sum() == Y - 1


def test_local_tiles_in_the_server_layout_keep_the_months_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(jrc, "TILE", 10)
    for m in (1, 2):
        path = jrc.local_path(tmp_path, "monthly_recurrence", 0, 0, month=m)
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1,
                           dtype="int16") as dst:
            dst.write(np.full((10, 10), 10 * m, "int16"), 1)
    assert jrc.read_window("monthly_recurrence", 0, 0, 2, 2, month=1, source=tmp_path)[0, 0] == 10
    assert jrc.read_window("monthly_recurrence", 0, 0, 2, 2, month=2, source=tmp_path)[0, 0] == 20
    assert "monthlyRecurrence2" in str(jrc.local_path(tmp_path, "monthly_recurrence", 0, 0, month=2))


def test_open_sea_coded_255_is_neither_extent_nor_occurrence():
    from h3lowpoints.water import extent_of, occurrence_of

    raw_ext = np.array([0, 1, 255], "uint8")
    raw_occ = np.array([0, 40, 255], "uint8")
    assert extent_of(raw_ext).tolist() == [False, True, False]
    assert occurrence_of(raw_occ).tolist() == [0.0, 40.0, 0.0]
