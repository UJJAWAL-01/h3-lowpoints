"""Drained or landlocked: does a mapped channel or a road let a depression's water out?

    from h3lowpoints.drainage import drainage_flags
    from h3lowpoints.susceptibility import write_susceptibility
    flags = drainage_flags("results", streams="streams.gpkg", canals="ditches.gpkg",
                           roads="roads.gpkg", land_cover="nlcd_classes.tif")
    write_susceptibility("results", flags)        # added to the susceptibility table

    h3lowpoints drainage results --nhd --tiger --land-cover nlcd_classes.tif

An elevation model shows every closed hollow, but many are drained in reality: a canal, a ditch
or a stream runs through them, or the rim they overflow at is a road embankment with a culvert
under it that the model cannot see. Landlocked depressions, where water truly has no way out, are
the ones that matter for waterlogging. From mapped lines (any line files; in the US, NHD for
streams and canals and Census TIGER for roads, see `sources.nhd` and `sources.roads`):

  land_cover     land cover class at the lowest point, from a raster of classes (0: no data,
                 or no raster given)
  stream_m       distance from the lowest point to the nearest stream or river line
  canal_m        the same for canals, ditches, conduits and pipelines
  channel_share  share of the depression's area in H3 cells that a stream or canal line crosses
                 (needs details=True)
  spill_road_m   distance from the spill point to the nearest road (needs catchments=True)
  drained        stream_m or canal_m at most near_m, or spill_road_m at most near_m (and, if
                 min_share is given, channel_share at least min_share)
  landlocked     not drained

Distances beyond `max_distance_m`, or to lines not given, are NaN; a NaN never makes a depression
drained, so `landlocked` means "no sign of drainage in the lines given". Storm drains and pipes
are not in these maps: in cities, many depressions flagged landlocked are drained by inlets.
"""
from pathlib import Path

import h3
import numpy as np
import pandas as pd

from .pipeline import Result, _package_version
from .store import CHUNK_ROWS, ids_to_int, iter_table, missing_files, read_meta, table_path

DRAINAGE_COLUMNS = ["land_cover", "stream_m", "canal_m", "channel_share", "spill_road_m",
                    "drained", "landlocked"]
NEAR_M = 30.0                   # a line this close to the lowest or spill point drains it
MAX_DISTANCE_M = 1000.0         # distances beyond this are not reported
# highways and ramps (TIGER MTFCC primary road, secondary road, ramp): at a spill point they are
# a sign of drainage far beyond chance; any road is not, in cities (docs/susceptibility.md)
MAJOR_ROADS = ("S1100", "S1200", "S1630")


def _geopandas():
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Drainage flags need geopandas: pip install 'h3-lowpoints[sources]'") from exc
    return gpd


def _lines(source, crs):
    """Line geometries (single lines) of a file, GeoDataFrame or GeoSeries, in `crs`; or None."""
    if source is None:
        return None
    gpd = _geopandas()
    import shapely

    if isinstance(source, (str, Path)):
        source = gpd.read_file(source)
    if source.crs is None:
        raise ValueError("lines without a coordinate reference system")
    geoms = source.to_crs(crs).geometry.values if hasattr(source, "geometry") else source.to_crs(crs).values
    parts = shapely.get_parts(np.asarray(geoms))
    return parts[shapely.get_type_id(parts) == 1]                     # LineStrings only


def _nearest(points, lines, max_distance):
    """Distance from each point to the nearest line, NaN beyond max_distance or with no lines."""
    import shapely

    out = np.full(len(points), np.nan)
    if lines is None or len(lines) == 0 or len(points) == 0:
        return out
    tree = shapely.STRtree(lines)
    (at, _), dist = tree.query_nearest(points, max_distance=max_distance, return_distance=True,
                                       all_matches=False)
    out[at] = dist
    return out


def cells_along(lines, crs, res, step=None, batch=2_000_000):
    """H3 cells at resolution `res` that lines (in projected `crs`, metres) pass through, found
    by points every `step` metres along them (default a third of the cells' edge). Lines are
    followed about `batch` points at a time, so a state's millions of lines fit in memory.
    Returns the cells as sorted unique integers."""
    import shapely
    from pyproj import Transformer

    from .h3agg.aggregate import h3_ids

    if lines is None or len(lines) == 0:
        return np.zeros(0, dtype=np.uint64)
    lines = np.asarray(lines)
    step = step or h3.average_hexagon_edge_length(res, unit="m") / 3
    to_lonlat = Transformer.from_crs(crs, 4326, always_xy=True)
    length = shapely.length(lines)
    n = np.maximum(np.ceil(length / step).astype(np.int64) + 1, 2)
    ends = np.cumsum(n)
    found, start = [], 0
    while start < len(lines):
        stop = max(start + 1, int(np.searchsorted(ends, (ends[start - 1] if start else 0) + batch)))
        stop = min(stop, len(lines))
        k = n[start:stop]
        which = np.repeat(np.arange(start, stop), k)
        first = np.repeat(np.cumsum(k) - k, k)
        along = (np.arange(k.sum()) - first) / (n[which] - 1) * length[which]
        pts = shapely.line_interpolate_point(lines[which], along)
        lon, lat = to_lonlat.transform(shapely.get_x(pts), shapely.get_y(pts))
        found.append(np.unique(h3_ids(lat, lon, res)))
        start = stop
    return np.unique(np.concatenate(found))


def in_sorted(values, sorted_values):
    """Whether each of `values` is in the sorted array `sorted_values` (by binary search)."""
    if len(sorted_values) == 0:
        return np.zeros(len(values), dtype=bool)
    at = np.minimum(np.searchsorted(sorted_values, values), len(sorted_values) - 1)
    return sorted_values[at] == values


def _sample_classes(raster, lat, lon):
    """Integer value of a raster at each point (0 outside it or where it has no data)."""
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import Window

    out = np.zeros(len(lat), dtype=np.int64)
    if len(lat) == 0:
        return out
    with rasterio.open(raster) as src:
        x, y = Transformer.from_crs(4326, src.crs, always_xy=True).transform(lon, lat)
        col, row = ~src.transform @ (np.asarray(x), np.asarray(y))
        row, col = np.floor(row).astype(np.int64), np.floor(col).astype(np.int64)
        inside = (row >= 0) & (row < src.height) & (col >= 0) & (col < src.width)
        if not inside.any():
            return out
        r0, r1 = row[inside].min(), row[inside].max() + 1          # one window around them all
        c0, c1 = col[inside].min(), col[inside].max() + 1
        band = np.ma.filled(src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0), masked=True), 0)
        out[inside] = band[row[inside] - r0, col[inside] - c0]
    return out


def _tables(result, rows):
    """(depressions, depression_cells chunks or None, meta)."""
    columns = ["id", "lat", "lon", "spill_lat", "spill_lon", "area_m2"]
    if isinstance(result, Result) and result.depressions is not None:
        deps = result.depressions
        chunks = None if result.depression_cells is None else (lambda: [result.depression_cells])
        return deps[[c for c in columns if c in deps]], chunks, result.meta
    folder = Path(result.path if isinstance(result, Result) else result)
    missing = missing_files(folder)
    if missing:
        raise FileNotFoundError(f"{folder} is missing {', '.join(missing)}")
    deps = pd.concat(list(iter_table(folder, "depressions", rows, columns=columns)), ignore_index=True)
    chunks = (None if table_path(folder, "depression_cells") is None
              else (lambda: iter_table(folder, "depression_cells", rows,
                                       columns=["id", "h3", "area_m2"])))
    return deps, chunks, read_meta(folder)


def local_crs(lat, lon):
    """A UTM zone (metres) for points around (lat, lon)."""
    gpd = _geopandas()
    from shapely.geometry import Point

    return gpd.GeoSeries([Point(float(np.mean(lon)), float(np.mean(lat)))], crs=4326).estimate_utm_crs()


def drainage_flags(result, *, streams=None, canals=None, roads=None, land_cover=None,
                   near_m=NEAR_M, min_share=None, max_distance_m=MAX_DISTANCE_M, crs=None,
                   rows=CHUNK_ROWS):
    """One row per depression: land cover, distances to mapped channels and roads, and whether
    it is drained or landlocked. See the module text and docs/susceptibility.md.

    result          a `Result` or its folder
    streams, canals, roads   line files (any CRS), GeoDataFrames or GeoSeries; at least one
    land_cover      a raster of land cover classes (such as NLCD), read at each lowest point
    near_m, min_share        the rule for `drained` (min_share None: channel_share is reported
                    but not used)
    max_distance_m  distances beyond this are NaN
    crs             projected CRS (metres) to measure in; default the UTM zone of the result
    """
    if streams is None and canals is None and roads is None:
        raise ValueError("give at least one of streams, canals or roads")
    if near_m < 0 or (min_share is not None and not 0 < min_share <= 1) or max_distance_m < near_m:
        raise ValueError("need near_m >= 0, 0 < min_share <= 1 (or None) and max_distance_m >= near_m")
    import shapely

    deps, cell_chunks, meta = _tables(result, rows)
    if len(deps) == 0:
        out = pd.DataFrame({"id": deps["id"]}, columns=["id"] + DRAINAGE_COLUMNS)
        out.attrs["settings"] = {"drainage": {}}
        return out
    crs = crs or local_crs(deps["lat"], deps["lon"])
    from pyproj import Transformer

    to_crs = Transformer.from_crs(4326, crs, always_xy=True)
    low = shapely.points(*to_crs.transform(deps["lon"].to_numpy(), deps["lat"].to_numpy()))
    stream_lines, canal_lines, road_lines = (_lines(s, crs) for s in (streams, canals, roads))
    out = pd.DataFrame({"id": deps["id"].to_numpy()})
    out["land_cover"] = (_sample_classes(land_cover, deps["lat"].to_numpy(), deps["lon"].to_numpy())
                         if land_cover is not None else 0)
    out["stream_m"] = _nearest(low, stream_lines, max_distance_m)
    out["canal_m"] = _nearest(low, canal_lines, max_distance_m)

    share = np.full(len(deps), np.nan)
    channels = [g for g in (stream_lines, canal_lines) if g is not None]
    if channels and cell_chunks is not None:
        crossed = cells_along(np.concatenate(channels), crs, meta["params"]["res"])
        ids = deps["id"].to_numpy()
        by_id = np.argsort(ids)
        wet, total = np.zeros(len(ids)), np.zeros(len(ids))
        for c in cell_chunks():
            if len(c) == 0:
                continue
            at = np.minimum(np.searchsorted(ids[by_id], c["id"].to_numpy()), len(ids) - 1)
            hit = ids[by_id][at] == c["id"].to_numpy()
            pos = by_id[at[hit]]
            area = c["area_m2"].to_numpy(dtype=float)[hit]
            cells = ids_to_int(list(c["h3"][hit]))
            inside = in_sorted(cells, crossed)
            np.add.at(total, pos, area)
            np.add.at(wet, pos, np.where(inside, area, 0.0))
        with np.errstate(invalid="ignore", divide="ignore"):
            share = np.where(total > 0, wet / total, np.nan)
    out["channel_share"] = share

    if road_lines is not None and "spill_lat" in deps:
        spill = shapely.points(*to_crs.transform(deps["spill_lon"].to_numpy(),
                                                 deps["spill_lat"].to_numpy()))
        out["spill_road_m"] = _nearest(spill, road_lines, max_distance_m)
    else:
        out["spill_road_m"] = np.nan
    out["drained"] = drained_rule(out, near_m, min_share)
    out["landlocked"] = ~out["drained"]
    out.attrs["settings"] = {"drainage": {
        "streams": _describe(streams), "canals": _describe(canals), "roads": _describe(roads),
        "land_cover": _describe(land_cover), "near_m": near_m, "min_share": min_share,
        "max_distance_m": max_distance_m, "crs": str(crs),
        "channel_share": "measured" if channels and cell_chunks is not None else
        "not measured (needs details=True and stream or canal lines)",
        "package_version": _package_version()}}
    return out


def drained_rule(table, near_m, min_share=None):
    """The `drained` column from the distance (and, with min_share, share) columns; NaN never
    drains."""
    with np.errstate(invalid="ignore"):
        drained = ((table["stream_m"] <= near_m) | (table["canal_m"] <= near_m)
                   | (table["spill_road_m"] <= near_m))
        if min_share is not None:
            drained |= table["channel_share"] >= min_share
        return drained.to_numpy(dtype=bool)


def _describe(source):
    if source is None:
        return None
    if isinstance(source, (str, Path)):
        return Path(source).name
    return f"given in memory ({len(source):,} features)"
