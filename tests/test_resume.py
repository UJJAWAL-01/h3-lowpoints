"""Runs written to disk survive interruption: continuing one gives exactly the uninterrupted result.

A run is stopped at chosen points (in pass 1, in pass 2, while the tables are written, or by its
time limit after every tile) and started again with the same arguments. The result must be
identical, table for table, to a run that was never interrupted.
"""
import json

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from h3lowpoints import analyze_tiled, read_result
from h3lowpoints import tiling as T
from h3lowpoints.cli import main
from h3lowpoints.sources import usgs
from h3lowpoints.verify import check_saved
from test_verify import _corner_tiles, dem

TILE = 37            # 120 x 140 pixels -> 4 x 4 = 16 tiles
DRAINS = np.array([[500030.0, 3349960.0], [500100.0, 3349900.0]])
BUILDINGS = [box(500040, 3349920, 500060, 3349940)]
KW = dict(tile=TILE, res=12, min_depth=0.1, drains_xy=DRAINS, buildings=BUILDINGS)


@pytest.fixture
def raster(tmp_path):
    return dem(tmp_path / "d.tif", seed=5)


@pytest.fixture
def reference(raster, tmp_path):
    return analyze_tiled(raster, out_dir=tmp_path / "reference", **KW).load()


def same(folder, reference):
    got = read_result(folder)
    pd.testing.assert_frame_equal(got.depressions, reference.depressions)
    pd.testing.assert_frame_equal(got.cells, reference.cells)
    assert got.meta["tiling"] == reference.meta["tiling"]
    assert not (folder / "_work").exists() and not (folder / "_spill").exists()
    assert check_saved(folder) == []


def fail_on_call(monkeypatch, name, n):
    """Make T.<name> raise on its n-th call (counting from 1)."""
    real, calls = getattr(T, name), {"n": 0}

    def wrapped(*a, **k):
        calls["n"] += 1
        if calls["n"] == n:
            raise RuntimeError(f"simulated crash in {name}")
        return real(*a, **k)
    monkeypatch.setattr(T, name, wrapped)


@pytest.mark.parametrize("where, n", [("_pass1_tile", 1), ("_pass1_tile", 7), ("_pass1_tile", 16),
                                      ("_pass2_tile", 1), ("_pass2_tile", 9), ("_join_pieces", 1),
                                      ("_write", 1), ("_cell_chunks", 1)])
def test_a_crash_anywhere_then_continuing_gives_the_uninterrupted_result(
        raster, reference, tmp_path, monkeypatch, where, n):
    out = tmp_path / "run"
    with monkeypatch.context() as m:
        fail_on_call(m, where, n)
        with pytest.raises(RuntimeError, match="simulated crash"):
            analyze_tiled(raster, out_dir=out, **KW)
    assert (out / "_work").exists()
    computed = {"n": 0}
    real = T._pass1_tile

    def counting(*a, **k):
        computed["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(T, "_pass1_tile", counting)
    analyze_tiled(raster, out_dir=out, **KW)
    if where == "_pass1_tile":
        assert computed["n"] == 16 - (n - 1)        # tiles finished before the crash are not redone
    else:
        assert computed["n"] == 0                   # pass 1 was complete
    same(out, reference)


def test_a_time_limit_splits_the_run_into_parts(raster, reference, tmp_path, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(T.time, "monotonic", lambda: clock["t"])

    def tick(*a, **k):
        clock["t"] += 3600.0                        # every tile "takes" an hour
        return real(*a, **k)
    real = T._pass1_tile
    monkeypatch.setattr(T, "_pass1_tile", tick)
    real2 = T._pass2_tile
    monkeypatch.setattr(T, "_pass2_tile", lambda *a, **k: (clock.__setitem__("t", clock["t"] + 3600.0), real2(*a, **k))[1])
    out, parts, last = tmp_path / "run", 0, None
    while True:
        try:
            analyze_tiled(raster, out_dir=out, hours=2.5, **KW)
            break
        except T.Paused as stop:
            parts += 1
            assert last is None or stop.done > last.done      # every part makes progress
            assert stop.total == 32 and "Run the same command again" in str(stop)
            last = stop
        assert parts < 40
    assert parts >= 8                               # 32 tile steps, about 3 per part
    same(out, reference)


def test_different_settings_cannot_continue_a_run(raster, tmp_path, monkeypatch):
    out = tmp_path / "run"
    fail_on_call(monkeypatch, "_pass2_tile", 3)
    with pytest.raises(RuntimeError):
        analyze_tiled(raster, out_dir=out, **KW)
    monkeypatch.undo()
    with pytest.raises(ValueError, match="different settings.*min_depth"):
        analyze_tiled(raster, out_dir=out, **{**KW, "min_depth": 0.2})
    with pytest.raises(ValueError, match="different settings.*tile"):
        analyze_tiled(raster, out_dir=out, **{**KW, "tile": 50})


def test_a_half_written_checkpoint_is_ignored(raster, reference, tmp_path, monkeypatch):
    out = tmp_path / "run"
    fail_on_call(monkeypatch, "_pass1_tile", 5)
    with pytest.raises(RuntimeError):
        analyze_tiled(raster, out_dir=out, **KW)
    monkeypatch.undo()
    (out / "_work" / "tmp_p1_000005.npz").write_bytes(b"cut off mid-write")   # a crash during a save
    analyze_tiled(raster, out_dir=out, **KW)
    same(out, reference)


def test_continuing_equals_the_whole_raster_analysis(raster, tmp_path, monkeypatch):
    from h3lowpoints import analyze

    out = tmp_path / "run"
    fail_on_call(monkeypatch, "_pass2_tile", 6)
    with pytest.raises(RuntimeError):
        analyze_tiled(raster, out_dir=out, **KW)
    monkeypatch.undo()
    analyze_tiled(raster, out_dir=out, **KW)
    whole = analyze(raster, **{k: v for k, v in KW.items() if k != "tile"})
    got = read_result(out)
    assert len(got.depressions) == len(whole.depressions) > 0
    np.testing.assert_allclose(np.sort(got.depressions["volume_m3"]), np.sort(whole.depressions["volume_m3"]))
    pd.testing.assert_frame_equal(got.cells.sort_values("h3").reset_index(drop=True),
                                  whole.cells.sort_values("h3").reset_index(drop=True), rtol=1e-9,
                                  check_dtype=False)


def test_hours_needs_an_output_folder(raster):
    with pytest.raises(ValueError, match="hours needs out_dir"):
        analyze_tiled(raster, hours=1, tile=TILE)
    with pytest.raises(ValueError, match="positive"):
        analyze_tiled(raster, hours=0, tile=TILE, out_dir="x")


def test_cli_build_in_parts(tmp_path, monkeypatch, capsys):
    (tmp_path / "tiles").mkdir()
    bbox = _corner_tiles(tmp_path / "tiles")
    real = usgs.build_3dep_vrt
    monkeypatch.setattr(usgs, "build_3dep_vrt",
                        lambda b, path, **k: real(b, path, resolution=k.get("resolution", 30),
                                                  source=tmp_path / "tiles"))
    clock = {"t": 0.0}
    monkeypatch.setattr(T.time, "monotonic", lambda: clock.__setitem__("t", clock["t"] + 900.0) or clock["t"])
    out = tmp_path / "region"
    args = ["build", "--bbox", *map(str, bbox), "--out", str(out), "--res", "10", "--min-depth", "0.01",
            "--tile", "17", "--hours", "1"]
    codes = []
    for _ in range(60):
        codes.append(main(args))
        if codes[-1] != 3:
            break
    printed = capsys.readouterr().out
    assert codes[-1] == 0 and codes.count(3) >= 2 and set(codes[:-1]) == {3}
    assert "Run the same command again" in printed and "all invariants hold" in printed
    assert json.loads((out / "verify.json").read_text()) == {"problems": []}
    assert not (out / "_work").exists()


def test_leftovers_of_a_killed_run_are_not_counted_twice(raster, reference, tmp_path, monkeypatch):
    # a power cut or a killed process skips all clean-up: spill files of the half-written tables stay
    out = tmp_path / "run"
    fail_on_call(monkeypatch, "_write", 1)
    with pytest.raises(RuntimeError):
        analyze_tiled(raster, out_dir=out, **KW)
    monkeypatch.undo()
    from h3lowpoints.store import PART_DTYPE, partition_key
    cell = T.h3_ids(np.array([30.27]), np.array([-97.75]), 12)
    stale = np.zeros(5, dtype=PART_DTYPE)
    stale["cell"], stale["area"], stale["volume"], stale["depth"] = cell[0], 1e6, 1e6, 99.0
    (out / "_spill").mkdir(exist_ok=True)
    stale.tofile(out / "_spill" / f"{int(partition_key(cell, 12)[0]):016x}.bin")
    analyze_tiled(raster, out_dir=out, **KW)
    same(out, reference)
