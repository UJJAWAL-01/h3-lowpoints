"""Design rainfall from NOAA Atlas 14: how much rain falls in a storm of a given rarity.

    from h3lowpoints.sources.rainfall import design_rainfall, rainfall_depth
    table = design_rainfall(30.27, -97.74)        # mm, rows = durations, columns = years (ARI)
    rainfall_depth(30.27, -97.74, "60-min", 10)   # 1-hour storm expected once in 10 years, mm

These are point estimates from NOAA's Precipitation Frequency Data Server (public domain, no
account). Over a city they change little, so one point near the centre is a fair design value.
Durations are labelled as NOAA labels them: "5-min" ... "60-min", "2-hr" ... "24-hr", "2-day" ...
"60-day". Atlas 14 does not cover the Pacific Northwest (Washington and Oregon; checked in 2026),
so there the server returns an error and you should supply a rainfall depth yourself.

For many points (every H3 cell of a region), `Atlas14Grids` reads NOAA's published grids once and
samples them:

    grids = Atlas14Grids(years=(10, 100), cache_dir="atlas14")   # 24-hour storms
    grids.depth(100, lat, lon)                                   # mm, NaN where Atlas 14 has none

The grids come in one set per Atlas 14 project area (ATLAS14_VOLUMES), 30 arc-seconds (about
800 m) apart, in thousandths of an inch. The areas meet in thin strips where two grids both have
values; there they can differ by a few percent (NOAA notes that discrepancies at project
boundaries are inevitable), and a point gets the mean of the grids that cover it.
"""
import io
import urllib.parse
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from ._common import download_once, read_bytes, read_text

PFDS = "https://hdsc.nws.noaa.gov/cgi-bin/new/fe_text_mean.csv"
ATLAS14_DATA = "https://hdsc.nws.noaa.gov/pub/hdsc/data"
# the grid sets covering the conterminous US, by NOAA's folder name (checked in October 2026)
ATLAS14_VOLUMES = {
    "sw": "Southwest: volumes 1 (semiarid Southwest) and 6 (California)",
    "orb": "volume 2, Ohio River Basin and surrounding states",
    "mw": "volume 8, Midwestern states",
    "se": "volume 9, Southeastern states",
    "ne": "volume 10, Northeastern states",
    "tx": "volume 11, Texas",
    "inw": "volume 12, Interior Northwest (Idaho, Montana, Wyoming)",
}
# their extents (west, south, east, north), the same for every duration and return period, so a
# grid is only downloaded and read when a point falls inside it
ATLAS14_EXTENTS = {
    "sw": (-124.7542, 31.3292, -102.9876, 42.9708),
    "orb": (-91.5376, 31.9566, -73.8743, 42.5296),
    "mw": (-109.3625, 33.3125, -82.3719, 49.4285),
    "se": (-94.9208, 24.4625, -79.9547, 36.7953),
    "ne": (-82.2958, 38.4958, -66.4792, 48.7458),
    "tx": (-106.6542, 25.8292, -93.4958, 36.5125),
    "inw": (-117.2542, 40.9875, -104.0292, 49.0125),
}
ATLAS14_YEARS = (1, 2, 5, 10, 25, 50, 100, 200, 500, 1000)
ATLAS14_DURATIONS = ("05m", "10m", "15m", "30m", "60m", "02h", "03h", "06h", "12h", "24h", "48h",
                     "03d", "04d", "07d", "10d", "20d", "30d", "45d", "60d")
MM_PER_THOUSANDTH_INCH = 0.0254


def parse_atlas14(text):
    """The precipitation-frequency table from a PFDS CSV response (first table: the estimates)."""
    if "not within a project area" in text or "result = 'none'" in text:
        raise ValueError("NOAA Atlas 14 does not cover this location (it does not cover the "
                         "Pacific Northwest, Washington and Oregon); supply a rainfall depth yourself")
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("by duration for ARI"))
    except StopIteration:
        raise ValueError(f"Unexpected answer from NOAA PFDS: {text[:200]!r}") from None
    years = [int(v) for v in lines[start].split(":", 1)[1].split(",") if v.strip()]
    rows = {}
    for ln in lines[start + 1:]:
        if not ln.strip() or ":" not in ln:
            break
        duration, values = ln.split(":", 1)
        numbers = [float(v) for v in values.split(",") if v.strip()]
        if len(numbers) != len(years):
            break
        rows[duration.strip()] = numbers
    if not rows:
        raise ValueError("NOAA PFDS returned no estimates")
    table = pd.DataFrame.from_dict(rows, orient="index", columns=years)
    table.index.name, table.columns.name = "duration", "ari_years"
    units = "mm" if "millimeters" in text.lower() else "in" if "inches" in text.lower() else "?"
    table.attrs.update({"units": units, "source": "NOAA Atlas 14 (PFDS)"})
    return table


def design_rainfall(lat, lon):
    """Partial-duration precipitation-frequency estimates in millimetres for a point in the US."""
    query = urllib.parse.urlencode({"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "data": "depth",
                                    "units": "metric", "series": "pds"})
    return parse_atlas14(read_text(f"{PFDS}?{query}"))


def rainfall_depth(lat, lon, duration="60-min", ari_years=10, table=None):
    """One design depth in millimetres, for example the 1-hour storm expected once in 10 years."""
    table = design_rainfall(lat, lon) if table is None else table
    if duration not in table.index:
        raise ValueError(f"duration must be one of {list(table.index)}, got {duration!r}")
    if ari_years not in table.columns:
        raise ValueError(f"ari_years must be one of {list(table.columns)}, got {ari_years!r}")
    return float(table.loc[duration, ari_years])


def atlas14_grid_url(volume, years, duration="24h"):
    """URL of NOAA's zipped grid of the `duration` storm expected once in `years` years."""
    if volume not in ATLAS14_VOLUMES:
        raise ValueError(f"volume must be one of {list(ATLAS14_VOLUMES)}, got {volume!r}")
    if years not in ATLAS14_YEARS:
        raise ValueError(f"years must be one of {list(ATLAS14_YEARS)}, got {years!r}")
    if duration not in ATLAS14_DURATIONS:
        raise ValueError(f"duration must be one of {list(ATLAS14_DURATIONS)}, got {duration!r}")
    return f"{ATLAS14_DATA}/{volume}/{volume}{years}yr{duration}a.zip"


def read_ascii_grid(data):
    """An ESRI ASCII grid, given as bytes of the .asc file or of a zip holding one.

    Returns (values, west, north, cell): a float32 array with NaN for no data, the grid's west and
    north edges in degrees, and its cell size. The header's keywords may be in any case, and the
    corner may be given as a cell corner (xllcorner) or a cell centre (xllcenter).
    """
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".asc")]
            if len(names) != 1:
                raise ValueError(f"expected one .asc grid in the zip, found {names}")
            data = z.read(names[0])
    head, rest = {}, data
    for _ in range(6):
        line, rest = rest.split(b"\n", 1)
        key, value = line.split()
        head[key.decode().lower()] = float(value)
    rows, cols, cell = int(head["nrows"]), int(head["ncols"]), head["cellsize"]
    west = head["xllcorner"] if "xllcorner" in head else head["xllcenter"] - cell / 2
    south = head["yllcorner"] if "yllcorner" in head else head["yllcenter"] - cell / 2
    values = np.array(rest.split(), dtype=np.float32)
    if values.size != rows * cols:
        raise ValueError(f"the grid holds {values.size} values, not {rows} x {cols}")
    values = values.reshape(rows, cols)
    if "nodata_value" in head:
        values[values == head["nodata_value"]] = np.nan
    return values, west, south + rows * cell, cell


class Atlas14Grids:
    """NOAA Atlas 14 precipitation-frequency depths for the conterminous US, sampled at points.

    years      return periods to load (any of ATLAS14_YEARS)
    duration   storm duration, as NOAA names its files (ATLAS14_DURATIONS; default "24h")
    cache_dir  keep the downloaded zips here and reuse them (about 2 to 4 MB each, one per
               volume and return period); without it they are downloaded every time
    volumes    the project areas to use (default: all of ATLAS14_VOLUMES)

    Each point takes the value of the grid cell containing it, and the mean where two project
    areas overlap. Points no grid covers (Washington, Oregon, the sea, outside the US) get NaN.
    A grid is downloaded and read the first time a point falls within its project area's extent
    (ATLAS14_EXTENTS), so a city needs only the grids of its own area.
    """

    def __init__(self, years=ATLAS14_YEARS, duration="24h", *, cache_dir=None, volumes=None):
        for y in years:
            atlas14_grid_url("sw", y, duration)          # checks the return periods and duration
        self.years, self.duration, self.cache_dir = tuple(years), duration, cache_dir
        self.volumes = tuple(ATLAS14_VOLUMES if volumes is None else volumes)
        self.grids = {}

    def grid(self, volume, years):
        """(values in mm with NaN for no data, west, north, cell size) of one grid, read once."""
        if (volume, years) not in self.grids:
            url = atlas14_grid_url(volume, years, self.duration)
            if self.cache_dir:
                local = download_once(url, self.cache_dir)
                if local is None:
                    raise ValueError(f"NOAA has no grid at {url}")
                data = Path(local).read_bytes()
            else:
                data = read_bytes(url)
            values, west, north, cell = read_ascii_grid(data)
            self.grids[(volume, years)] = (values * np.float32(MM_PER_THOUSANDTH_INCH), west, north,
                                           cell)
        return self.grids[(volume, years)]

    def depth(self, years, lat, lon):
        """Rainfall depth in mm of the storm expected once in `years` years at each point."""
        if years not in self.years:
            raise ValueError(f"years {years!r} was not loaded (loaded: {list(self.years)})")
        lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
        total, count = np.zeros(lat.shape), np.zeros(lat.shape)
        for v in self.volumes:
            if v in ATLAS14_EXTENTS:
                w, s, e, n = ATLAS14_EXTENTS[v]
                if not ((lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)).any():
                    continue
            values, west, north, cell = self.grid(v, years)
            row = np.floor((north - lat) / cell).astype(np.int64)
            col = np.floor((lon - west) / cell).astype(np.int64)
            inside = (row >= 0) & (row < values.shape[0]) & (col >= 0) & (col < values.shape[1])
            got = np.full(lat.shape, np.nan)
            got[inside] = values[row[inside], col[inside]]
            ok = ~np.isnan(got)
            total[ok] += got[ok]
            count[ok] += 1
        with np.errstate(invalid="ignore"):
            return np.where(count > 0, total / np.maximum(count, 1), np.nan)


# ---------------------------------------------------------------- storms of any duration

def duration_hours(duration):
    """Hours of a duration as NOAA names its grid files ("05m", "06h", "03d", ...)."""
    if duration not in ATLAS14_DURATIONS:
        raise ValueError(f"duration must be one of {list(ATLAS14_DURATIONS)}, got {duration!r}")
    value, unit = int(duration[:2]), duration[2]
    return value / 60.0 if unit == "m" else value * 24.0 if unit == "d" else float(value)


ATLAS14_HOURS = np.array([duration_hours(d) for d in ATLAS14_DURATIONS])


class Atlas14Storms:
    """NOAA Atlas 14 storms of any duration from 5 minutes to 60 days, and the return period of
    any storm, at points in the conterminous US.

        storms = Atlas14Storms(cache_dir="atlas14")
        storms.depth(100, 6.0, lat, lon)              # mm in the 100-year 6-hour storm
        storms.return_period(300.0, 6.0, lat, lon)    # years between storms of 300 mm in 6 hours

    Between NOAA's published durations, depth is interpolated linearly in log(depth) against
    log(duration); between its published return periods (1 to 1000 years), the return period is
    interpolated linearly in log(years) against depth. A storm smaller than the 1-year storm gets
    0 (more frequent than once a year) and one larger than the 1000-year storm gets inf (beyond
    Atlas 14). Return periods are NOAA's average recurrence intervals from partial-duration
    series. Points Atlas 14 does not cover get NaN. Grids are read as in Atlas14Grids, one set per
    duration, the first time they are needed.
    """

    def __init__(self, *, cache_dir=None, volumes=None):
        self.cache_dir, self.volumes = cache_dir, volumes
        self.sets = {}

    def grids(self, duration):
        """The Atlas14Grids of one published duration (all return periods), made once."""
        if duration not in self.sets:
            self.sets[duration] = Atlas14Grids(ATLAS14_YEARS, duration, cache_dir=self.cache_dir,
                                               volumes=self.volumes)
        return self.sets[duration]

    @staticmethod
    def bracket(hours):
        """(shorter, longer, weight of longer) of the published durations around `hours`."""
        if not ATLAS14_HOURS[0] <= hours <= ATLAS14_HOURS[-1]:
            raise ValueError(f"Atlas 14 covers storms of 5 minutes to 60 days, not {hours:g} hours")
        hi = int(np.searchsorted(ATLAS14_HOURS, hours))
        if ATLAS14_HOURS[hi] == hours:
            return ATLAS14_DURATIONS[hi], ATLAS14_DURATIONS[hi], 1.0
        lo = hi - 1
        w = ((np.log(hours) - np.log(ATLAS14_HOURS[lo]))
             / (np.log(ATLAS14_HOURS[hi]) - np.log(ATLAS14_HOURS[lo])))
        return ATLAS14_DURATIONS[lo], ATLAS14_DURATIONS[hi], float(w)

    def depth(self, years, hours, lat, lon):
        """Depth in mm of the `hours`-long storm expected once in `years` years (ATLAS14_YEARS)."""
        if years not in ATLAS14_YEARS:
            raise ValueError(f"years must be one of {list(ATLAS14_YEARS)}, got {years!r}")
        short, long, w = self.bracket(float(hours))
        b = self.grids(long).depth(years, lat, lon)
        if w == 1.0:
            return b
        a = self.grids(short).depth(years, lat, lon)
        return log_interpolate(a, b, w)

    def depths(self, hours, lat, lon):
        """Depths in mm at every return period of ATLAS14_YEARS: shape (points, 10)."""
        lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
        return np.stack([self.depth(y, hours, lat, lon) for y in ATLAS14_YEARS], axis=-1)

    def return_period(self, depth_mm, hours, lat, lon):
        """Years between storms of `depth_mm` (or more) in `hours` at each point; see the class."""
        lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
        depth_mm = np.broadcast_to(np.asarray(depth_mm, dtype=float), lat.shape)
        return years_of_depth(depth_mm, self.depths(hours, lat, lon))


def log_interpolate(a, b, w):
    """exp((1 - w) log a + w log b): depth between two durations, `w` the share of the way."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.exp((1 - w) * np.log(a) + w * np.log(b))


def years_of_depth(depth_mm, table):
    """Return period of each depth from its row of depths at ATLAS14_YEARS (the rule of
    Atlas14Storms.return_period): 0 below the first, inf above the last, NaN where unknown."""
    depth_mm = np.asarray(depth_mm, dtype=float)
    table = np.asarray(table, dtype=float).reshape(depth_mm.shape + (len(ATLAS14_YEARS),))
    log_years = np.log(np.asarray(ATLAS14_YEARS, dtype=float))
    out = np.full(depth_mm.shape, np.nan)
    for i in np.ndindex(depth_mm.shape):
        row, d = table[i], depth_mm[i]
        if np.isnan(d) or np.isnan(row).any():
            continue
        if d < row[0]:
            out[i] = 0.0
        elif d > row[-1]:
            out[i] = np.inf
        else:
            out[i] = float(np.exp(np.interp(d, row, log_years)))
    return out


# --------------------------------------------------------------------- how storms unfold

TEMPORAL_SHAPEFILES = "https://hdsc.nws.noaa.gov/pfds/shapefiles/Vol{n}_Temporals_Shapefiles.zip"
# Atlas 14 volume -> region number -> file prefix, for the lower 48 (checked in October 2026
# against NOAA's temporal-distribution page: volume 1 area 1 is "general" and area 2
# "convective"; the files of volumes 1 and 6 are in the sw folder)
TEMPORAL_VOLUMES = {
    1: lambda r: {1: "sw_general", 2: "sw_convective"}[r],
    2: lambda r: "orb_general",
    6: lambda r: f"ca_{r}",
    8: lambda r: f"mw_{r}",
    9: lambda r: f"se_{r}",
    10: lambda r: f"ne_{r}",
    11: lambda r: f"tx_{r}",
    12: lambda r: f"inw_{r}",
}
TEMPORAL_HOURS = (6, 12, 24, 96)
TEMPORAL_CASES = ("first", "second", "third", "fourth", "all")
TEMPORAL_PERCENTS = (10, 20, 30, 40, 50, 60, 70, 80, 90)


def temporal_url(name, hours):
    """URL of NOAA's temporal-distribution file of region `name` ("tx_1", "ca_3", ...)."""
    if hours not in TEMPORAL_HOURS:
        raise ValueError(f"NOAA publishes temporal distributions for {TEMPORAL_HOURS} hours, not {hours!r}")
    prefix = name.split("_")[0]
    folder = "sw" if prefix in ("sw", "ca") else prefix
    if folder not in ATLAS14_VOLUMES:
        raise ValueError(f"unknown temporal-distribution region {name!r}")
    return f"{ATLAS14_DATA}/{folder}/{name}_{hours}h_temporal.csv"


def parse_temporal(text):
    """NOAA Atlas 14 temporal distributions from one of its CSV files.

    Returns {case: (time as a share of the duration, {percent: cumulative share of the rain})},
    case in TEMPORAL_CASES (the four quartiles of storms, by when most rain falls, and all
    storms). A curve's percent is the share of storms that had at least that much rain by each
    time (10: front-loaded ... 90: late; 50 is the median). Reads both of NOAA's layouts: volumes
    1 and 2 (one row per curve, columns in percent of the duration) and the later volumes (one
    row per time, in hours). Where rounding makes a curve fall back slightly (by at most 0.03
    percentage points in the 120 files of the lower 48), it is held level instead.
    """
    out, case = {}, None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        low = lines[i].strip().strip(",").strip().lower()
        if "quartile" in low or "all cases" in low:
            case = next((k for k in TEMPORAL_CASES if low.startswith(k) or f"for {k}" in low), None)
        elif case and low.startswith("percent of duration"):          # volumes 1 and 2
            times = np.array([float(v) for v in low.split(",")[1:] if v.strip()]) / 100.0
            curves = {}
            for ln in lines[i + 1:i + 1 + len(TEMPORAL_PERCENTS)]:
                cells = [c.strip() for c in ln.split(",") if c.strip()]
                curves[int(cells[0].rstrip("%"))] = np.array([float(v) for v in cells[1:]]) / 100.0
            out[case], case = (times, curves), None
            i += len(TEMPORAL_PERCENTS)
        elif case and low.startswith("hours,"):                         # later volumes
            percents = [int(v.strip().rstrip("%")) for v in low.split(",")[1:] if v.strip()]
            rows = []
            for ln in lines[i + 1:]:
                try:
                    row = [float(c) for c in ln.split(",") if c.strip()]
                except ValueError:
                    break
                if not row:
                    break
                rows.append(row)
            rows = np.array(rows)
            out[case] = (rows[:, 0] / rows[-1, 0],
                         {p: rows[:, j + 1] / 100.0 for j, p in enumerate(percents)})
            case = None
            i += len(rows)
        i += 1
    if set(out) != set(TEMPORAL_CASES):
        raise ValueError(f"expected the cases {TEMPORAL_CASES} in the temporal file, found {sorted(out)}")
    for times, curves in out.values():
        if set(curves) != set(TEMPORAL_PERCENTS) or any(len(c) != len(times) for c in curves.values()):
            raise ValueError("a temporal table does not have the curves 10% to 90% at every time")
        for p, c in curves.items():
            # NOAA's tables are rounded: a few curves fall back by up to 0.03 percentage points
            curves[p] = np.maximum.accumulate(c)
    return out


def nearest_temporal_hours(hours):
    """The published temporal duration (6, 12, 24, 96 h) nearest to `hours` in log time."""
    if not hours > 0:
        raise ValueError("a storm lasts a positive number of hours")
    gaps = np.abs(np.log(np.asarray(TEMPORAL_HOURS, float)) - np.log(hours))
    return TEMPORAL_HOURS[int(np.argmin(gaps))]


class TemporalPatterns:
    """NOAA Atlas 14 temporal distributions: how the rain of a storm is spread over its duration,
    by region, for 6, 12, 24 and 96-hour storms.

        patterns = TemporalPatterns(cache_dir="atlas14")
        patterns.region(lat, lon)                  # e.g. "tx_1" at each point (None: no region)
        t, cum = patterns.pattern("tx_1", 6.0)     # all-cases median curve of a 6-hour storm

    Regions come from NOAA's shapefiles of temporal-distribution areas, one per volume (reading
    them needs geopandas); curves from its CSV files; both are downloaded once into `cache_dir`.
    A storm of another duration takes the curve of the nearest published duration (in log time),
    stretched to its own length: an assumption, as NOAA publishes these four only.
    """

    def __init__(self, *, cache_dir=None):
        self.cache_dir = cache_dir
        self.regions, self.tables = None, {}

    def _bytes(self, url):
        if self.cache_dir:
            local = download_once(url, self.cache_dir)
            if local is None:
                raise ValueError(f"NOAA has no file at {url}")
            return Path(local).read_bytes()
        return read_bytes(url)

    def _region_frame(self):
        if self.regions is None:
            import tempfile

            import geopandas as gpd

            frames = []
            for n, prefix in sorted(TEMPORAL_VOLUMES.items(), reverse=True):     # newest first
                data = self._bytes(TEMPORAL_SHAPEFILES.format(n=n))
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / f"vol{n}.zip"
                    path.write_bytes(data)
                    g = gpd.read_file(f"zip://{path}").to_crs("EPSG:4326")
                field = "Region" if "Region" in g.columns else "Id"
                g["name"] = [prefix(int(r)) for r in g[field]]
                frames.append(g[["name", "geometry"]])
            self.regions = pd.concat(frames, ignore_index=True)
        return self.regions

    def region(self, lat, lon):
        """Name of the temporal-distribution region at each point (None outside every region).

        NOAA's regions of different volumes overlap only in thin strips along their borders (at
        most about 1,000 km2, under 1% of a region; measured in October 2026) and where Volume 8's
        region 4 and Volume 9's region 1 are one area with identical files; a point in two takes
        the newer volume's region."""
        import geopandas as gpd

        lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
        points = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")
        hit = gpd.sjoin(points, self._region_frame(), how="left", predicate="within")
        names = hit[~hit.index.duplicated(keep="first")]["name"].reindex(points.index)
        return np.array([None if pd.isna(v) else v for v in names], dtype=object)

    def table(self, name, hours):
        """parse_temporal of region `name` for a published duration (6, 12, 24 or 96 hours)."""
        if (name, hours) not in self.tables:
            text = self._bytes(temporal_url(name, hours)).decode("latin-1")
            self.tables[(name, hours)] = parse_temporal(text)
        return self.tables[(name, hours)]

    def pattern(self, name, hours, case="all", percent=50):
        """(time share, cumulative share of the rain) of a storm of `hours` in region `name`."""
        if case not in TEMPORAL_CASES:
            raise ValueError(f"case must be one of {TEMPORAL_CASES}, got {case!r}")
        if percent not in TEMPORAL_PERCENTS:
            raise ValueError(f"percent must be one of {TEMPORAL_PERCENTS}, got {percent!r}")
        times, curves = self.table(name, nearest_temporal_hours(hours))[case]
        return times, curves[percent]
