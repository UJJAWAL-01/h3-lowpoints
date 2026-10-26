"""Mapped streams, rivers, canals and ditches: USGS National Hydrography Dataset (NHD).

    from h3lowpoints.sources.nhd import fetch_flowlines, state_flowlines
    lines = fetch_flowlines((-95.6, 29.6, -95.2, 29.9))         # a box, from the NHD map service
    lines = state_flowlines("Texas", cache_dir="nhd")           # a whole state, from its download

NHD high resolution (1:24,000 or finer) is the most detailed national map of US surface water;
public domain. Its flowlines carry a feature type, `ftype`: 460 stream or river, 558 artificial
path (the line through a lake or a wide river), 336 canal or ditch, 420 underground conduit,
428 pipeline, 334 connector, 566 coastline. NHD high resolution has not been updated since 2023,
when USGS moved to its successor, the 3D Hydrography Program; it remains the national map of
small ditches. The state downloads (one zip of 3 MB to 3.5 GB each, about 45 GB for the lower 48
states and DC) cover whole river basins that cross the state line, so neighbouring states'
files overlap.
"""
import json
import urllib.parse

from ._common import check_bbox, download_once, read_bytes

NHD_SERVICE = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6/query"
NHD_STATES = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHD/State/GPKG/"
              "NHD_H_{name}_State_GPKG.zip")
STREAM_FTYPES = (460, 558)            # stream or river, artificial path
CANAL_FTYPES = (336, 420, 428)        # canal or ditch, underground conduit, pipeline
PAGE = 2000                           # the service's largest page


def _geopandas():
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("NHD lines need geopandas: pip install 'h3-lowpoints[sources]'") from exc
    return gpd


def flowline_query(bbox, offset=0, page=PAGE):
    """The NHD map-service URL for one page of flowlines intersecting a lon/lat box."""
    params = {"where": "1=1", "geometry": ",".join(str(v) for v in check_bbox(bbox)),
              "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
              "spatialRel": "esriSpatialRelIntersects", "outFields": "ftype,fcode",
              "f": "geojson", "resultOffset": offset, "resultRecordCount": page,
              "orderByFields": "OBJECTID"}
    return f"{NHD_SERVICE}?{urllib.parse.urlencode(params)}"


def fetch_flowlines(bbox, out_path=None, *, max_lines=200_000, get=read_bytes):
    """NHD high-resolution flowlines intersecting a lon/lat box, from the NHD map service.

    Returns a GeoDataFrame in WGS 84 with `ftype`, `fcode` and line geometry. For boxes beyond
    about a degree, use `state_flowlines` (the service sends 2,000 lines per request).
    out_path: also write them to a vector file. `get` reads a URL (for tests).
    """
    gpd = _geopandas()
    frames, offset = [], 0
    while True:
        page = json.loads(get(flowline_query(bbox, offset)))
        if "error" in page:
            raise OSError(f"the NHD service answered: {page['error']}")
        features = page.get("features", [])
        if features:
            frames.append(gpd.GeoDataFrame.from_features(features, crs=4326))
        offset += len(features)
        if offset > max_lines:
            raise ValueError(f"more than {max_lines:,} NHD lines in this box; use state_flowlines")
        if len(features) < PAGE and not page.get("exceededTransferLimit"):
            break
    lines = (gpd.GeoDataFrame(gpd.pd.concat(frames, ignore_index=True), crs=4326) if frames
             else gpd.GeoDataFrame({"ftype": [], "fcode": []}, geometry=[], crs=4326))
    lines = lines[["ftype", "fcode", "geometry"]]
    if out_path is not None:
        lines.to_file(out_path)
    return lines


def state_url(state):
    """URL of a state's NHD high-resolution GeoPackage zip (`state` as named there, such as
    "Texas" or "District of Columbia")."""
    return NHD_STATES.format(name=state.replace(" ", "_"))


def state_flowlines(state, *, cache_dir, bbox=None, ftypes=None):
    """A state's NHD high-resolution flowlines, read straight from its downloaded zip.

    The zip (up to 3.5 GB) is downloaded once into `cache_dir`. `bbox` (lon/lat) reads only the
    lines intersecting it; `ftypes` only those feature types. Returns a GeoDataFrame in WGS 84
    with `ftype`, `fcode` and line geometry (heights and measures dropped).
    """
    gpd = _geopandas()
    import warnings

    import pyogrio
    import shapely

    local = download_once(state_url(state), cache_dir)
    if local is None:
        raise ValueError(f"NHD has no download for {state!r} (names are as on The National Map, "
                         "such as 'Texas' or 'District of Columbia')")
    path = f"/vsizip/{local}/{_gpkg_name(local)}"
    where = None if ftypes is None else f"ftype IN ({', '.join(str(int(f)) for f in ftypes)})"
    # the layer is in NAD83 (EPSG:4269), within a few metres of WGS 84, so a lon/lat box filters it
    with warnings.catch_warnings():              # NHD lines carry measures, which are dropped
        warnings.simplefilter("ignore", UserWarning)
        lines = pyogrio.read_dataframe(path, layer="NHDFlowline", columns=["ftype", "fcode"],
                                       bbox=None if bbox is None else tuple(check_bbox(bbox)),
                                       where=where)
    lines = lines.to_crs(4326) if lines.crs is not None else lines.set_crs(4326)
    lines["geometry"] = shapely.force_2d(lines.geometry.values)
    return gpd.GeoDataFrame(lines[["ftype", "fcode", "geometry"]], crs=4326)


def _gpkg_name(zip_path):
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".gpkg")]
    if len(names) != 1:
        raise ValueError(f"expected one GeoPackage in {zip_path}, found {names}")
    return names[0]
