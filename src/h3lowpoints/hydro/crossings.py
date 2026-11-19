"""Road-crossing fix: cut road and railway embankments where a stream passes under them.

An elevation model sees a road embankment but not the culvert or bridge opening under it, so the
embankment shows as a dam and the ground behind it as a depression. The fix works in three steps:

1. **Cuts** (`make_cuts`): every point where an embankment line meets a stream line, and the
   stream line up to `radius_m` each side of it (two short lines, side a and side b), measured
   in metres along the stream. They depend on the lines only, not on any grid.
2. **Pixels** (`trace_cuts`): the pixels each side passes over, on a given grid, in order,
   sampled every third of a pixel (an 8-connected path).
3. **The cut** (`cut_levels`, `apply_cuts`): each cut's level is the higher of the lowest valid
   ground on its two sides ("stream level"), and every valid pixel of the cut is lowered to it:

       level = max(lowest ground on side a, lowest ground on side b)
       ground = min(ground, level)        on every pixel of the cut

Ground is only ever lowered, never raised, and never below the stream's own lowest ground on
either side, so the cut cannot drain a basin with no lower way out. Where cuts share a pixel the
lowest result wins, so the order does not matter. A cut with a side holding no valid pixel (the
stream starts or ends at the crossing) is not made.

Because the cuts are traced on the whole grid and the levels are minima, a region analyzed in
tiles (`analyze_tiled`) cuts exactly the same pixels to exactly the same levels.
"""
import numpy as np
import pandas as pd

STEP = 1 / 3          # sampling step along a line, in pixels
EARTH_M_PER_DEG_LAT = 110_574.0
EARTH_M_PER_DEG_LON = 111_320.0
CUT_COLUMNS = ["x", "y", "a", "b"]


def _geoms(lines):
    """Shapely lines (LineString or MultiLineString) from a GeoSeries, GeoDataFrame or iterable."""
    geoms = getattr(lines, "geometry", lines)
    out = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        out.extend(getattr(g, "geoms", [g]))
    return [g for g in out if g.geom_type == "LineString" and len(g.coords) >= 2]


def crossing_points(barriers, streams):
    """Points where barrier lines meet stream lines: a list of (point, stream index)."""
    from shapely import STRtree

    if not barriers or not streams:
        return []
    tree = STRtree(barriers)
    out = []
    for i, s in enumerate(streams):
        for j in tree.query(s, predicate="intersects"):
            hit = s.intersection(barriers[int(j)])
            for part in getattr(hit, "geoms", [hit]):
                if part.is_empty:
                    continue
                # a stream running along a road for a stretch: cut at the middle of the overlap
                point = part if part.geom_type == "Point" else part.interpolate(0.5, normalized=True)
                out.append((point, i))
    return out


def make_cuts(barriers, streams, crs, radius_m=100.0):
    """The cuts of all crossings: a table with the crossing (x, y) and its two sides (a, b) as
    lines, all in `crs` (the lines' CRS). Lengths are measured in metres: in a geographic CRS
    on a local flat approximation around each crossing (exact enough over a few hundred metres),
    otherwise in the CRS's own unit."""
    from shapely.affinity import affine_transform
    from shapely.ops import substring

    from ..geometry import crs_info

    geographic, unit = crs_info(crs)
    bar, st = _geoms(barriers), _geoms(streams)
    rows = []
    for point, i in crossing_points(bar, st):
        s = st[i]
        if geographic:
            kx = EARTH_M_PER_DEG_LON * np.cos(np.radians(point.y))
            ky = EARTH_M_PER_DEG_LAT
        else:
            kx = ky = unit
        to_m = [kx, 0, 0, ky, -kx * point.x, -ky * point.y]
        back = [1 / kx, 0, 0, 1 / ky, point.x, point.y]
        sm = affine_transform(s, to_m)
        at = sm.project(affine_transform(point, to_m))
        sides = []
        for a, b in ((max(at - radius_m, 0.0), at), (at, min(at + radius_m, sm.length))):
            sides.append(affine_transform(substring(sm, a, b), back) if b - a > 0 else None)
        rows.append({"x": point.x, "y": point.y, "a": sides[0], "b": sides[1]})
    out = pd.DataFrame(rows, columns=CUT_COLUMNS)
    out.attrs["radius_m"] = radius_m
    return out


def trace_lines(lines, transform, shape):
    """The pixels lines pass over on a grid: (line index, rows, cols), in order along each line,
    each pixel once per line, inside the grid only. Vectorised over all lines."""
    import shapely
    from shapely.affinity import affine_transform

    inv = ~transform
    m = [inv.a, inv.b, inv.d, inv.e, inv.c, inv.f]
    geoms = []
    index = []
    for k, g in enumerate(lines):
        if g is None or g.is_empty:
            continue
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "LineString" and len(part.coords) >= 2:
                geoms.append(affine_transform(part, m))       # in (col, row) pixel units
                index.append(k)
    if not geoms:
        z = np.zeros(0, np.int64)
        return z, z, z
    geoms = np.array(geoms, dtype=object)
    lengths = shapely.length(geoms)
    n = np.maximum(np.ceil(lengths / STEP).astype(np.int64), 1) + 1      # samples per line
    which = np.repeat(np.arange(len(geoms)), n)
    first = np.repeat(np.cumsum(n) - n, n)
    t = (np.arange(n.sum()) - first) / np.repeat(n - 1, n)
    pts = shapely.line_interpolate_point(geoms[which], t * lengths[which])
    cols = np.floor(shapely.get_x(pts)).astype(np.int64)
    rows = np.floor(shapely.get_y(pts)).astype(np.int64)
    line = np.asarray(index, dtype=np.int64)[which]
    keep = np.r_[True, (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1]) | (which[1:] != which[:-1])]
    rows, cols, line = rows[keep], cols[keep], line[keep]
    inside = (rows >= 0) & (rows < shape[0]) & (cols >= 0) & (cols < shape[1])
    return line[inside], rows[inside], cols[inside]


def trace_cuts(cuts, transform, shape):
    """The pixels of every cut on a grid: dict of arrays `cut` (row of `cuts`), `side` (0 = a,
    1 = b), `row`, `col`."""
    n = len(cuts)
    sides = list(cuts["a"]) + list(cuts["b"])
    line, rows, cols = trace_lines(sides, transform, shape)
    return {"cut": line % n if n else line, "side": (line >= n).astype(np.int8) if n else line,
            "row": rows, "col": cols}


def side_minima(trace, ground, valid, n_cuts, offset=(0, 0)):
    """(n_cuts, 2) lowest valid ground on each side of each cut, from the traced pixels that lie
    in `ground` (a window starting at `offset` = (row, col) of the whole grid); +inf where a side
    has no valid pixel there. Minima of windows combine with `np.minimum`."""
    out = np.full((n_cuts, 2), np.inf)
    r = trace["row"] - offset[0]
    c = trace["col"] - offset[1]
    ok = (r >= 0) & (r < ground.shape[0]) & (c >= 0) & (c < ground.shape[1])
    r, c = r[ok], c[ok]
    good = valid[r, c]
    np.minimum.at(out, (trace["cut"][ok][good], trace["side"][ok][good]), ground[r[good], c[good]])
    return out


def cut_levels(minima):
    """Each cut's level from its sides' minima: the higher of the two, NaN when a side has no
    valid pixel (no cut)."""
    level = minima.max(axis=1)
    return np.where(np.isfinite(level), level, np.nan)


def apply_cuts(ground, valid, trace, level, offset=(0, 0)):
    """Ground with every valid pixel of every cut lowered to the cut's level (never raised)."""
    out = ground.astype(np.float64, copy=True)
    r = trace["row"] - offset[0]
    c = trace["col"] - offset[1]
    lv = level[trace["cut"]]
    ok = (r >= 0) & (r < out.shape[0]) & (c >= 0) & (c < out.shape[1]) & np.isfinite(lv)
    r, c, lv = r[ok], c[ok], lv[ok]
    good = valid[r, c]
    np.minimum.at(out, (r[good], c[good]), lv[good])
    return out


def breach(ground, invalid, transform, crs, cuts):
    """Ground with every embankment cut (see the module doc), for a grid held whole.

    cuts: from `make_cuts` (in the grid's CRS). Returns (cut ground, crossings table as a dict
    of arrays: x, y in the grid's CRS, level_m, lowered_m the largest lowering at that crossing,
    n_pixels).
    """
    valid = ~invalid
    trace = trace_cuts(cuts, transform, ground.shape)
    level = cut_levels(side_minima(trace, ground, valid, len(cuts)))
    out = apply_cuts(ground, valid, trace, level)
    made = np.isfinite(level)
    ok = valid[trace["row"], trace["col"]]
    cut = trace["cut"][ok]
    lowered = np.zeros(len(cuts))
    np.maximum.at(lowered, cut, np.maximum(ground[trace["row"][ok], trace["col"][ok]]
                                           - np.nan_to_num(level[cut], nan=np.inf), 0.0))
    n_px = np.bincount(cut, minlength=len(cuts))
    rec = {"x": cuts["x"].to_numpy(float)[made], "y": cuts["y"].to_numpy(float)[made],
           "level_m": level[made], "lowered_m": lowered[made], "n_pixels": n_px[made].astype(float)}
    return out, rec


def line_mask(lines, transform, shape):
    """Boolean raster, True on every pixel a line passes over."""
    mask = np.zeros(shape, dtype=bool)
    _, r, c = trace_lines(_geoms(lines), transform, shape)
    mask[r, c] = True
    return mask


class Crossings:
    """Everything the road-crossing fix needs, ready for any grid: the cuts (`make_cuts`), the
    bridge lines, their CRS, and a description (`info`) for the run's metadata. Make it once
    (`prepare`), for example state by state for a whole country, and reuse it."""

    def __init__(self, cuts, bridges, crs, info=None):
        self.cuts, self.bridges, self.crs = cuts, list(bridges), crs
        self.info = dict(info or {})

    def to_crs(self, crs):
        """The same crossings in another CRS (the cuts' lengths stay as measured)."""
        from pyproj import CRS, Transformer
        from shapely.ops import transform as reshape

        if CRS.from_user_input(crs) == CRS.from_user_input(self.crs):
            return self
        f = Transformer.from_crs(self.crs, crs, always_xy=True).transform
        cuts = self.cuts.copy()
        for side in ("a", "b"):
            cuts[side] = [None if g is None else reshape(f, g) for g in cuts[side]]
        if len(cuts):
            cuts["x"], cuts["y"] = f(cuts["x"].to_numpy(float), cuts["y"].to_numpy(float))
        return Crossings(cuts, [reshape(f, g) for g in self.bridges], crs, self.info)


def prepare(lines, crs, radius_m=100.0):
    """`Crossings` from road, railway and stream lines: OSM lines (`sources.osm.fetch_lines`), or
    a tuple (barriers, streams) or (barriers, streams, bridges) of line layers. Layers with a CRS
    are reprojected to `crs`; others are taken to be in it already."""
    if isinstance(lines, Crossings):
        return lines.to_crs(crs)
    if hasattr(lines, "columns") and {"highway", "waterway"} <= set(lines.columns):
        from ..sources.osm import split_lines

        parts = split_lines(lines)
    else:
        parts = tuple(lines)
        if len(parts) not in (2, 3):
            raise ValueError("breach_crossings takes OSM lines (sources.osm.fetch_lines), a "
                             "tuple (barriers, streams) or (barriers, streams, bridges), or "
                             "Crossings")
        parts = parts + (None,) * (3 - len(parts))

    def here(layer):
        if layer is None:
            return []
        if getattr(layer, "crs", None) is not None:
            return _geoms(layer.to_crs(crs))
        return _geoms(layer)
    barriers, streams, bridges = (here(x) for x in parts)
    info = {"radius_m": radius_m, "n_barriers": len(barriers), "n_streams": len(streams),
            "n_bridges": len(bridges), "lines": getattr(lines, "attrs", {}).get("extract")}
    return Crossings(make_cuts(barriers, streams, crs, radius_m), bridges, crs, info)
