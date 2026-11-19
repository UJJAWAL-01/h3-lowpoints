"""Flood evidence: was each depression seen flooded in real flood maps?

    from h3lowpoints import analyze
    from h3lowpoints.floods import flood_events
    from h3lowpoints.sources import gfm
    result = analyze("dem.tif")
    scenes = gfm.search(bbox, "2017-08-25", "2017-09-05", min_flooded_pixels=25_000)
    summary, seen = flood_events(result, scenes)

For each flood map (a GFM radar scene, or any coded raster of your own) every depression is
laid on the map's grid by nearest pixel, and its pixels are counted:

- **observed**: the map's observed pixels (coded 0 or 1) cover at least `OBSERVED_SHARE` (50%)
  of the depression's area. Pixels the map could not judge (coded 255: outside the image,
  masked, or normally water) are never counted as dry.
- **flooded**: observed, and flooded pixels are at least `FLOODED_SHARE` (10%) of its observed
  pixels, and at least `MIN_FLOODED_PIXELS` (2).

The thresholds are those of the study rules (section 4, method note B), fixed before any data was
looked at. A user flood map is a raster coded 1 flooded, 0 observed and dry, anything else not
observed; `sources.gfm.write_flood_map` writes GFM scenes in that form.

The same counts are made tile by tile for regions too big to hold (`FloodHook` with
`analyze_tiled`): each map pixel counts for the tile holding its centre, and the counts of a
depression's pieces add up, so the tables are the same.
"""
import numpy as np
import pandas as pd

from .sources import gfm

OBSERVED_SHARE = 0.5
FLOODED_SHARE = 0.10
MIN_FLOODED_PIXELS = 2
SUMMARY_COLUMNS = ["id", "scenes_covered", "scenes_observed", "scenes_flooded", "first_flooded",
                   "last_flooded", "max_flooded_share", "flooded_scenes"]
SEEN_COLUMNS = ["scene", "datetime", "id", "observed_share", "flooded_share", "observed", "flooded"]
LONG_FIELDS = ("total", "seen_px", "flooded_px", "seen_area")


def counts(labels, codes, ids, pixel_area=None):
    """(pixels, observed pixels, flooded pixels, observed area m2) of each depression id on a
    coded map whose pixels the labels already sit on (same shape). pixel_area: (rows,) m2 of the
    map's pixels by row (1 each when None)."""
    lab = labels.ravel()
    c = codes.ravel()
    n = int(max(np.max(ids, initial=0), lab.max(initial=0))) + 1
    lab = np.where((lab > 0) & (lab < n), lab, 0)
    seen = c != gfm.NOT_SEEN
    w = (np.ones(codes.shape) if pixel_area is None
         else np.broadcast_to(np.asarray(pixel_area, float)[:, None], codes.shape)).ravel()
    total = np.bincount(lab, minlength=n)
    seen_px = np.bincount(lab[seen], minlength=n)
    flooded = np.bincount(lab[c == gfm.FLOODED], minlength=n)
    seen_area = np.bincount(lab[seen], weights=w[seen], minlength=n)
    return total[ids], seen_px[ids], flooded[ids], seen_area[ids]


def judge(area_m2, seen_area_m2, seen_px, flooded_px):
    """(observed share, flooded share, observed, flooded) by the rules in the module doc. The
    observed share is the observed pixels' area over the depression's area (capped at 1)."""
    area = np.asarray(area_m2, dtype=float)
    seen_px = np.asarray(seen_px, dtype=float)
    flooded_px = np.asarray(flooded_px, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        observed_share = np.minimum(np.asarray(seen_area_m2, float) / area, 1.0)
        flooded_share = np.where(seen_px > 0, flooded_px / np.maximum(seen_px, 1), 0.0)
    observed = observed_share >= OBSERVED_SHARE
    flooded = observed & (flooded_px >= MIN_FLOODED_PIXELS) & (flooded_share >= FLOODED_SHARE)
    return observed_share, flooded_share, observed, flooded


def _lay(labels, transform, crs, codes, c_transform, c_crs):
    """Labels on the map's grid by nearest pixel; 0 where a map pixel's centre lies off the
    labels' grid, so each map pixel counts for at most one tile of a tiled run."""
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    out = np.full(codes.shape, -1, dtype="int32")
    reproject(labels.astype("int32"), out, src_transform=transform, src_crs=crs,
              dst_transform=c_transform, dst_crs=c_crs, resampling=Resampling.nearest,
              src_nodata=None, dst_nodata=-1, tolerance=0)
    return np.maximum(out, 0)


def _bounds(transform, shape):
    x0, y0 = transform @ (0, 0)
    x1, y1 = transform @ (shape[1], shape[0])
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _read_user_map(path, bounds, crs):
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(path) as src:
        b = transform_bounds(crs, src.crs, *bounds, densify_pts=21)
        win = gfm.covering_window(b, src.transform, src.width, src.height)
        if win is None:                              # the box misses the map
            return None, None, src.crs
        a = src.read(1, window=win)
        codes = np.where(a == 1, gfm.FLOODED, np.where(a == 0, gfm.DRY, gfm.NOT_SEEN))
        if src.nodata is not None and src.nodata in (0, 1):
            codes[a == src.nodata] = gfm.NOT_SEEN
        return codes.astype("uint8"), src.window_transform(win), src.crs


def _maps(scenes, advisory=False):
    """(jobs [(name, when, source)], skipped names): the maps to read, in order."""
    if isinstance(scenes, dict):
        return [(name, None, path) for name, path in scenes.items()], []
    rows = [row for _, row in scenes.iterrows()]
    skipped = [row["item"] for row in rows if gfm.missing_layers(row, advisory)]
    jobs = [(row["item"], row["datetime"], row) for row in rows
            if not gfm.missing_layers(row, advisory)]
    return jobs, skipped


def _load(job, bounds, crs, reader, advisory=False):
    _, when, src = job
    if when is None:
        return _read_user_map(src, bounds, crs)
    if reader is not None:
        return reader(src)
    return gfm.read_scene(src, bounds, crs=crs, advisory=advisory)


def map_counts(labels, transform, crs, ids, job, bounds, reader=None, advisory=False):
    """Counts of each depression on one map: (positions in ids, total, observed px, flooded px,
    observed area), only for depressions the map reaches. None when the map misses the box."""
    from .geometry import pixel_areas_m2

    codes, c_t, c_crs = _load(job, bounds, crs, reader, advisory)
    if codes is None:
        return None
    lab = _lay(labels, transform, crs, codes, c_t, c_crs)
    total, seen_px, fl_px, seen_area = counts(lab, codes, ids,
                                              pixel_areas_m2(c_t, c_crs, codes.shape[0]))
    k = np.flatnonzero(total > 0)
    return k, total[k], seen_px[k], fl_px[k], seen_area[k]


def _empty_long():
    return {"pos": np.zeros(0, np.int64), "map": np.zeros(0, np.int64),
            **{f: np.zeros(0) for f in LONG_FIELDS}}


def summarise(ids, area_m2, long, jobs, skipped=(), advisory=False):
    """(summary, seen) from counts per depression and map. long: dict of arrays `pos` (into
    ids), `map` (into jobs) and LONG_FIELDS, one row per depression and map that reaches it."""
    ids = np.asarray(ids)
    pos, m = np.asarray(long["pos"], np.int64), np.asarray(long["map"], np.int64)
    o_share, f_share, observed, flooded = judge(np.asarray(area_m2, float)[pos], long["seen_area"],
                                                long["seen_px"], long["flooded_px"])
    n = len(ids)
    covered = np.bincount(pos, minlength=n)
    n_obs = np.bincount(pos[observed], minlength=n)
    n_fl = np.bincount(pos[flooded], minlength=n)
    max_share = np.zeros(n)
    np.maximum.at(max_share, pos[observed], f_share[observed])
    label = np.array([str(when or name) for name, when, _ in jobs], dtype=object)
    names = [[] for _ in range(n)]
    for i, k in zip(pos[flooded], m[flooded]):
        names[i].append(label[k])
    summary = pd.DataFrame({
        "id": ids, "scenes_covered": covered, "scenes_observed": n_obs, "scenes_flooded": n_fl,
        "first_flooded": [min(v) if v else None for v in names],
        "last_flooded": [max(v) if v else None for v in names],
        "max_flooded_share": max_share,
        "flooded_scenes": [";".join(sorted(v)) for v in names]})
    order = np.lexsort((pos, m))                  # by map, then depression
    order = order[observed[order]]
    seen = pd.DataFrame({
        "scene": np.array([name for name, _, _ in jobs] or [None], dtype=object)[m[order]]
        if len(order) else np.array([], dtype=object),
        "datetime": np.array([when for _, when, _ in jobs] or [None], dtype=object)[m[order]]
        if len(order) else np.array([], dtype=object),
        "id": ids[pos[order]], "observed_share": o_share[order],
        "flooded_share": f_share[order], "observed": np.ones(len(order), bool),
        "flooded": flooded[order]})
    rule = {"observed_share": OBSERVED_SHARE, "flooded_share": FLOODED_SHARE,
            "min_flooded_pixels": MIN_FLOODED_PIXELS, "n_maps": len(jobs),
            "skipped_missing_layer": list(skipped), "advisory_flags_not_observed": bool(advisory)}
    whens = [when for _, when, _ in jobs if when is not None]
    if whens:
        rule["credit"] = gfm.credit(whens)
    summary.attrs["settings"] = seen.attrs["settings"] = rule
    return summary[SUMMARY_COLUMNS], seen[SEEN_COLUMNS]


def flood_events(result, scenes, *, reader=None, workers=1, advisory=False):
    """Per depression: in how many flood maps it was observed and flooded (module doc).

    result: a `Result` with labels (from `analyze`).
    scenes: a table from `sources.gfm.search` (one row per item), or a dict {name: path} of
        coded rasters of your own (1 flooded, 0 observed dry, anything else not observed).
    Returns (summary, seen):
      summary  one row per depression: scenes_covered (maps that reach any of its pixels),
               scenes_observed, scenes_flooded, first_flooded, last_flooded (dates or names),
               max_flooded_share, flooded_scenes (names joined by ";")
      seen     one row per observed depression and map: observed_share, flooded_share,
               observed, flooded (for scoring maps one by one)
      Catalogue items without one of the three layers cannot be judged and are skipped; they
      are listed in `attrs["settings"]["skipped_missing_layer"]`.
    workers: read this many maps at once (threads; reading is mostly waiting on the network).
        The answer does not change.
    advisory: GFM scenes only: also count pixels under GFM's advisory flags (snow, ice, dry
        soil, wind) as not observed. Off by default (the study's frozen rule does not use them).
    """
    from concurrent.futures import ThreadPoolExecutor

    if result.labels is None:
        raise ValueError("flood_events needs a result with labels (from analyze, not from disk)")
    deps = result.depressions
    ids = deps["id"].to_numpy()
    bounds = _bounds(result.transform, result.labels.shape)
    jobs, skipped = _maps(scenes, advisory)

    def one(k):
        got = map_counts(result.labels, result.transform, result.crs, ids, jobs[k], bounds, reader,
                         advisory)
        if got is None:
            return None
        p, *values = got
        return {"pos": p, "map": np.full(len(p), k), **dict(zip(LONG_FIELDS, values))}
    with ThreadPoolExecutor(max(1, int(workers))) as pool:
        parts = [x for x in pool.map(one, range(len(jobs))) if x is not None]
    long = ({k: np.concatenate([p[k] for p in parts]) for k in parts[0]} if parts
            else _empty_long())
    return summarise(ids, deps["area_m2"].to_numpy(float), long, jobs, skipped, advisory)


class FloodHook:
    """`analyze_tiled(hooks=[FloodHook(scenes)])`: `flood_events` for a region too big to hold,
    counted tile by tile. Adds the tables `flood_events` (the summary) and `flood_seen`, equal
    to what `flood_events` gives on the whole region."""

    name = "floods"
    wide = {}

    def __init__(self, scenes, *, workers=4, reader=None, advisory=False):
        self.jobs, self.skipped = _maps(scenes, advisory)
        self.workers, self.reader, self.advisory = workers, reader, advisory
        self.footprints = None
        if not isinstance(scenes, dict) and "geometry" in getattr(scenes, "columns", []):
            self.footprints = [row["geometry"] for _, row in scenes.iterrows()
                               if not gfm.missing_layers(row, advisory)]

    def settings(self):
        import hashlib

        names = ";".join(str(name) for name, _, _ in self.jobs)
        return {"hook": self.name, "n_maps": len(self.jobs), "advisory": bool(self.advisory),
                "maps_sha256": hashlib.sha256(names.encode()).hexdigest()}

    def tile(self, crs, box, transform, labels, labs):
        from concurrent.futures import ThreadPoolExecutor

        if len(labs) == 0:
            return {}, {}
        bounds = _bounds(transform, labels.shape)
        todo = list(range(len(self.jobs)))
        if self.footprints is not None:
            from rasterio.warp import transform_bounds
            from shapely.geometry import box as make_box

            ll = make_box(*transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21))
            todo = [k for k in todo if self.footprints[k].intersects(ll)]
        tile_labels = np.where(np.isin(labels, labs), labels, 0)

        def one(k):
            got = map_counts(tile_labels, transform, crs, labs, self.jobs[k], bounds, self.reader,
                             self.advisory)
            if got is None:
                return None
            p, *values = got
            return {"rec": p, "map": np.full(len(p), k), **dict(zip(LONG_FIELDS, values))}
        with ThreadPoolExecutor(max(1, int(self.workers))) as pool:
            parts = [x for x in pool.map(one, todo) if x is not None]
        if not parts:
            return {}, {}
        return {}, {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}

    def finish(self, out, piece_comp, id_of_comp, parts):
        ids = np.zeros(0, np.int64) if out is None else out["id"].to_numpy()
        area = np.zeros(0) if out is None else out["area_m2"].to_numpy(float)
        key = f"{self.name}__rec"
        pieces = [p for p in parts if key in p]
        long = _empty_long()
        if pieces:
            dep = id_of_comp[piece_comp[np.concatenate([p[key] for p in pieces])]]
            frame = pd.DataFrame({
                "id": dep, "map": np.concatenate([p[f"{self.name}__map"] for p in pieces]),
                **{f: np.concatenate([p[f"{self.name}__{f}"] for p in pieces])
                   for f in LONG_FIELDS}})
            frame = frame[frame["id"] >= 0].groupby(["id", "map"], as_index=False).sum()
            long = {"pos": np.searchsorted(ids, frame["id"].to_numpy()),
                    "map": frame["map"].to_numpy(), **{f: frame[f].to_numpy() for f in LONG_FIELDS}}
        summary, seen = summarise(ids, area, long, self.jobs, self.skipped, self.advisory)
        return {"flood_events": summary, "flood_seen": seen}
