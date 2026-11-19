"""Seen wet: how often satellites saw water in each depression, 1984 to 2021.

    from h3lowpoints import analyze
    from h3lowpoints.water import water_history

    res = analyze("dem.tif", min_depth=0.5)
    table = water_history(res)              # one row per depression

Each depression's pixels of the JRC Global Surface Water record (`sources.jrc`, 30 m, Landsat) are
counted year by year, and the depression gets a class: `permanent` (a pond or lake), `seasonal`
(wet in at least half the years), `some_years`, `never_seen_wet`, or `too_small` and
`too_few_views` when the record cannot tell. Every threshold is a constant below and is described
in docs/water-history.md.

Two ways to find a depression's pixels:

- **outline** (`water_history(result)` with a result held in memory): the JRC pixels whose
  centres fall inside the depression, from its pixel labels.
- **cells** (`water_history_by_cells`): from the depression x cell table of a saved result
  (`details=True`), each H3 cell's JRC values weighted by the depression's area in the cell. No
  labels are needed, so it works on saved national results; it is an approximation, because
  water anywhere in a cell is spread over the whole cell.

**Clear years are judged per area, not per depression.** The yearly files hold 0 both for "no
data" and for every pixel outside the water extent of the period it was processed in, so a 0 at
one pixel does not say whether the satellite saw it. A missing year shows instead as a whole area
without data: a year is clear for a depression when at least half of the extent pixels of its H3
cell (resolution 5, else 4, else 3, at its lowest point) holding data is at least half its median
over the years (and at least 10 %). The median, not a fixed share, because pixels first mapped as
water after 2014 hold 0 in every earlier year. In a clear year a 0 inside the depression counts as
not water.

The record has blind spots, stated in every table's settings: Landsat passes about every 16 days
and does not see through cloud, so short floods are mostly missed ("never seen wet" is not "never
wet"), and pixels are 30 m, so small street sags are below what it can see.
"""
import math

import h3
import numpy as np
import pandas as pd
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds

from .h3agg.aggregate import h3_ids, pixel_centers
from .sources import jrc

# The rules (frozen in the phase 7 study, with deviations 1 and 2).
MIN_PIXELS = 3              # fewer JRC pixels than this: too small to tell
CLEAR_SHARE = 0.5           # a year is clear when the share of the area's extent pixels holding data is at
CLEAR_FLOOR = 0.1           # least this times its median over the years, and at least this floor
WET_SHARE = 0.10            # a clear year is wet when water covers at least this share of the pixels ...
WET_MIN_AREA_M2 = 10_000    # ... or at least this area (1 ha) ...
WET_MIN_PIXELS = 2          # ... and in either case at least this many pixels
MIN_CLEAR_YEARS = 20        # fewer clear years than this: too few views
PERMANENT_SHARE = 0.5       # permanent water on at least this share of the pixels ...
PERMANENT_YEARS = 0.8       # ... in at least this share of the clear years: permanent
SEASONAL_YEARS = 0.5        # wet in at least this share of the clear years: seasonal
AREA_RES = (5, 4, 3)        # H3 resolutions tried, in order, for the views of a depression's area
TREND_MIN_YEARS = 10        # clear years needed for a trend

CLASSES = ("too_small", "too_few_views", "permanent", "seasonal", "some_years", "never_seen_wet")
WATER_COLUMNS = ["id", "jrc_pixels", "extent_pixels", "clear_years", "views_res", "wet_years",
                 "first_wet_year", "last_wet_year", "permanent_years", "max_wet_share",
                 "occurrence_mean", "ever_wet_share", "wettest_month", "trend_per_decade",
                 "trend_p", "water_class"]


def pixel_area_m2(lat):
    """Area of a JRC pixel (0.00025 degrees) centred at latitude `lat`, in m2 (spherical, the
    accuracy the rules need)."""
    lat = np.radians(np.asarray(lat, dtype=float))
    return (jrc.PIXEL * 111_320.0 * np.cos(lat)) * (jrc.PIXEL * 110_574.0)


class Counts:
    """Per-depression counts, year by year: the input of the rules.

    n_pix, ext_n, occ_sum: (D,) pixels, pixels inside the maximum extent, sum of occurrence.
    mon_sum: (D, 12) sums of monthly recurrence over the extent pixels.
    water, permanent: (Y, D) per year, pixels with water (seasonal or permanent) and with
    permanent water. Counts may be fractional (cells method).
    """

    def __init__(self, ids, n_years):
        d = len(ids)
        self.ids = np.asarray(ids)
        self.n_pix = np.zeros(d)
        self.ext_n = np.zeros(d)
        self.occ_sum = np.zeros(d)
        self.mon_sum = np.zeros((d, 12))
        self.water = np.zeros((n_years, d))
        self.permanent = np.zeros((n_years, d))


def _years(years):
    years = tuple(int(y) for y in (years if years is not None else jrc.YEARS))
    bad = [y for y in years if y not in jrc.YEARS]
    if bad or not years:
        raise ValueError(f"years must lie in {jrc.YEARS[0]}..{jrc.YEARS[-1]}")
    return years


def apply_rules(counts, years, clear, views_res, pixel_area):
    """The table from counts.

    clear: (Y, D) bool, the clear years of each depression's area. views_res: (D,) the H3
    resolution they were judged at, 0 where no area had extent pixels (views unknown).
    pixel_area: (D,) m2 of one JRC pixel at each depression.
    """
    years = np.asarray(years)
    n_years = len(years)
    c = counts
    known = np.asarray(views_res) > 0
    clear = np.asarray(clear, dtype=bool) & known[None, :]
    min_px = np.maximum(WET_MIN_PIXELS,
                        np.minimum(WET_SHARE * c.n_pix, WET_MIN_AREA_M2 / pixel_area))[None, :]
    wet = clear & (c.water >= min_px - 1e-9)
    # a permanent year is also a wet year; this only matters for 1-2 pixel (too_small)
    # depressions, which can reach the permanent share but not the 2-pixel wet minimum
    perm = wet & (c.permanent >= PERMANENT_SHARE * c.n_pix[None, :] - 1e-9)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(clear, c.water / np.where(c.n_pix > 0, c.n_pix, np.nan)[None, :], np.nan)

    clear_years = np.where(known, clear.sum(axis=0), np.nan)
    wet_years = wet.sum(axis=0)
    perm_years = perm.sum(axis=0)
    any_wet = wet.any(axis=0)
    first = np.where(any_wet, years[np.argmax(wet, axis=0)], -1)
    last = np.where(any_wet, years[n_years - 1 - np.argmax(wet[::-1], axis=0)], -1)
    with np.errstate(invalid="ignore", divide="ignore", all="ignore"):
        max_share = np.where(clear.any(axis=0), np.nanmax(np.where(clear, share, -1), axis=0), np.nan)
        occurrence = np.where(c.n_pix > 0, c.occ_sum / c.n_pix, np.nan)
        ever = np.where(c.n_pix > 0, c.ext_n / c.n_pix, np.nan)
        own = c.ext_n > 0
        mon = np.where(own[:, None], c.mon_sum / np.where(own, c.ext_n, 1)[:, None], 0.0)
    wettest = np.where(own & (mon.max(axis=1) > 0), np.argmax(mon, axis=1) + 1, 0)

    slope, p = trends(share, years, any_wet & (clear.sum(axis=0) >= TREND_MIN_YEARS))

    cls = np.full(len(c.ids), "never_seen_wet", dtype=object)
    cy = np.nan_to_num(clear_years, nan=np.inf)       # unknown views are not "too few"
    cls[wet_years >= 1] = "some_years"
    cls[(wet_years >= 1) & (wet_years >= SEASONAL_YEARS * cy)] = "seasonal"
    cls[(perm_years >= 1) & (perm_years >= PERMANENT_YEARS * cy)] = "permanent"
    cls[cy < MIN_CLEAR_YEARS] = "too_few_views"
    cls[c.n_pix < MIN_PIXELS - 1e-9] = "too_small"

    return pd.DataFrame({
        "id": c.ids, "jrc_pixels": c.n_pix, "extent_pixels": c.ext_n, "clear_years": clear_years,
        "views_res": np.asarray(views_res, dtype=int), "wet_years": wet_years,
        "first_wet_year": first, "last_wet_year": last, "permanent_years": perm_years,
        "max_wet_share": max_share, "occurrence_mean": occurrence, "ever_wet_share": ever,
        "wettest_month": wettest, "trend_per_decade": slope * 10, "trend_p": p,
        "water_class": cls})


def trends(share, years, rows):
    """Theil-Sen slope (per year) and two-sided Mann-Kendall p (with the tie correction) of each
    depression's yearly wet share, over its clear years, for the depressions in `rows`; NaN
    elsewhere."""
    n_dep = share.shape[1]
    slope, p = np.full(n_dep, np.nan), np.full(n_dep, np.nan)
    idx = np.flatnonzero(rows)
    i, j = np.triu_indices(len(years), k=1)
    dt = (years[j] - years[i]).astype(float)
    for start in range(0, len(idx), 20000):
        cols = idx[start:start + 20000]
        y = share[:, cols].T                                   # (d, Y)
        dy = y[:, j] - y[:, i]                                 # NaN where either year is not clear
        slope[cols] = np.nanmedian(dy / dt, axis=1)
        s = np.nansum(np.sign(dy), axis=1)
        n = np.isfinite(y).sum(axis=1)
        var = (n * (n - 1) * (2 * n + 5)).astype(float)
        for row in range(len(cols)):
            vals = y[row][np.isfinite(y[row])]
            _, t = np.unique(vals, return_counts=True)
            var[row] -= np.sum(t * (t - 1) * (2 * t + 5))
        var = var / 18.0
        z = np.where(var > 0, (s - np.sign(s)) / np.sqrt(np.where(var > 0, var, 1)), 0.0)
        p[cols] = [math.erfc(abs(v) / math.sqrt(2)) for v in z]
    return slope, p


def extent_of(values):
    """The maximum water extent as booleans. The files hold 1 inside it, 0 outside and 255 where
    the record has no data (open sea): only 1 counts."""
    return values == 1


def occurrence_of(values):
    """Occurrence in percent as floats: 255 (no data, open sea) counts as 0."""
    return np.where(values <= 100, values, 0).astype(float)


def read_layers(row0, col0, rows, cols, years, source=None):
    """The JRC layers of a window: {"extent", "occurrence", "yearly": {year: array},
    "monthly": [12 arrays]}."""
    window = (row0, col0, rows, cols)
    return {"extent": extent_of(jrc.read_window("extent", *window, source=source)),
            "occurrence": occurrence_of(jrc.read_window("occurrence", *window, source=source)),
            "yearly": {y: jrc.read_window("yearly", *window, year=y, source=source) for y in years},
            "monthly": [jrc.read_window("monthly_recurrence", *window, month=m, source=source)
                        for m in jrc.MONTHS]}


def count_by_label(lab, ids, extent, occurrence, yearly, monthly, years):
    """Counts for depressions labelled on the JRC grid (`lab`: 0 outside every depression)."""
    counts = Counts(ids, len(years))
    sel = (lab > 0) & np.isin(lab, ids)
    pos = np.searchsorted(ids, lab[sel])
    n = len(ids)
    ext = extent[sel]
    counts.n_pix = np.bincount(pos, minlength=n).astype(float)
    counts.ext_n = np.bincount(pos, weights=ext, minlength=n)
    counts.occ_sum = np.bincount(pos, weights=occurrence[sel].astype(float), minlength=n)
    if monthly is not None:
        for m in range(12):
            counts.mon_sum[:, m] = np.bincount(pos, weights=monthly[m][sel] * ext, minlength=n)
    for k, y in enumerate(years):
        v = yearly[y][sel]
        counts.water[k] = np.bincount(pos, weights=v >= jrc.SEASONAL, minlength=n)
        counts.permanent[k] = np.bincount(pos, weights=v == jrc.PERMANENT, minlength=n)
    return counts


def area_counts(extent, yearly, transform, years, inside=None, workers=1):
    """The extent pixels of each H3 cell (at AREA_RES[0]) and, year by year, how many of them
    hold data: (cells (C,) uint64, total (C,), with_data (Y, C)). Only pixels where `inside` is
    True count (all when None). Counts of windows that share no pixel add up (`merge_area_counts`),
    which is how a region analyzed in tiles gets the same views as one analyzed whole."""
    sel = extent if inside is None else (extent & inside)
    rr, cc = np.nonzero(sel)
    if len(rr) == 0:
        return np.zeros(0, np.uint64), np.zeros(0), np.zeros((len(years), 0))
    lon, lat = pixel_centers(transform, rr, cc)
    uniq, inv = np.unique(h3_ids(lat, lon, AREA_RES[0], workers=workers), return_inverse=True)
    total = np.bincount(inv, minlength=len(uniq)).astype(float)
    with_data = np.stack([np.bincount(inv, weights=yearly[y][rr, cc] > 0, minlength=len(uniq))
                          for y in years])
    return uniq.astype(np.uint64), total, with_data


def merge_area_counts(parts):
    """`area_counts` of several windows summed cell by cell."""
    parts = [p for p in parts if len(p[0])]
    if not parts:
        return np.zeros(0, np.uint64), np.zeros(0), None
    cells = np.concatenate([p[0] for p in parts])
    uniq, inv = np.unique(cells, return_inverse=True)
    total = np.bincount(inv, weights=np.concatenate([p[1] for p in parts]), minlength=len(uniq))
    wd = np.concatenate([p[2] for p in parts], axis=1)
    with_data = np.stack([np.bincount(inv, weights=wd[k], minlength=len(uniq))
                          for k in range(wd.shape[0])])
    return uniq, total, with_data


def views_from_counts(cells, total, with_data):
    """Clear years of each H3 cell at the resolutions of AREA_RES: {res: {cell: (Y,) bool}}. A
    year is clear when the share of the cell's extent pixels holding data is at least CLEAR_SHARE
    times its median over the years, and at least CLEAR_FLOOR."""
    if len(cells) == 0:
        return {r: {} for r in AREA_RES}
    out = {}
    for r in AREA_RES:
        if r == AREA_RES[0]:
            uniq, inv = cells, np.arange(len(cells))
        else:
            parents = np.array([h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(x)), r))
                                for x in cells], dtype=np.uint64)
            uniq, inv = np.unique(parents, return_inverse=True)
        tot = np.bincount(inv, weights=total, minlength=len(uniq))
        share = np.stack([np.bincount(inv, weights=with_data[k], minlength=len(uniq)) / tot
                          for k in range(with_data.shape[0])])              # (Y, cells)
        need = np.maximum(CLEAR_FLOOR, CLEAR_SHARE * np.median(share, axis=0))
        clear = share >= need[None, :] - 1e-12
        out[r] = {int(cell): clear[:, k] for k, cell in enumerate(uniq)}
    return out


def area_views(extent, yearly, transform, years, workers=1, inside=None):
    """Clear years of each H3 cell at the resolutions of AREA_RES, from its extent pixels (those
    where `inside` is True): {res: {cell: (Y,) bool}}. See `views_from_counts`."""
    return views_from_counts(*area_counts(extent, yearly, transform, years, inside, workers))


def lookup_area(lat, lon, views, n_years):
    """(clear (Y, D) bool, views_res (D,)) for points: the clear years of the first cell, at the
    resolutions of AREA_RES in order, that has extent pixels; views_res 0 where none has."""
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    clear = np.zeros((n_years, len(lat)), dtype=bool)
    views_res = np.zeros(len(lat), dtype=int)
    for r in AREA_RES:
        todo = np.flatnonzero(views_res == 0)
        if len(todo) == 0 or not views.get(r):
            continue
        cells = h3_ids(lat[todo], lon[todo], r)
        for k, cell in zip(todo, cells):
            found = views[r].get(int(cell))
            if found is not None:
                clear[:, k] = found
                views_res[k] = r
    return clear, views_res


def _settings(method, years, source):
    return {"method": method, "years": [int(years[0]), int(years[-1])], "n_years": len(years),
            "source": "JRC Global Surface Water 1.4" if source is None else str(source),
            "rules": {"min_pixels": MIN_PIXELS, "clear_share": CLEAR_SHARE, "clear_floor": CLEAR_FLOOR,
                      "wet_share": WET_SHARE, "wet_min_area_m2": WET_MIN_AREA_M2,
                      "wet_min_pixels": WET_MIN_PIXELS, "min_clear_years": MIN_CLEAR_YEARS,
                      "permanent_share": PERMANENT_SHARE, "permanent_years": PERMANENT_YEARS,
                      "seasonal_years": SEASONAL_YEARS, "area_res": list(AREA_RES)},
            "credit": "Source: EC JRC/Google (Pekel et al. 2016, doi:10.1038/nature20584), CC BY 4.0"}


def _finish(counts, layers, grid, deps, ids, years, workers, inside=None):
    views = area_views(layers["extent"], layers["yearly"], grid, years, workers=workers,
                       inside=inside)
    return finish_counts(counts, views, deps, ids, years)


def finish_counts(counts, views, deps, ids, years):
    """The water table from counts and area views (the last step of every method)."""
    by_id = deps.set_index("id").loc[ids]
    lat, lon = by_id["lat"].to_numpy(), by_id["lon"].to_numpy()
    clear, views_res = lookup_area(lat, lon, views, len(years))
    return apply_rules(counts, years, clear, views_res, pixel_area_m2(lat))


def history_from_labels(labels, transform, crs, deps, years=None, *, layers=None, source=None,
                        workers=1):
    """The water table of labelled depressions (outline method). `layers`: the JRC layers of the
    window (from `read_layers`), read from `source` when not given."""
    years = _years(years)
    ids = np.sort(deps["id"].to_numpy())
    h, w = labels.shape
    bounds = transform_bounds(crs, "EPSG:4326", *_bounds(transform, h, w), densify_pts=21)
    row0, col0, rows, cols = jrc.grid_window(bounds)
    grid = jrc.grid_transform(row0, col0)
    lab = labels_on_grid(labels, transform, crs, grid, (rows, cols))
    if layers is None:
        layers = read_layers(row0, col0, rows, cols, years, source)
    counts = count_by_label(lab, ids, layers["extent"], layers["occurrence"], layers["yearly"],
                            layers["monthly"], years)
    # the area views count the JRC pixels whose centres lie on the elevation grid, so that a
    # region analyzed in tiles (each pixel counted by the one tile holding its centre) agrees
    table = _finish(counts, layers, grid, deps, ids, years, workers, inside=lab >= 0)
    table.attrs["settings"] = _settings("outline", years, source)
    return table


def labels_on_grid(labels, transform, crs, grid, shape):
    """Depression labels laid on the JRC grid by nearest pixel: -1 where a JRC pixel's centre
    lies off the elevation grid, 0 outside every depression."""
    lab = np.full(shape, -1, dtype="int32")
    reproject(labels.astype("int32"), lab, src_transform=transform, src_crs=crs,
              dst_transform=grid, dst_crs="EPSG:4326", resampling=Resampling.nearest,
              src_nodata=None, dst_nodata=-1, tolerance=0)
    return lab


def water_history(result, *, years=None, source=None, workers=1):
    """One row per depression of a result held in memory: its 1984-2021 satellite water record.

    result: from `analyze` (it keeps the pixel labels). Saved results have no labels: use
        `water_history_by_cells` with their depression x cell table.
    years: the years to use (default all, 1984 to 2021).
    source: None to read from the JRC server, or a folder of JRC tiles laid out as on the server
        (`sources.jrc.local_path`).
    The table's settings (rules, years, credit) are in `table.attrs["settings"]`.
    """
    if result.labels is None:
        raise ValueError("this result has no pixel labels (saved results do not keep them); "
                         "use water_history_by_cells with its depression_cells table")
    return history_from_labels(result.labels, result.transform, result.crs, result.depressions,
                               years, source=source, workers=workers)


def _bounds(transform, h, w):
    x0, y0 = transform @ (0, 0)
    x1, y1 = transform @ (w, h)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def count_by_cells(dep_cells, cell_ids, pixel_cells, pixel_area, extent, occurrence, yearly,
                   monthly, years, ids):
    """Counts for depressions from per-cell JRC values, weighted by each depression's area in
    each cell (the cells method).

    dep_cells: rows (id, h3 as uint64, area_m2). pixel_cells: (P,) H3 cell of each JRC pixel in
    the window, as uint64. pixel_area: (P,) m2 of each pixel. The layers are flattened alike.
    """
    keep = np.isin(pixel_cells, cell_ids)
    uniq, inv = np.unique(pixel_cells[keep], return_inverse=True)
    k = len(uniq)
    ext = np.ravel(extent)[keep]
    n_px = np.bincount(inv, minlength=k).astype(float)
    a_px = np.bincount(inv, weights=pixel_area[keep], minlength=k) / np.maximum(n_px, 1)
    per_ext = np.bincount(inv, weights=ext, minlength=k)
    per_occ = np.bincount(inv, weights=np.ravel(occurrence)[keep].astype(float), minlength=k)
    mon = (np.stack([np.bincount(inv, weights=np.ravel(monthly[m])[keep] * ext, minlength=k)
                     for m in range(12)], axis=1) if monthly is not None else np.zeros((k, 12)))
    wat = np.zeros((len(years), k))
    prm = np.zeros((len(years), k))
    for t, y in enumerate(years):
        v = np.ravel(yearly[y])[keep]
        wat[t] = np.bincount(inv, weights=v >= jrc.SEASONAL, minlength=k)
        prm[t] = np.bincount(inv, weights=v == jrc.PERMANENT, minlength=k)

    dc = dep_cells[np.isin(dep_cells["h3"].to_numpy(), uniq)]
    cpos = np.searchsorted(uniq, dc["h3"].to_numpy())
    dpos = np.searchsorted(ids, dc["id"].to_numpy())
    # the depression's pixels in the cell, as a share of the cell's pixels
    frac = (dc["area_m2"].to_numpy() / a_px[cpos]) / np.maximum(n_px[cpos], 1)
    n = len(ids)
    counts = Counts(ids, len(years))
    counts.n_pix = np.bincount(dpos, weights=frac * n_px[cpos], minlength=n)
    counts.ext_n = np.bincount(dpos, weights=frac * per_ext[cpos], minlength=n)
    counts.occ_sum = np.bincount(dpos, weights=frac * per_occ[cpos], minlength=n)
    for m in range(12):
        counts.mon_sum[:, m] = np.bincount(dpos, weights=frac * mon[cpos, m], minlength=n)
    for t in range(len(years)):
        counts.water[t] = np.bincount(dpos, weights=frac * wat[t, cpos], minlength=n)
        counts.permanent[t] = np.bincount(dpos, weights=frac * prm[t, cpos], minlength=n)
    return counts


def water_history_by_cells(depressions, depression_cells, bbox, *, years=None, source=None,
                           layers=None, workers=1):
    """The water record of the depressions in `bbox` from a depression x cell table (cells
    method). `depressions` needs id, lat and lon; `depression_cells` id, h3 and area_m2.
    `layers`: the JRC layers of the box's window (from `read_layers`), read when not given."""
    years = _years(years)
    row0, col0, rows, cols = jrc.grid_window(bbox)
    grid = jrc.grid_transform(row0, col0)
    dc = depression_cells[["id", "h3", "area_m2"]].copy()
    dc["h3"] = np.array([h3.str_to_int(x) if isinstance(x, str) else int(x) for x in dc["h3"]],
                        dtype=np.uint64)
    res = h3.get_resolution(h3.int_to_str(int(dc["h3"].iloc[0])))
    ids = np.sort(depressions["id"].to_numpy())
    rr, cc = np.indices((rows, cols))
    lon, lat = pixel_centers(grid, rr.ravel(), cc.ravel())
    pixel_cells = h3_ids(lat, lon, res, workers=workers)
    if layers is None:
        layers = read_layers(row0, col0, rows, cols, years, source)
    counts = count_by_cells(dc, np.unique(dc["h3"].to_numpy()), pixel_cells, pixel_area_m2(lat),
                            layers["extent"], layers["occurrence"], layers["yearly"],
                            layers["monthly"], years, ids)
    table = _finish(counts, layers, grid, depressions, ids, years, workers)
    table.attrs["settings"] = _settings("cells", years, source)
    return table


class WaterHook:
    """`analyze_tiled(hooks=[WaterHook(source=...)])`: `water_history` (outline method) for a
    region too big to hold, counted tile by tile without keeping pixel labels.

    Each JRC pixel counts for the one tile holding its centre; the counts of a depression's pieces
    add up, and so do the area views' counts, so the `water_history` table it adds equals
    `water_history` on the whole region. Layers are read one at a time, so a tile needs little
    memory. source: a folder of JRC tiles laid out as on the server (`sources.jrc.local_path`;
    download them first for a national run, since reading
    windows from the server is far too slow at that size), or None for the server.
    """

    name = "water"
    wide = {"water__npix": "sum", "water__ext": "sum", "water__occ": "sum"}

    def __init__(self, source=None, years=None, workers=1):
        self.source, self.years, self.workers = source, _years(years), workers

    def settings(self):
        return {"hook": self.name, "source": None if self.source is None else str(self.source),
                "years": [self.years[0], self.years[-1], len(self.years)]}

    def tile(self, crs, box, transform, labels, labs):
        n = len(labs)
        zeros = {c: np.zeros(n) for c in self.wide}
        h, w = labels.shape
        try:
            row0, col0, rows, cols = jrc.grid_window(
                transform_bounds(crs, "EPSG:4326", *_bounds(transform, h, w), densify_pts=21))
        except ValueError:                           # outside the JRC record (beyond 80N / 60S)
            return zeros, {}
        grid = jrc.grid_transform(row0, col0)
        lab = labels_on_grid(labels, transform, crs, grid, (rows, cols))
        inside = lab >= 0

        def read(layer, **kw):
            return jrc.read_window(layer, row0, col0, rows, cols, source=self.source, **kw)
        extent = extent_of(read("extent"))
        order = np.argsort(labs)
        sel = (lab > 0) & np.isin(lab, labs)
        pos = order[np.searchsorted(labs[order], lab[sel])]       # piece number of each pixel
        ext = extent[sel]
        wide = {"water__npix": np.bincount(pos, minlength=n).astype(float),
                "water__ext": np.bincount(pos, weights=ext, minlength=n),
                "water__occ": np.bincount(pos, weights=occurrence_of(read("occurrence"))[sel],
                                          minlength=n)}
        flat = {}
        recs, keys, sums = [], [], []
        for m in jrc.MONTHS:
            v = np.bincount(pos, weights=read("monthly_recurrence", month=m)[sel] * ext, minlength=n)
            k = np.flatnonzero(v)
            recs.append(k)
            keys.append(np.full(len(k), m - 1))
            sums.append(v[k])
        flat.update(mrec=np.concatenate(recs), mkey=np.concatenate(keys),
                    msum=np.concatenate(sums))
        rr, cc = np.nonzero(extent & inside)
        cells = np.zeros(0, np.uint64)
        if len(rr):
            lon, lat = pixel_centers(grid, rr, cc)
            cells, inv = np.unique(h3_ids(lat, lon, AREA_RES[0], workers=self.workers),
                                   return_inverse=True)
        with_data = np.zeros((len(self.years), len(cells)))
        recs, keys, wat, prm = [], [], [], []
        for t, y in enumerate(self.years):
            v = read("yearly", year=y)
            if len(rr):
                with_data[t] = np.bincount(inv, weights=v[rr, cc] > 0, minlength=len(cells))
            vs = v[sel]
            a = np.bincount(pos, weights=vs >= jrc.SEASONAL, minlength=n)
            b = np.bincount(pos, weights=vs == jrc.PERMANENT, minlength=n)
            k = np.flatnonzero(a)
            recs.append(k)
            keys.append(np.full(len(k), t))
            wat.append(a[k])
            prm.append(b[k])
        flat.update(yrec=np.concatenate(recs), ykey=np.concatenate(keys),
                    ywater=np.concatenate(wat), yperm=np.concatenate(prm),
                    cells=cells.astype(np.uint64),
                    total=np.bincount(inv, minlength=len(cells)).astype(float) if len(rr)
                    else np.zeros(0), with_data=with_data)
        return wide, flat

    def finish(self, out, piece_comp, id_of_comp, parts):
        years = self.years
        if out is None or len(out) == 0:
            table = apply_rules(Counts(np.zeros(0, np.int64), len(years)), years,
                                np.zeros((len(years), 0), bool), np.zeros(0, int), np.zeros(0))
            table.attrs["settings"] = _settings("outline (tiled)", years, self.source)
            return {"water_history": table}
        ids = out["id"].to_numpy()
        counts = Counts(ids, len(years))
        counts.n_pix = out["water__npix"].to_numpy(float)
        counts.ext_n = out["water__ext"].to_numpy(float)
        counts.occ_sum = out["water__occ"].to_numpy(float)
        key = self.name + "__"

        def gather(rec, *fields):
            got = [p for p in parts if key + rec in p]
            if not got:
                return None
            dep = id_of_comp[piece_comp[np.concatenate([p[key + rec] for p in got])]]
            keep = dep >= 0
            pos = np.searchsorted(ids, dep[keep])
            return (pos, *(np.concatenate([p[key + f] for p in got])[keep] for f in fields))
        g = gather("yrec", "ykey", "ywater", "yperm")
        if g is not None:
            pos, k, wat, prm = g
            np.add.at(counts.water, (k.astype(np.int64), pos), wat)
            np.add.at(counts.permanent, (k.astype(np.int64), pos), prm)
        g = gather("mrec", "mkey", "msum")
        if g is not None:
            pos, k, v = g
            np.add.at(counts.mon_sum, (pos, k.astype(np.int64)), v)
        views = views_from_counts(*merge_area_counts(
            [(p[key + "cells"], p[key + "total"], p[key + "with_data"])
             for p in parts if key + "cells" in p]))
        table = finish_counts(counts, views, out, ids, years)
        table.attrs["settings"] = _settings("outline (tiled)", years, self.source)
        return {"water_history": table}
