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
