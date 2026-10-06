import numpy as np


def _geopandas():
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Reading vector files needs geopandas: pip install 'h3-lowpoints[sources]'") from exc
    return gpd


def read_drains(path, crs):
    """Read drain locations (inlets, grates) and return an (n, 2) array of x, y in `crs`.

    Accepts a vector file (GeoPackage, GeoJSON, Shapefile) or a CSV with lon/lat,
    longitude/latitude (EPSG:4326) or x/y (already in the DEM's CRS) columns.
    """
    gpd = _geopandas()
    path = str(path)
    if path.lower().endswith(".csv"):
        import pandas as pd

        df = pd.read_csv(path)
        cols = {c.lower(): c for c in df.columns}
        for xn, yn, src in (("lon", "lat", 4326), ("longitude", "latitude", 4326), ("x", "y", crs)):
            if xn in cols and yn in cols:
                gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy(df[cols[xn]], df[cols[yn]]), crs=src)
                break
        else:
            raise ValueError("CSV needs lon/lat, longitude/latitude or x/y columns")
    else:
        gdf = gpd.read_file(path)
        if gdf.crs is None:
            raise ValueError(f"{path} has no coordinate reference system")
    pts = gdf.to_crs(crs).geometry
    pts = pts[pts.geom_type == "Point"]
    if len(pts) == 0:
        raise ValueError(f"{path} contains no point geometries")
    return np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])


def read_buildings(path, crs):
    """Read building footprints and return a GeoDataFrame of polygons in `crs`."""
    gpd = _geopandas()
    gdf = gpd.read_file(str(path))
    if gdf.crs is None:
        raise ValueError(f"{path} has no coordinate reference system")
    gdf = gdf.to_crs(crs)
    return gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])]
