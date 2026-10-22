"""Result files (CSV and Parquet) and tiled runs that write their tables to disk.

Saved tables must read back exactly, whatever the format or the size of the pieces they were
written and read in, and a run written to disk must equal the same run held in memory.
"""
import importlib.util
import sys

import h3
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze, analyze_tiled, read_result
from h3lowpoints import store as S
from h3lowpoints import tiling as T
from h3lowpoints import verify as V
from h3lowpoints.cli import main
from h3lowpoints.pipeline import Result
from test_verify import CORRUPTIONS, dem

needs_pyarrow = pytest.mark.skipif(importlib.util.find_spec("pyarrow") is None,
                                   reason="needs pyarrow (the parquet extra)")
FORMATS = ["csv", pytest.param("parquet", marks=needs_pyarrow)]


def same_tables(a, b):
    assert list(a.columns) == list(b.columns)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True),
                                  check_dtype=False, rtol=0, atol=0)


def flat_dem(path):
    prof = {"driver": "GTiff", "height": 20, "width": 20, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1)}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(np.zeros((20, 20), dtype="float32"), 1)
    return str(path)


@pytest.fixture
def good(tmp_path):
    return analyze(dem(tmp_path / "d.tif"), res=12, min_depth=0.1)


# ------------------------------------------------------------------ saving and reading back

@pytest.mark.parametrize("fmt", FORMATS)
def test_saved_tables_read_back_exactly(tmp_path, good, fmt):
    folder = good.save(tmp_path / "out", format=fmt)
    back = read_result(folder)
    same_tables(back.depressions, good.depressions)
    same_tables(back.cells, good.cells)
    assert back.meta["output"] == {"format": fmt, "n_cells": len(good.cells)}
    assert back.depressions["edge"].dtype == bool and back.cells["n_lowpoints"].dtype == "int64"
    assert V.check_saved(folder) == []


@pytest.mark.parametrize("fmt", FORMATS)
def test_ids_that_look_like_numbers_stay_text(tmp_path, fmt):
    # resolution-14 ids with only digits after "8e" read as infinity if a reader guesses types
    ids = ["8e4516503458447", "8e7020889730207", "8e2634074644927"]
    S.write_table(pd.DataFrame({"h3": ids, "v": [1.0, 2.0, 3.0]}), tmp_path / f"cells.{fmt}", fmt)
    back = pd.concat(list(S.iter_table(tmp_path, "cells")))
    assert list(back["h3"]) == ids


@pytest.mark.parametrize("fmt", FORMATS)
def test_empty_results_round_trip(tmp_path, fmt):
    res = analyze(flat_dem(tmp_path / "flat.tif"))
    back = read_result(res.save(tmp_path / "out", format=fmt))
    assert len(back.depressions) == len(back.cells) == 0
    assert list(back.depressions.columns) == list(res.depressions.columns)
    assert list(back.cells.columns) == list(res.cells.columns)
    assert V.check_saved(tmp_path / "out") == []


@needs_pyarrow
def test_a_folder_reused_with_the_other_format_holds_only_the_new_tables(tmp_path, good):
    good.save(tmp_path / "out", format="csv")
    good.save(tmp_path / "out", format="parquet")
    assert not (tmp_path / "out" / "cells.csv").exists()
    assert S.table_path(tmp_path / "out", "cells").suffix == ".parquet"
    p = dem(tmp_path / "d.tif")                       # the same for runs written to disk
    analyze_tiled(p, tile=50, res=12, min_depth=0.1, out_dir=tmp_path / "run", format="parquet")
    analyze_tiled(p, tile=50, res=12, min_depth=0.1, out_dir=tmp_path / "run", format="csv")
    assert sorted(f.name for f in (tmp_path / "run").glob("*.*")) == ["cells.csv", "depressions.csv", "meta.json"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_reading_only_some_columns(tmp_path, good, fmt):
    S.write_table(good.cells, tmp_path / f"cells.{fmt}", fmt)
    parts = list(S.iter_table(tmp_path, "cells", rows=5, columns=["max_depth_m", "h3", "absent"]))
    same_tables(pd.concat(parts), good.cells[["h3", "max_depth_m"]])     # the table's order
    S.write_table(good.cells.iloc[:0], tmp_path / f"cells.{fmt}", fmt)
    assert list(next(S.iter_table(tmp_path, "cells", columns=["h3"])).columns) == ["h3"]


def test_writing_in_pieces_equals_writing_at_once(tmp_path, good, monkeypatch):
    monkeypatch.setattr(S, "CHUNK_ROWS", 7)
    S.write_table(good.cells, tmp_path / "cells.csv")
    parts = list(S.iter_table(tmp_path, "cells", rows=5))
    assert len(parts) == -(-len(good.cells) // 5)
    same_tables(pd.concat(parts), good.cells)


def test_format_errors(tmp_path, good, monkeypatch):
    with pytest.raises(ValueError, match="format must be one of"):
        good.save(tmp_path / "out", format="xlsx")
    monkeypatch.setitem(sys.modules, "pyarrow", None)            # as if pyarrow were not installed
    with pytest.raises(ImportError, match=r"h3-lowpoints\[parquet\]"):
        good.save(tmp_path / "out", format="parquet")
    with pytest.raises(FileNotFoundError, match="missing"):
        read_result(tmp_path / "nowhere")


@pytest.mark.parametrize("name", list(CORRUPTIONS))
def test_every_corruption_is_caught_in_a_saved_result_read_in_pieces(tmp_path, good, name):
    corrupt, message = CORRUPTIONS[name]
    d, c, m = corrupt(good.depressions.copy(), good.cells.copy(), dict(good.meta))
    Result(d, c, m).save(tmp_path / "out")
    problems = V.check_saved(tmp_path / "out", rows=3)
    assert any(message in p for p in problems), problems


def test_duplicate_cells_in_different_pieces_are_caught(tmp_path, good):
    cells = pd.concat([good.cells, good.cells.iloc[[0]]], ignore_index=True)
    Result(good.depressions, cells, good.meta).save(tmp_path / "out")
    assert any("not unique" in p for p in V.check_saved(tmp_path / "out", rows=4))


def test_ids_that_are_not_h3_cells_are_reported(tmp_path, good):
    cells = good.cells.assign(h3=["nonsense"] + list(good.cells["h3"].iloc[1:]))
    Result(good.depressions, cells, good.meta).save(tmp_path / "out")
    assert any("not valid H3 cells" in p for p in V.check_saved(tmp_path / "out"))


# ------------------------------------------------------------------ tiled runs written to disk

@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("tile", [23, 60, 1000])
def test_a_run_written_to_disk_equals_the_run_in_memory(tmp_path, fmt, tile, monkeypatch):
    monkeypatch.setattr(T, "CHUNK_ROWS", 7)          # write the depressions in many pieces
    p = dem(tmp_path / "d.tif", seed=3)
    mem = analyze_tiled(p, tile=tile, res=12, min_depth=0.1)
    disk = analyze_tiled(p, tile=tile, res=12, min_depth=0.1, out_dir=tmp_path / "out", format=fmt)
    assert disk.depressions is None and disk.cells is None and disk.path == tmp_path / "out"
    loaded = disk.load()
    same_tables(loaded.depressions, mem.depressions)
    same_tables(loaded.cells, mem.cells)
    assert disk.meta["output"] == {"format": fmt, "n_cells": len(mem.cells)}
    assert disk.meta["n_depressions"] == len(mem.depressions)
    assert not (tmp_path / "out" / "_spill").exists()
    assert V.check_result(disk) == []
    with pytest.raises(ValueError, match="already on disk"):
        disk.save(tmp_path / "again")


def test_a_run_written_to_disk_with_nothing_found(tmp_path):
    res = analyze_tiled(flat_dem(tmp_path / "flat.tif"), tile=7, out_dir=tmp_path / "out")
    back = res.load()
    assert len(back.depressions) == len(back.cells) == 0
    assert V.check_result(res) == []


def test_the_spill_folder_is_removed_when_a_run_fails(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("stop")
    monkeypatch.setattr(T, "_join_pieces", broken)
    with pytest.raises(RuntimeError):
        analyze_tiled(dem(tmp_path / "d.tif"), tile=40, res=12, out_dir=tmp_path / "out")
    assert not (tmp_path / "out" / "_spill").exists()


def test_disk_and_memory_agree_on_a_large_lattice_of_pits(tmp_path):
    # thousands of depressions on a grid centred where three partitions meet
    lat, lon = h3.cell_to_boundary(h3.latlng_to_cell(30.0, -98.0, 12 - S.PART_DEPTH))[0]
    arr = np.full((300, 300), 10.0, dtype="float32")
    arr[2:-2:5, 2:-2:5] = 9.0
    px = 1e-5                                                  # about 1 m
    prof = {"driver": "GTiff", "height": 300, "width": 300, "count": 1, "dtype": "float32",
            "crs": "EPSG:4326", "transform": from_origin(lon - 150 * px, lat + 150 * px, px, px)}
    with rasterio.open(tmp_path / "pits.tif", "w", **prof) as dst:
        dst.write(arr, 1)
    p = str(tmp_path / "pits.tif")
    mem = analyze_tiled(p, tile=64, res=12, min_depth=0.5)
    loaded = analyze_tiled(p, tile=64, res=12, min_depth=0.5, out_dir=tmp_path / "out").load()
    assert len(mem.depressions) > 3000
    assert len(set(S.partition_key(S.ids_to_int(mem.cells["h3"]), 12))) >= 2
    same_tables(loaded.depressions, mem.depressions)
    same_tables(loaded.cells, mem.cells)


# ------------------------------------------------------------------ commands

@pytest.mark.parametrize("fmt", FORMATS)
def test_cli_analyze_in_either_format_then_map_and_verify(tmp_path, fmt, capsys):
    p = dem(tmp_path / "d.tif")
    out = tmp_path / "out"
    assert main(["analyze", p, "--out", str(out), "--res", "12", "--min-depth", "0.1",
                 "--format", fmt]) == 0
    assert (out / f"cells.{fmt}").exists() and (out / f"depressions.{fmt}").exists()
    assert main(["verify", str(out)]) == 0
    assert main(["map", str(out), "--out", str(tmp_path / "m.html")]) == 0
    assert (tmp_path / "m.html").stat().st_size > 1000


@pytest.mark.parametrize("rows", [3, 50, 10**6])
def test_rolling_up_a_saved_result_in_pieces_equals_rolling_up_the_whole(tmp_path, good, rows):
    from h3lowpoints.pipeline import rollup, rollup_saved

    folder = good.save(tmp_path / "out")
    for res in (11, 9, 6):
        want = rollup(good.cells, res).sort_values("h3").reset_index(drop=True)
        got = rollup_saved(folder, res, rows=rows).sort_values("h3").reset_index(drop=True)
        pd.testing.assert_frame_equal(got, want, check_dtype=False, rtol=1e-12)
