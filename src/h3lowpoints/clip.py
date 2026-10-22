"""Keep the part of a saved result that lies inside a boundary: a country, a state, a basin.

    from h3lowpoints.clip import clip_result
    clip_result("conus_30m", "us48.gpkg", "conus_30m_us")      # or a shapely geometry

    h3lowpoints clip conus_30m --boundary us48 --out conus_30m_us

Clipping happens after the analysis, so the hydrology stays exact: water still crossed the
boundary while the depressions were found, and no artificial outlet was made along it.

Membership is decided per H3 cell: a cell is inside when its centre is, and a depression is kept
when the cell of its lowest point (`h3_lowpoint`) is. The clipped tables therefore stay
consistent with each other (every kept lowest point is in a kept cell, and `n_lowpoints` is
unchanged), but a depression that straddles the boundary keeps only its cells on one side. The
totals of the two tables then differ slightly, so a clipped result records `meta["clip"]` and
`verify` skips the checks that compare those totals.
"""
from pathlib import Path

import h3
import numpy as np

from .store import (CHUNK_ROWS, TableWriter, check_format, ids_to_int, iter_table, read_meta,
                    read_table, remove_other_formats, table_path, write_meta)


def load_boundary(boundary):
    """A shapely geometry in longitude/latitude from a geometry or a vector file (any CRS)."""
    if hasattr(boundary, "geom_type"):
        return boundary
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Reading a boundary file needs geopandas: "
                          "pip install 'h3-lowpoints[sources]'") from exc
    try:
        frame = gpd.read_file(str(boundary))
    except Exception as exc:              # each I/O engine raises its own error types
        raise ValueError(f"could not read {boundary}: {exc}") from exc
    if frame.crs is None:
        raise ValueError(f"{boundary} has no coordinate reference system")
    return frame.to_crs(4326).geometry.union_all()


def cells_inside(cells, geometry):
    """Boolean array: is each H3 cell's centre inside the geometry (longitude/latitude)?"""
    import shapely

    if len(cells) == 0:
        return np.zeros(0, dtype=bool)
    shapely.prepare(geometry)
    centres = np.array([h3.cell_to_latlng(c) for c in cells])
    return shapely.contains_xy(geometry, centres[:, 1], centres[:, 0])


def clip_result(folder, boundary, out_dir, *, format=None, name=None, rows=CHUNK_ROWS):
    """Write the part of the result in `folder` inside `boundary` to `out_dir`. Returns out_dir.

    boundary: a shapely geometry in longitude/latitude, or a vector file (GeoPackage, GeoJSON,
        Shapefile, any CRS).
    format: "csv" or "parquet"; by default the format of the input.
    name: how `meta["clip"]` names the boundary (default: the file name).
    The cell table is read and written `rows` at a time, so a result of any size can be clipped.
    """
    folder, out = Path(folder), Path(out_dir)
    if folder.resolve() == out.resolve():
        raise ValueError("write the clipped result to another folder")
    fmt = format or table_path(folder, "cells").suffix.lstrip(".")
    check_format(fmt)
    geometry = load_boundary(boundary)
    out.mkdir(parents=True, exist_ok=True)
    remove_other_formats(out, fmt)
    kept_ids, n_cells = [], 0
    with TableWriter(out / f"cells.{fmt}", fmt) as w:
        empty = None
        for chunk in iter_table(folder, "cells", rows=rows):
            n_cells += len(chunk)
            inside = cells_inside(list(chunk["h3"]), geometry)
            kept = chunk[inside]
            empty = chunk.iloc[:0]
            if len(kept):
                w.write(kept.reset_index(drop=True))
                kept_ids.append(ids_to_int(list(kept["h3"])))
        w.close(empty=empty)
        n_kept_cells = w.rows
    kept_ids = np.sort(np.concatenate(kept_ids)) if kept_ids else np.zeros(0, dtype=np.uint64)

    deps = read_table(folder, "depressions")
    low = ids_to_int(list(deps["h3_lowpoint"]))
    at = np.minimum(np.searchsorted(kept_ids, low), max(len(kept_ids) - 1, 0))
    keep = (kept_ids[at] == low) if len(kept_ids) else np.zeros(len(deps), dtype=bool)
    kept_deps = deps[keep].reset_index(drop=True)
    with TableWriter(out / f"depressions.{fmt}", fmt) as w:
        for start in range(0, len(kept_deps), CHUNK_ROWS):
            w.write(kept_deps.iloc[start:start + CHUNK_ROWS])
        w.close(empty=deps.iloc[:0])
    if table_path(folder, "catchments"):
        # a kept depression keeps its whole catchment, also where it reaches beyond the boundary
        wanted = np.sort(kept_deps["id"].to_numpy())
        with TableWriter(out / f"catchments.{fmt}", fmt) as w:
            empty = None
            for chunk in iter_table(folder, "catchments", rows=rows):
                empty = chunk.iloc[:0]
                ids = chunk["id"].to_numpy()
                at = np.minimum(np.searchsorted(wanted, ids), max(len(wanted) - 1, 0))
                hit = wanted[at] == ids if len(wanted) else np.zeros(len(ids), dtype=bool)
                if hit.any():
                    w.write(chunk[hit].reset_index(drop=True))
            w.close(empty=empty)

    meta = read_meta(folder)
    meta["clip"] = {
        "boundary": name or (Path(boundary).name if not hasattr(boundary, "geom_type") else "geometry"),
        "rule": "cells whose centre is inside; depressions whose lowest point's cell is kept",
        "depressions_before": len(deps), "cells_before": n_cells}
    meta["n_depressions"] = len(kept_deps)
    meta["n_edge"] = int(kept_deps["edge"].sum()) if "edge" in kept_deps else meta.get("n_edge")
    meta["output"] = {"format": fmt, "n_cells": n_kept_cells}
    write_meta(out, meta)
    return out
