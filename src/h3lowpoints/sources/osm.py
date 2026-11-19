"""OpenStreetMap roads, railways and streams for the road-crossing fix, from Geofabrik extracts.

    from h3lowpoints.sources import osm
    lines = osm.fetch_lines((-99.5, 47.7, -98.5, 48.3), cache_dir="osm")
    barriers, streams, bridges = osm.split_lines(lines)

Geofabrik (download.geofabrik.de) publishes daily extracts of OpenStreetMap for every country and
many states and regions, with no account. `fetch_lines` picks the smallest extract that holds the
box (from Geofabrik's index), downloads it once into `cache_dir` under its dated name, and reads the
lines in the box with GDAL's OSM driver. OpenStreetMap data is under the Open Database Licence:
credit "© OpenStreetMap contributors" (see `CREDIT`), and say which extract date was used
(`lines.attrs["extract"]`).

Which lines count (fixed in the study rules, method note A):
- barriers: `highway` roads of the classes in `ROADS` (and every `*_link`), `railway` in `RAILWAYS`,
  not in a tunnel;
- streams: `waterway` in `STREAMS`, culverts included;
- bridges: barriers tagged `bridge` other than `no`.
"""
import json
import re
import urllib.request
from pathlib import Path

from ._common import USER_AGENT, check_bbox, download_once, read_bytes

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
CREDIT = "© OpenStreetMap contributors (ODbL), extract from Geofabrik"
ROADS = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
         "service", "living_street", "road")
RAILWAYS = ("rail", "light_rail", "narrow_gauge", "preserved", "disused", "abandoned")
STREAMS = ("river", "stream", "canal", "drain", "ditch")
_TAG = re.compile(r'"([^"]+)"=>"([^"]*)"')


def _geopandas():
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("OSM lines need geopandas: pip install 'h3-lowpoints[sources]'") from exc
    return gpd


def extract_for(bbox, *, cache_dir, get=read_bytes):
    """(id, pbf URL) of the smallest Geofabrik extract whose area holds the whole box.

    The index (about 4 MB) is kept in `cache_dir` as `geofabrik-index.json`."""
    from shapely.geometry import box, shape

    west, south, east, north = check_bbox(bbox)
    path = Path(cache_dir) / "geofabrik-index.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(get(INDEX_URL))
    want = box(west, south, east, north)
    best = None
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        url = f["properties"].get("urls", {}).get("pbf")
        if not url or f.get("geometry") is None:
            continue
        g = shape(f["geometry"])
        if g.covers(want) and (best is None or g.area < best[0]):
            best = (g.area, f["properties"]["id"], url)
    if best is None:
        raise ValueError(f"no Geofabrik extract holds the whole box {bbox}; use a smaller box")
    return best[1], best[2]


def dated_url(url, timeout=60):
    """The dated file that Geofabrik's "-latest" link points to today (the link redirects)."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.geturl()


def tags(other_tags):
    """The OSM tags GDAL leaves in its `other_tags` text, as a dict."""
    return dict(_TAG.findall(other_tags)) if isinstance(other_tags, str) else {}


def read_lines(pbf, bbox):
    """The lines of an OSM .pbf file inside a lon/lat box with `highway`, `railway`, `waterway`,
    `bridge` and `tunnel`, in WGS 84."""
    gpd = _geopandas()
    lines = gpd.read_file(pbf, layer="lines", bbox=tuple(check_bbox(bbox)),
                          columns=["osm_id", "highway", "waterway", "railway", "other_tags"])
    extra = [tags(t) for t in lines.pop("other_tags")]
    for name in ("railway", "bridge", "tunnel"):
        values = [e.get(name) for e in extra]
        if name not in lines:
            lines[name] = values
        else:
            lines[name] = lines[name].where(lines[name].notna(), values)
    return lines.set_crs(4326, allow_override=True)


def fetch_lines(bbox, *, cache_dir, extract=None):
    """Roads, railways and streams of OpenStreetMap in a lon/lat box (see the module doc).

    extract: a Geofabrik .pbf URL or local file to use instead of the one found for the box.
    The extract is kept in `cache_dir` (download once). `attrs["extract"]` holds its file name,
    which carries its date (for example `north-dakota-261113.osm.pbf` is 13 November 2026).
    """
    if extract is None or str(extract).startswith("http"):
        url = extract or extract_for(bbox, cache_dir=cache_dir)[1]
        local = download_once(dated_url(url) if "-latest" in url else url, cache_dir)
        if local is None:
            raise ValueError(f"Geofabrik has no file at {url}")
    else:
        local = Path(extract)
    lines = read_lines(local, bbox)
    lines.attrs["extract"] = Path(local).name
    lines.attrs["credit"] = CREDIT
    return lines


def _yes(values):
    return values.notna() & (values.astype(str) != "no")


def split_lines(lines):
    """(barriers, streams, bridges): the lines that dam, the lines that carry water, and the
    barriers on bridges (see the module doc)."""
    hw = lines["highway"].astype("string")
    road = hw.isin(ROADS) | hw.str.endswith("_link").fillna(False)
    rail = lines["railway"].isin(RAILWAYS)
    barrier = (road | rail) & ~_yes(lines["tunnel"])
    streams = lines[lines["waterway"].isin(STREAMS)]
    return lines[barrier], streams, lines[barrier & _yes(lines["bridge"])]
