"""Design rainfall from NOAA Atlas 14: how much rain falls in a storm of a given rarity.

    from h3lowpoints.sources.rainfall import design_rainfall, rainfall_depth
    table = design_rainfall(30.27, -97.74)        # mm, rows = durations, columns = years (ARI)
    rainfall_depth(30.27, -97.74, "60-min", 10)   # 1-hour storm expected once in 10 years, mm

These are point estimates from NOAA's Precipitation Frequency Data Server (public domain, no
account). Over a city they change little, so one point near the centre is a fair design value.
Durations are labelled as NOAA labels them: "5-min" ... "60-min", "2-hr" ... "24-hr", "2-day" ...
"60-day". Atlas 14 does not cover the Pacific Northwest (Washington and Oregon; checked in 2026),
so there the server returns an error and you should supply a rainfall depth yourself.
"""
import urllib.parse

import pandas as pd

from ._common import read_text

PFDS = "https://hdsc.nws.noaa.gov/cgi-bin/new/fe_text_mean.csv"


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
