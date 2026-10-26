"""Result files: writing tables piece by piece, reading them back, and spilling to disk.

A result folder holds `depressions.<ext>`, `cells.<ext>` and `meta.json`, where the extension is
`csv` (always available) or `parquet` (needs the `parquet` extra, `pip install h3-lowpoints[parquet]`).
Tables are written in pieces, so a region's tables never need to fit in memory at once, and read
back in pieces by `iter_table`.

H3 ids are written as text (`8a2a1072b59ffff`). They are always read back as text: a reader that
guesses types turns ids such as `8e4516503458447` (resolution 14) into the number infinity.

`CellParts` holds per-(piece, H3 cell) sums during a tiled run, grouped by a coarse parent cell,
either in memory or in files on disk.
"""
import json
import os
import re
import shutil
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

FORMATS = ("csv", "parquet")
H3_COLUMNS = ("h3", "h3_lowpoint", "h3_lowpoint_15")
CHUNK_ROWS = 500_000            # rows read or written at a time
PART_DEPTH = 6                  # partitions are parents this many levels coarser than the cells
# Every number must read back to the exact value written. pandas 3.1 does so by default (and
# deprecates the option); older versions need float_precision="round_trip".
_EXACT_FLOATS = ({} if tuple(map(int, re.match(r"(\d+)\.(\d+)", pd.__version__).groups())) >= (3, 1)
                 else {"float_precision": "round_trip"})


class Paused(Exception):
    """A long job written to disk stopped at its time limit (`hours`); run it again to continue.

    `done` and `total` count its steps (for the tiled analysis: every tile twice, once per pass;
    for a water mask: tiles).
    """

    def __init__(self, message, done, total):
        super().__init__(message)
        self.done, self.total = done, total


def check_format(fmt):
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}, got {fmt!r}")
    if fmt == "parquet":
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            raise ImportError("writing Parquet needs pyarrow: pip install h3-lowpoints[parquet]") from None


def table_path(folder, name):
    """The file holding table `name` ("depressions" or "cells") in a result folder, or None."""
    for fmt in FORMATS:
        path = Path(folder) / f"{name}.{fmt}"
        if path.exists():
            return path
    return None


def _arrow_schema(df):
    """Parquet column types from the DataFrame's dtypes (text, bool, int64 or float64)."""
    import pyarrow as pa

    fields = []
    for name, dtype in df.dtypes.items():
        if pd.api.types.is_bool_dtype(dtype):
            typ = pa.bool_()
        elif pd.api.types.is_integer_dtype(dtype):
            typ = pa.int64()
        elif pd.api.types.is_float_dtype(dtype):
            typ = pa.float64()
        else:
            typ = pa.string()
        fields.append(pa.field(str(name), typ))
    return pa.schema(fields)


class TableWriter:
    """Appends DataFrames with the same columns to one CSV or Parquet file."""

    def __init__(self, path, fmt="csv"):
        check_format(fmt)
        self.path, self.fmt = Path(path), fmt
        self.rows, self._writer, self._schema, self._started = 0, None, None, False
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, df):
        if self.fmt == "csv":
            df.to_csv(self.path, mode="a" if self._started else "w", header=not self._started,
                      index=False)
        else:
            import pyarrow as pa
            import pyarrow.parquet as pq

            if self._writer is None:
                self._schema = _arrow_schema(df)
                self._writer = pq.ParquetWriter(self.path, self._schema, compression="zstd")
            table = pa.Table.from_pandas(df, schema=self._schema, preserve_index=False)
            self._writer.write_table(table.replace_schema_metadata(None))
        self._started = True
        self.rows += len(df)

    def close(self, empty=None):
        """Finish the file. `empty` (a DataFrame with the columns) is written if nothing was."""
        if not self._started and empty is not None:
            self.write(empty)
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def write_table(df, path, fmt="csv"):
    """Write a whole DataFrame (in pieces of `CHUNK_ROWS` rows)."""
    with TableWriter(path, fmt) as w:
        for start in range(0, max(len(df), 1), CHUNK_ROWS):
            w.write(df.iloc[start:start + CHUNK_ROWS])
    return Path(path)


def _text_ids(df):
    for col in H3_COLUMNS:
        if col in df:
            df[col] = df[col].astype(object)
    return df


def iter_table(folder, name, rows=CHUNK_ROWS, columns=None):
    """Yield table `name` of a result folder as DataFrames of at most `rows` rows.

    `columns` reads only those of the named columns the table has (all columns when None).
    """
    path = table_path(folder, name)
    if path is None:
        raise FileNotFoundError(f"{folder} has no {name}.csv or {name}.parquet")
    yield from iter_file(path, rows, columns)


def iter_file(path, rows=CHUNK_ROWS, columns=None):
    """Yield a .csv or .parquet table file as DataFrames of at most `rows` rows (see iter_table)."""
    path = Path(path)
    if path.suffix not in (".csv", ".parquet"):
        raise ValueError(f"{path} is not a .csv or .parquet file")
    if path.suffix == ".csv":
        header = pd.read_csv(path, nrows=0).columns
        dtype = {c: str for c in H3_COLUMNS if c in header}
        yielded = False
        opts = {"dtype": dtype, **_EXACT_FLOATS}
        if columns is not None:
            opts["usecols"] = [c for c in header if c in columns]
        for chunk in pd.read_csv(path, chunksize=rows, **opts):
            yielded = True
            yield _text_ids(chunk)
        if not yielded:
            yield _text_ids(pd.read_csv(path, **opts))
    else:
        import pyarrow.parquet as pq

        f = pq.ParquetFile(path)
        cols = None if columns is None else [c for c in f.schema_arrow.names if c in columns]
        if f.metadata.num_rows == 0:
            empty = f.schema_arrow.empty_table()
            yield _text_ids((empty if cols is None else empty.select(cols)).to_pandas())
            return
        for batch in f.iter_batches(batch_size=rows, columns=cols):
            yield _text_ids(batch.to_pandas())


def read_table(folder, name):
    """Table `name` of a result folder as one DataFrame."""
    parts = list(iter_table(folder, name))
    return pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]


def read_meta(folder):
    return json.loads((Path(folder) / "meta.json").read_text())


def write_meta(folder, meta):
    path = Path(folder) / "meta.json"
    path.write_text(json.dumps(meta, indent=2, default=str))
    return path


def remove_other_formats(folder, fmt):
    """Delete tables of the format not being written, so a reused folder holds one result."""
    for name in ("depressions", "cells", "catchments", "depression_cells", "susceptibility"):
        for other in FORMATS:
            if other != fmt:
                (Path(folder) / f"{name}.{other}").unlink(missing_ok=True)


def missing_files(folder):
    """Names of the files a result folder should have but does not."""
    folder = Path(folder)
    out = [f"{n}.csv or {n}.parquet" for n in ("depressions", "cells") if table_path(folder, n) is None]
    return out + ([] if (folder / "meta.json").exists() else ["meta.json"])


def ids_to_int(ids):
    """H3 ids given as text, as an unsigned 64-bit integer array."""
    return np.fromiter((int(x, 16) for x in ids), dtype=np.uint64, count=len(ids))


def partition_key(cells, res):
    """Key of each cell's parent `PART_DEPTH` levels coarser (cells at resolution `res`).

    In an H3 id the resolution-r digit sits 3 * (15 - r) bits from the bottom, and the id's top
    bits (mode, resolution, base cell, coarser digits) come first. Cells sorted by id are
    therefore grouped by parent, and keys sort in the same order as the cells they hold.
    """
    shift = 3 * (15 - max(0, res - PART_DEPTH))
    return np.asarray(cells, dtype=np.uint64) >> np.uint64(shift)


PART_DTYPE = np.dtype([("rec", "<i8"), ("cell", "<u8"), ("depth", "<f8"), ("area", "<f8"),
                       ("volume", "<f8")])


class CellParts:
    """Per-(piece, cell) sums of a tiled run, grouped by partition, in memory or on disk.

    Each record holds a piece, a cell and the summed `fields` (by default depth, area and
    volume) followed by any `extra` ones. `spill_dir=None` keeps them in memory. Otherwise each
    partition is a file in `spill_dir`, appended to as tiles finish: every block is compressed
    (zlib, fastest level) and written after its length, which takes about a third of the space
    of the raw records. The folder is removed by `close`.
    """

    def __init__(self, res, spill_dir=None, extra=(), fields=("depth", "area", "volume")):
        self.res, self.dir = res, None if spill_dir is None else Path(spill_dir)
        self.extra = tuple(extra)                         # more summed values, by name
        self.fields = tuple(fields) + self.extra
        self.dtype = np.dtype([("rec", "<i8"), ("cell", "<u8")]
                              + [(name, "<f8") for name in self.fields])
        self._memory = {}
        self.n_records = 0
        if self.dir is not None:
            shutil.rmtree(self.dir, ignore_errors=True)   # leftovers of an interrupted run would count twice
            self.dir.mkdir(parents=True)

    def add(self, rec, cell, *values):
        """Append records: `values` are arrays in the order of `fields`."""
        if len(rec) == 0:
            return
        arr = np.empty(len(rec), dtype=self.dtype)
        arr["rec"], arr["cell"] = rec, cell
        for name, v in zip(self.fields, values):
            arr[name] = v
        key = partition_key(arr["cell"], self.res)
        order = np.argsort(key, kind="stable")
        arr, key = arr[order], key[order]
        starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        for s, e in zip(starts, np.r_[starts[1:], len(key)]):
            k = int(key[s])
            if self.dir is None:
                self._memory.setdefault(k, []).append(arr[s:e])
            else:
                blob = zlib.compress(arr[s:e].tobytes(), 1)
                with open(self.dir / f"{k:016x}.bin", "ab") as f:
                    f.write(len(blob).to_bytes(8, "little"))
                    f.write(blob)
        self.n_records += len(arr)

    def keys(self):
        """Partition keys in ascending order (the order of the cell ids they hold)."""
        if self.dir is None:
            return sorted(self._memory)
        return sorted(int(p.stem, 16) for p in self.dir.glob("*.bin"))

    def load(self, key):
        if self.dir is None:
            return np.concatenate(self._memory.pop(key))
        data = (self.dir / f"{key:016x}.bin").read_bytes()
        blocks, pos = [], 0
        while pos < len(data):
            n = int.from_bytes(data[pos:pos + 8], "little")
            blocks.append(np.frombuffer(zlib.decompress(data[pos + 8:pos + 8 + n]), dtype=self.dtype))
            pos += 8 + n
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=self.dtype)

    def close(self):
        self._memory.clear()
        if self.dir is not None and self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)


class Checkpoints:
    """Per-tile results of a tiled run saved on disk, so an interrupted run continues where it stopped.

    Each result is one .npz file of named arrays, written under a temporary name and renamed when
    complete, so a crash never leaves a half-written file under a real name. `manifest.json`
    records the run's settings; reopening the folder with different settings is refused, so pieces
    of two different runs can never be mixed.
    """

    def __init__(self, folder, settings):
        self.dir = Path(folder)
        settings = json.loads(json.dumps(settings))          # the form it takes once saved
        manifest = self.dir / "manifest.json"
        if manifest.exists():
            saved = json.loads(manifest.read_text())
            if saved != settings:
                changed = sorted(k for k in set(saved) | set(settings) if saved.get(k) != settings.get(k))
                raise ValueError(
                    f"{self.dir} holds a run started with different settings ({', '.join(changed)}). "
                    "Use the original settings to continue it, or delete that folder to start again.")
        else:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "manifest.json.tmp"
            tmp.write_text(json.dumps(settings, indent=2))
            os.replace(tmp, manifest)

    def _path(self, stage, k):
        return self.dir / f"{stage}_{k:06d}.npz"

    def has(self, stage, k):
        return self._path(stage, k).exists()

    def count(self, stage):
        return sum(1 for _ in self.dir.glob(f"{stage}_*.npz"))

    def save(self, stage, k, arrays):
        tmp = self.dir / f"tmp_{stage}_{k:06d}.npz"
        np.savez_compressed(tmp, **arrays)          # about a third of the size, 0.4 s per tile
        os.replace(tmp, self._path(stage, k))

    def load(self, stage, k, keys=None):
        with np.load(self._path(stage, k), allow_pickle=False) as f:
            return {n: f[n] for n in (f.files if keys is None else keys)}

    def save_json(self, name, data):
        tmp = self.dir / f"{name}.json.tmp"
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.dir / f"{name}.json")

    def load_json(self, name):
        path = self.dir / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def remove(self):
        shutil.rmtree(self.dir, ignore_errors=True)
