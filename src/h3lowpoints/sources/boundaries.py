"""Administrative boundaries for clipping results: the lower 48 US states from the US Census Bureau.

    from h3lowpoints.sources.boundaries import us_lower48
    geometry = us_lower48("us48.gpkg")            # also saved, for reuse

The Census Bureau's cartographic boundary files (1:500,000, public domain) are generalized for
mapping: along coasts and the borders the line can be a few hundred metres from the legal one,
which decides membership only for cells within that distance of it. The lower 48 states plus the
District of Columbia make up the area that 3DEP's seamless national products and NLCD cover in
one piece; Alaska, Hawaii and the territories are left out.
"""
import io
import tempfile
import zipfile
from pathlib import Path

from ._common import read_bytes

CENSUS_STATES = "https://www2.census.gov/geo/tiger/GENZ{year}/shp/cb_{year}_us_state_500k.zip"
NOT_LOWER48 = {"AK", "HI", "PR", "VI", "GU", "MP", "AS"}


def us_lower48(out_path=None, *, year=2025, data=None):
    """The lower 48 states and DC as one shapely geometry in longitude/latitude (WGS 84).

    out_path: also write it to a vector file (GeoPackage or GeoJSON), for reuse.
    data: the Census zip file's bytes (for tests or a local copy); downloaded by default.
    Needs the `sources` extra (geopandas).
    """
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Boundaries need geopandas: pip install 'h3-lowpoints[sources]'") from exc
    raw = data if data is not None else read_bytes(CENSUS_STATES.format(year=year))
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            z.extractall(tmp)
        shp = next(Path(tmp).glob("*.shp"), None)
        if shp is None:
            raise ValueError("The Census download holds no shapefile")
        states = gpd.read_file(shp)
    if "STUSPS" not in states:
        raise ValueError("The Census states file has no STUSPS column")
    lower48 = states[~states["STUSPS"].isin(NOT_LOWER48)].to_crs(4326)
    geometry = lower48.geometry.union_all()
    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame({"name": ["Lower 48 US states and DC"],
                          "source": [f"US Census Bureau, cartographic boundaries {year}, 1:500,000"]},
                         geometry=[geometry], crs=4326).to_file(out)
    return geometry
