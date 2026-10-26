"""How easily depressions fill: the spill cascade, rain to fill, design storms, and their checks.

The cascade is compared with a plain recursive version on random spill forests, and must
conserve water. Rain to fill (with water from upstream) is compared with a bisection that runs
the plain version once per depression and per trial depth. End to end, the three ways of giving
imperviousness (raster, table in memory, table file) must agree, and two independent answers must
agree with each other: a uniform storm fills exactly the depressions whose rain to fill it reaches.
Damaged tables must be caught by `verify`.
"""
import io
import zipfile

import h3
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze_array, read_result
from h3lowpoints.cli import main
from h3lowpoints.clip import clip_result
from h3lowpoints.hydro.cascade import cascade, rain_to_fill_upstream, spill_order
from h3lowpoints.sources import rainfall
from h3lowpoints.susceptibility import (RUNOFF_RULE, cell_means, runoff_coefficient,
                                        susceptibility, write_susceptibility)
from h3lowpoints.verify import check_saved, check_tables
from test_store import FORMATS
from test_tiling import smooth

UTM = ("EPSG:32614", from_origin(500000, 3350000, 10, 10))      # 10 m pixels


# ------------------------------------------------------------------ the cascade, by hand

def forest(seed, n, leave=0.2):
    """Random depressions spilling into others further down the list, or out (id 0)."""
    rng = np.random.default_rng(seed)
    ids = rng.permutation(np.arange(1, n + 1)) * 3          # ids neither sorted nor contiguous
    spill_to = np.zeros(n, dtype=np.int64)
    for i in range(n - 1):
        if rng.random() > leave:
            spill_to[i] = ids[rng.integers(i + 1, n)]
    volume = rng.uniform(1, 100, n)
    area = rng.uniform(10, 1000, n)
    return ids, spill_to, volume, area


def plain_inflow(ids, spill_to, own, volume):
    """Water reaching each depression, by recursion over the depressions spilling into it."""
    pos = {d: i for i, d in enumerate(ids)}
    feeders = {i: [] for i in range(len(ids))}
    for i, t in enumerate(spill_to):
        if t in pos:
            feeders[pos[t]].append(i)
    memo = {}

    def reach(i):
        if i not in memo:
            memo[i] = own[i] + sum(max(0.0, reach(j) - volume[j]) for j in feeders[i])
        return memo[i]
    return np.array([reach(i) for i in range(len(ids))])


@pytest.mark.parametrize("seed", range(5))
def test_cascade_matches_a_plain_recursion_and_conserves_water(seed):
    ids, spill_to, volume, area = forest(seed, 300)
    own = area * np.random.default_rng(seed).uniform(0.01, 0.2, len(ids))
    order, target = spill_order(ids, spill_to)
    inflow, leaving = cascade(order, target, own, volume)
    assert np.allclose(inflow, plain_inflow(ids, spill_to, own, volume), rtol=1e-12)
    stored = np.minimum(inflow, volume).sum()
    assert own.sum() == pytest.approx(stored + leaving, rel=1e-12)
    # a mistaken cascade that passes on all water, not just the overflow, must disagree
    wrong, _ = cascade(order, target, own, np.zeros(len(ids)))
    assert not np.allclose(wrong, inflow)


def test_spill_order_puts_feeders_first_and_finds_loops():
    ids, spill_to, _, _ = forest(7, 200)
    order, target = spill_order(ids, spill_to)
    rank = np.empty(len(ids), dtype=int)
    rank[order] = np.arange(len(ids))
    has = target >= 0
    assert (rank[has] < rank[target[has]]).all()
    # spill_to naming a depression not in the table (a clipped result) means leaving
    assert spill_order([5, 6], [99, 5])[1].tolist() == [-1, 0]
    with pytest.raises(ValueError, match="loop"):
        spill_order([1, 2, 3], [2, 3, 1])


def test_unknown_runoff_makes_everything_downstream_unknown():
    ids, spill_to = np.array([1, 2, 3, 4]), np.array([2, 3, 0, 3])
    own = np.array([np.nan, 1.0, 1.0, 1.0])
    order, target = spill_order(ids, spill_to)
    inflow, _ = cascade(order, target, own, np.ones(4))
    assert np.isnan(inflow[:3]).all() and inflow[3] == 1.0
    found = rain_to_fill_upstream(order, target, np.array([np.nan, 10.0, 10.0, 10.0]), np.ones(4))
    assert np.isnan(found[:3]).all() and found[3] == pytest.approx(0.1)


def bisect_rain(ids, spill_to, area, volume, i):
    lo, hi = 0.0, volume[i] / area[i]
    for _ in range(100):
        mid = (lo + hi) / 2
        if plain_inflow(ids, spill_to, area * mid, volume)[i] >= volume[i]:
            hi = mid
        else:
            lo = mid
    return hi


@pytest.mark.parametrize("seed", range(3))
def test_rain_to_fill_upstream_is_within_one_percent_of_a_bisection(seed):
    ids, spill_to, volume, area = forest(seed, 60)
    order, target = spill_order(ids, spill_to)
    found = rain_to_fill_upstream(order, target, area, volume, rel=0.01)
    own = volume / area
    assert (found <= own * (1 + 1e-12)).all()
    fed = np.isin(ids, spill_to)
    # nothing spills into these: the rain is exactly their own
    assert np.allclose(found[~fed], own[~fed], rtol=1e-9)
    for i in np.flatnonzero(fed):
        exact = bisect_rain(ids, spill_to, area, volume, i)
        assert abs(found[i] / exact - 1) <= 0.01, (i, found[i], exact)
    assert (found[fed] < own[fed] * (1 - 1e-6)).any()       # upstream water does arrive


# ------------------------------------------------------------------ end to end

@pytest.fixture(scope="module")
def result():
    z = smooth(3, (150, 150), sigma=3.0, trend=2.0)
    res = analyze_array(z, UTM[1], UTM[0], res=11, min_depth=0.05, catchments=True)
    assert len(res.depressions) > 40 and (res.depressions["spill_to"] > 0).sum() > 20
    return res


def imperviousness_raster(path, seed=0):
    """Percent impervious in Conus Albers at 30 m (as NLCD), covering the result with a margin,
    with a strip of no data."""
    rng = np.random.default_rng(seed)
    from pyproj import Transformer
    to_albers = Transformer.from_crs(UTM[0], "EPSG:5070", always_xy=True)
    xs, ys = to_albers.transform([499900, 501700, 499900, 501700], [3348300, 3348300, 3350100, 3350100])
    west, north = np.floor(min(xs) / 30) * 30, np.ceil(max(ys) / 30) * 30
    width, height = int((max(xs) - west) / 30) + 2, int((north - min(ys)) / 30) + 2
    arr = rng.integers(0, 101, (height, width)).astype("uint8")
    arr[:, :3] = 255
    prof = {"driver": "GTiff", "height": height, "width": width, "count": 1, "dtype": "uint8",
            "crs": "EPSG:5070", "transform": from_origin(west, north, 30, 30), "nodata": 255}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)
    return path


def test_cell_means_are_the_means_of_the_pixels_whose_centre_is_in_the_cell(tmp_path):
    path = imperviousness_raster(tmp_path / "imp.tif")
    means = cell_means(path, 9)
    with rasterio.open(path) as src:
        arr = src.read(1)
        rows, cols = np.nonzero(arr != 255)
        from pyproj import Transformer
        xs, ys = rasterio.transform.xy(src.transform, rows, cols)
        lon, lat = Transformer.from_crs(src.crs, 4326, always_xy=True).transform(xs, ys)
    frame = pd.DataFrame({"h3": [h3.latlng_to_cell(a, b, 9) for a, b in zip(lat, lon)],
                          "v": arr[rows, cols].astype(float)})
    expected = frame.groupby("h3")["v"].agg(["mean", "size"]).sort_index()
    assert list(means["h3"]) == list(expected.index)
    assert np.allclose(means["mean"], expected["mean"])
    assert (means["n_pixels"].to_numpy() == expected["size"].to_numpy()).all()
    parent = h3.cell_to_parent(means["h3"].iloc[0], 6)
    part = cell_means(path, 9, parent=parent)
    assert len(part) and all(h3.cell_to_parent(c, 6) == parent for c in part["h3"])
    assert part.equals(means[means["h3"].map(lambda c: h3.cell_to_parent(c, 6)) == parent]
                       .reset_index(drop=True))


@pytest.mark.parametrize("fmt", FORMATS)
def test_raster_table_and_file_give_the_same_answer(result, tmp_path, fmt):
    raster = imperviousness_raster(tmp_path / "imp.tif")
    table = cell_means(raster, 11).rename(columns={"mean": "impervious_pct"})
    file = tmp_path / f"imp.{fmt}"
    table.to_csv(file, index=False) if fmt == "csv" else table.to_parquet(file, index=False)
    a = susceptibility(result, impervious=raster, rain=50.0)
    b = susceptibility(result, impervious=table, rain=50.0)
    c = susceptibility(result, impervious=file, rain=50.0, rows=37)   # small pieces: streamed
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(a, c)
    known = a["impervious_pct"].notna()
    assert known.mean() > 0.5
    assert np.allclose(a["runoff_coef"][known], runoff_coefficient(a["impervious_pct"][known]))
    assert a.attrs["settings"]["runoff"] == RUNOFF_RULE


def test_a_uniform_storm_fills_exactly_where_its_rain_reaches_rain_to_fill(result):
    for depth in (5.0, 20.0, 80.0):
        s = susceptibility(result, runoff_coef=0.4, rain=depth)
        full = s["fill_ratio"] >= 1
        fills = s["rain_to_fill_upstream_mm"] <= depth
        unsure = (np.abs(s["rain_to_fill_upstream_mm"] / depth - 1) <= 0.0101)  # within 1 %
        assert (full == fills)[~unsure].all()
        alone = ~s["id"].isin(result.depressions["spill_to"])       # nothing spills into these
        assert np.allclose(s["fill_ratio"][alone], depth / s["rain_to_fill_mm"][alone])


def test_storms_by_return_period(result):
    rain = {1: 10.0, 2: 20.0, 10: 40.0, 100: lambda lat, lon: np.full(len(lat), 90.0)}
    s = susceptibility(result, runoff_coef=0.3, rain=rain)
    assert {"fill_ratio_2yr", "fill_ratio_10yr", "fill_ratio_100yr"} <= set(s)
    assert "fill_ratio_1yr" not in s
    ratio = {y: susceptibility(result, runoff_coef=0.3, rain=float(d) if not callable(d) else 90.0)
             ["fill_ratio"] for y, d in rain.items()}
    expected = np.full(len(s), np.inf)
    for y in sorted(rain, reverse=True):
        expected[ratio[y].to_numpy() >= 1] = y
    assert np.array_equal(s["fills_in_years"].to_numpy(), expected)
    assert np.allclose(s["fill_ratio_10yr"], ratio[10])


def test_too_little_coverage_gives_nan(result):
    cells = result.catchments["h3"].unique()
    half = pd.DataFrame({"h3": cells[::2], "impervious_pct": 50.0})
    s = susceptibility(result, impervious=half, min_coverage=0.9)
    loose = susceptibility(result, impervious=half, min_coverage=0.01)
    assert s["runoff_coef"].isna().any() and loose["runoff_coef"].notna().all()
    assert np.allclose(loose["runoff_coef"], 0.5)
    # rain known on only part of the cells: storm columns NaN there, and below them
    some = set(cells[: len(cells) // 3])

    def patchy(lat, lon):
        here = [h3.latlng_to_cell(a, b, 11) in some for a, b in zip(lat, lon)]
        return np.where(here, 50.0, np.nan)
    s = susceptibility(result, runoff_coef=0.5, rain={10: patchy})
    assert s["fill_ratio_10yr"].isna().any() and s["fill_ratio_10yr"].notna().any()
    assert s["rain_to_fill_mm"].notna().all()


def test_settings_are_checked(result):
    with pytest.raises(ValueError, match="either"):
        susceptibility(result)
    with pytest.raises(ValueError, match="either"):
        susceptibility(result, impervious="x.tif", runoff_coef=0.5)
    with pytest.raises(ValueError, match="runoff_coef"):
        susceptibility(result, runoff_coef=1.5)
    with pytest.raises(ValueError, match="return periods"):
        susceptibility(result, runoff_coef=0.5, rain={"big": 50.0})
    plain = analyze_array(smooth(3, (40, 40)), UTM[1], UTM[0], res=11, min_depth=0.05)
    with pytest.raises(ValueError, match="catchments=True"):
        susceptibility(plain, runoff_coef=0.5)


@pytest.mark.parametrize("fmt", FORMATS)
def test_saved_read_back_verified_and_clipped(result, tmp_path, fmt):
    folder = result.save(tmp_path / "r", format=fmt)
    table = susceptibility(folder, runoff_coef=0.5, rain={2: 20.0, 10: 40.0, 100: 80.0})
    path = write_susceptibility(folder, table)
    assert path.name == f"susceptibility.{fmt}"
    back = read_result(folder)
    pd.testing.assert_frame_equal(back.susceptibility, table, check_dtype=False)
    assert back.meta["susceptibility"]["rain"] == "given per storm"
    assert check_saved(folder) == []
    lat, lon = result.depressions["lat"].median(), result.depressions["lon"].median()
    from shapely.geometry import box
    clipped = clip_result(folder, box(lon - 1, lat - 1, lon, lat + 1), tmp_path / "c")
    kept = read_result(clipped)
    assert set(kept.susceptibility["id"]) == set(kept.depressions["id"])
    assert kept.meta["susceptibility"]["clipped_after"] is True
    assert check_saved(clipped) == []


def damaged(s, how):
    s = s.copy()
    if how == "coef":
        s.loc[0, "runoff_coef"] *= 1.1
    elif how == "area":
        s.loc[0, "runoff_area_m2"] *= 1.1
    elif how == "fill":
        s.loc[0, "rain_to_fill_mm"] *= 1.1
    elif how == "upstream":
        s.loc[0, "rain_to_fill_upstream_mm"] = s.loc[0, "rain_to_fill_mm"] * 2
    elif how == "too low":
        s["rain_to_fill_upstream_mm"] = s["rain_to_fill_upstream_mm"] / 1000
    elif how == "missing":
        s = s.iloc[1:]
    elif how == "twice":
        s = pd.concat([s, s.iloc[:1]])
    elif how == "years":
        s.loc[s["fill_ratio_2yr"] >= 1, "fills_in_years"] = 100.0
    elif how == "imp":
        s.loc[0, "impervious_pct"] = 120.0
    return s


@pytest.mark.parametrize("how", ["coef", "area", "fill", "upstream", "too low", "missing", "twice",
                                 "years", "imp"])
def test_verify_catches_damage(result, how):
    imp = pd.DataFrame({"h3": result.catchments["h3"].unique(), "impervious_pct": 30.0})
    s = susceptibility(result, impervious=imp, rain={2: 30.0, 10: 60.0, 100: 120.0})
    meta = {**result.meta, "susceptibility": s.attrs["settings"]}
    args = (result.depressions, result.cells, meta, result.catchments, None)
    assert check_tables(*args, s) == []
    assert (s["fill_ratio_2yr"] >= 1).any()
    assert check_tables(*args, damaged(s, how)) != []


def test_cli_index(result, tmp_path):
    folder = result.save(tmp_path / "r")
    assert main(["index", str(folder), "--runoff-coef", "0.5", "--rain", "40"]) == 0
    back = read_result(folder)
    assert "fill_ratio" in back.susceptibility
    raster = imperviousness_raster(tmp_path / "imp.tif")
    assert main(["index", str(folder), "--impervious", str(raster)]) == 0
    assert read_result(folder).susceptibility["impervious_pct"].notna().any()
    with pytest.raises(SystemExit, match="--rain"):
        main(["index", str(folder), "--runoff-coef", "0.5", "--rain", "lots"])


# ------------------------------------------------------------------ Atlas 14 grids

def ascii_grid(values, west, south, cell, nodata=-9, centre=False):
    rows, cols = values.shape
    corner = (f"xllcenter {west + cell / 2}\nyllcenter {south + cell / 2}\n" if centre
              else f"xllcorner {west}\nyllcorner {south}\n")
    body = "\n".join(" ".join(str(int(v)) for v in row) for row in values)
    return f"ncols {cols}\nnrows {rows}\n{corner}cellsize {cell}\nNODATA_value {nodata}\n{body}\n".encode()


def zipped(name, data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name + ".asc", data)
        z.writestr(name + ".xml", "<metadata/>")
    return buf.getvalue()


def test_read_ascii_grid_both_corner_styles_and_nodata():
    values = np.array([[1000, -9], [2000, 3000]])
    for centre in (False, True):
        out, west, north, cell = rainfall.read_ascii_grid(
            zipped("g", ascii_grid(values, -100.0, 30.0, 0.5, centre=centre)))
        assert (west, north, cell) == (-100.0, 31.0, 0.5)
        assert np.isnan(out[0, 1]) and out[1, 1] == 3000
    out, *_ = rainfall.read_ascii_grid(ascii_grid(np.array([[-999, 5]]), 0, 0, 1, nodata=-999))
    assert np.isnan(out[0, 0]) and out[0, 1] == 5


def test_atlas14_grids_sample_convert_and_average_overlaps(monkeypatch):
    # two project areas overlapping in one column; values in thousandths of an inch
    a = np.array([[1000, 2000, 3000], [1000, 2000, 3000]])
    b = np.array([[4000, 5000], [-9, 5000]])
    files = {"orb": zipped("orb", ascii_grid(a, -90.0, 30.0, 1.0)),
             "ne": zipped("ne", ascii_grid(b, -88.0, 30.0, 1.0))}
    seen = []

    def fake(url):
        seen.append(url)
        return files[url.rsplit("/", 2)[1]]
    monkeypatch.setattr(rainfall, "read_bytes", fake)
    monkeypatch.setattr(rainfall, "ATLAS14_EXTENTS", {"orb": (-90.0, 30.0, -87.0, 32.0),
                                                      "ne": (-88.0, 30.0, -86.0, 32.0)})
    grids = rainfall.Atlas14Grids(years=(100,), volumes=("orb", "ne"))
    assert seen == []                                         # nothing is read until needed
    assert grids.depth(100, [31.5], [-89.5])[0] == pytest.approx(25.4)
    assert seen == ["https://hdsc.nws.noaa.gov/pub/hdsc/data/orb/orb100yr24ha.zip"]
    lat = np.array([31.5, 31.5, 30.5, 31.5, 35.0])
    lon = np.array([-89.5, -87.5, -87.5, -86.5, -89.5])
    got = grids.depth(100, lat, lon)
    assert len(seen) == 2                                     # each grid read once
    inch = 25.4
    assert got[0] == pytest.approx(1 * inch)
    assert got[1] == pytest.approx((3 + 4) / 2 * inch)        # both cover it: the mean
    assert got[2] == pytest.approx(3 * inch)                  # ne has no data there
    assert got[3] == pytest.approx(5 * inch)
    assert np.isnan(got[4])                                   # outside every grid
    with pytest.raises(ValueError, match="not loaded"):
        grids.depth(10, lat, lon)
    with pytest.raises(ValueError, match="duration"):
        rainfall.atlas14_grid_url("ne", 100, "25h")
    with pytest.raises(ValueError, match="years"):
        rainfall.Atlas14Grids(years=(3,))


@pytest.mark.network
def test_atlas14_extents_match_noaas_files(tmp_path):
    grids = rainfall.Atlas14Grids(years=(2,), cache_dir=tmp_path)
    for volume, (w, s, e, n) in rainfall.ATLAS14_EXTENTS.items():
        values, west, north, cell = grids.grid(volume, 2)
        rows, cols = values.shape
        got = (west, north - rows * cell, west + cols * cell, north)
        assert np.allclose(got, (w, s, e, n), atol=1e-3), volume


@pytest.mark.network
def test_atlas14_grid_matches_the_point_service(tmp_path):
    lat, lon = 29.76, -95.37                                  # Houston
    grids = rainfall.Atlas14Grids(years=(100,), volumes=("tx",), cache_dir=tmp_path)
    point = rainfall.rainfall_depth(lat, lon, "24-hr", 100)
    assert grids.depth(100, [lat], [lon])[0] == pytest.approx(point, rel=0.01)
