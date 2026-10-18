import argparse
from importlib import metadata
from pathlib import Path

import rasterio


def _version():
    try:
        return metadata.version("h3-lowpoints")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="h3lowpoints",
        description="Find closed depressions in a DEM and describe them on H3 cells.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="analyze a DEM")
    a.add_argument("dem", help="GeoTIFF in a projected CRS or in lon/lat degrees")
    a.add_argument("--reproject", action="store_true",
                   help="first reproject the DEM to its UTM zone (not needed: lon/lat DEMs are "
                        "analyzed natively, and resampling blurs sharp edges)")
    a.add_argument("--drains", help="inlet/grate points: GeoPackage, GeoJSON, Shapefile or CSV")
    a.add_argument("--buildings", help="building footprint polygons")
    a.add_argument("--out", default="h3lowpoints_out", help="output folder (default h3lowpoints_out)")
    a.add_argument("--res", type=int, default=11, help="H3 resolution (default 11)")
    a.add_argument("--min-depth", type=float, default=0.15, help="minimum depression depth in metres")
    a.add_argument("--tile", type=int, default=0,
                   help="process in tiles of this many cells (for DEMs larger than memory); "
                        "0 = whole raster. The result does not depend on the tile size")

    a.add_argument("--map", help="also write an interactive web map to this .html file")

    m = sub.add_parser("map", help="make an interactive web map from a results folder")
    m.add_argument("results", help="folder written by 'analyze' (depressions.csv, cells.csv, meta.json)")
    m.add_argument("--out", default="map.html", help="output .html file (default map.html)")
    m.add_argument("--title", help="title shown on the map")

    f = sub.add_parser("fetch-dem", help="download a Copernicus GLO-30 (30 m, global) DEM")
    f.add_argument("--bbox", nargs=4, type=float, required=True,
                   metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="bounding box in degrees")
    f.add_argument("--out", required=True, help="output GeoTIFF")
    f.add_argument("--source", help="folder with tiles instead of the public AWS bucket")
    f.add_argument("--max-tiles", type=int, default=16, help="refuse requests above this many 1-degree tiles")
    return parser


def _fetch(args):
    from .sources.copernicus import fetch_dem

    out = fetch_dem(tuple(args.bbox), args.out, source=args.source, max_tiles=args.max_tiles)
    with rasterio.open(out) as src:
        print(f"{src.width} x {src.height} pixels written to {out}")
    return 0


def _analyze(args):
    from .context.readers import read_buildings, read_drains
    from .pipeline import analyze

    dem = args.dem
    if args.reproject:
        import tempfile

        from .dem_io import reproject_to_utm

        dem = reproject_to_utm(args.dem, Path(tempfile.mkdtemp()) / "dem_utm.tif")
    with rasterio.open(dem) as src:
        crs = src.crs
    drains = read_drains(args.drains, crs) if args.drains else None
    buildings = read_buildings(args.buildings, crs) if args.buildings else None
    if args.tile:
        from .tiling import analyze_tiled

        result = analyze_tiled(dem, drains_xy=drains, buildings=buildings, tile=args.tile,
                               res=args.res, min_depth=args.min_depth)
    else:
        result = analyze(dem, drains_xy=drains, res=args.res, min_depth=args.min_depth,
                         buildings=buildings)
    out = result.save(args.out)
    print(f"{result.meta['n_depressions']} depressions written to {out}")
    print(f"  {result.meta['n_edge']} cut off by the data border")
    if args.map:
        print(f"map written to {result.to_html(args.map)}")
    return 0


def _map(args):
    from .viz.webmap import map_from_dir

    print(f"map written to {map_from_dir(args.results, args.out, title=args.title)}")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return {"analyze": _analyze, "fetch-dem": _fetch, "map": _map}[args.command](args)
    except (ValueError, OSError, ImportError) as exc:
        raise SystemExit(f"error: {exc}") from exc
