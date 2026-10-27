"""Roads: US Census Bureau TIGER/Line, every public road of every county.

    from h3lowpoints.sources.roads import fetch_roads
    roads = fetch_roads((-95.6, 29.6, -95.2, 29.9), cache_dir="tiger")

TIGER/Line roads (public domain) come as one zip per county (1 to 12 MB; 3,233 counties and
equivalents in 2024). A road crossing a valley usually passes water through a culvert that an
elevation model does not see: the model shows the embankment as a dam, and the depression behind
it overflows there. Where a depression's spill point lies on a road, it is likely drained.
"""
import json
import urllib.parse

from ._common import check_bbox, download_once, read_bytes

COUNTIES = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/"
            "MapServer/1/query")
ROADS = "https://www2.census.gov/geo/tiger/TIGER{year}/ROADS/tl_{year}_{geoid}_roads.zip"


def _geopandas():
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Roads need geopandas: pip install 'h3-lowpoints[sources]'") from exc
    return gpd


def counties(bbox, get=read_bytes):
    """GEOIDs (five digits: state and county) of the counties a lon/lat box touches."""
    params = {"geometry": ",".join(str(v) for v in check_bbox(bbox)),
              "geometryType": "esriGeometryEnvelope", "inSR": 4326,
              "spatialRel": "esriSpatialRelIntersects", "outFields": "GEOID",
              "returnGeometry": "false", "f": "json"}
    answer = json.loads(get(f"{COUNTIES}?{urllib.parse.urlencode(params)}"))
    if "error" in answer:
        raise OSError(f"the Census TIGERweb service answered: {answer['error']}")
    return sorted(f["attributes"]["GEOID"] for f in answer.get("features", []))


def county_roads(geoid, *, cache_dir, year=2024):
    """Every road of one county (GEOID such as "48201"), in WGS 84, with the road's `MTFCC`
    class. The zip is downloaded once into `cache_dir`."""
    gpd = _geopandas()
    from pathlib import Path

    local = download_once(ROADS.format(year=year, geoid=geoid), cache_dir)
    if local is None:
        raise ValueError(f"the Census Bureau has no {year} roads file for county {geoid}")
    with open(local, "rb") as fh:
        head = fh.read(300)
    if not head.startswith(b"PK"):
        # the Census web server answers some requests with an HTML page ("Request Rejected"):
        # mostly when limiting a client, but for a few files every time (see data-sources.md).
        # Never keep that as the county's file
        Path(local).unlink()
        raise OSError(f"the Census server sent a web page instead of county {geoid}'s {year} "
                      f"roads (try again later, or another year): {head[:120]!r}")
    roads = gpd.read_file(f"zip://{local}", columns=["MTFCC"])
    return roads.to_crs(4326)[["MTFCC", "geometry"]]


def fetch_roads(bbox, out_path=None, *, cache_dir, year=2024, get=read_bytes):
    """Every road in a lon/lat box (the roads of the counties it touches, cut to the box), in
    WGS 84 with `MTFCC`. out_path: also write them to a vector file."""
    gpd = _geopandas()
    west, south, east, north = check_bbox(bbox)
    parts = [county_roads(g, cache_dir=cache_dir, year=year) for g in counties(bbox, get)]
    if not parts:
        raise ValueError("no US county touches this box")
    roads = gpd.GeoDataFrame(gpd.pd.concat(parts, ignore_index=True), crs=4326)
    roads = roads.cx[west:east, south:north].reset_index(drop=True)
    if out_path is not None:
        roads.to_file(out_path)
    return roads
