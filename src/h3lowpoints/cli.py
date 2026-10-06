import argparse
from importlib import metadata

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
    a.add_argument("dem", help="GeoTIFF in a projected CRS with metre units")
    a.add_argument("--drains", help="inlet/grate points: GeoPackage, GeoJSON, Shapefile or CSV")
    a.add_argument("--buildings", help="building footprint polygons")
    a.add_argument("--out", default="h3lowpoints_out", help="output folder (default h3lowpoints_out)")
    a.add_argument("--res", type=int, default=11, help="H3 resolution (default 11)")
    a.add_argument("--min-depth", type=float, default=0.15, help="minimum depression depth in metres")
    a.add_argument("--tile", type=int, default=0,
                   help="process in tiles of this many cells (for large DEMs); 0 = whole raster")
    a.add_argument("--buffer", type=int, default=256, help="tile overlap in cells (default 256)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    from .pipeline import analyze
    from .readers import read_buildings, read_drains

    try:
        with rasterio.open(args.dem) as src:
            crs = src.crs
        drains = read_drains(args.drains, crs) if args.drains else None
        buildings = read_buildings(args.buildings, crs) if args.buildings else None
        if args.tile:
            from .tiling import analyze_tiled

            result = analyze_tiled(args.dem, drains_xy=drains, buildings=buildings,
                                   tile=args.tile, buffer=args.buffer, res=args.res,
                                   min_depth=args.min_depth)
        else:
            result = analyze(args.dem, drains_xy=drains, res=args.res,
                             min_depth=args.min_depth, buildings=buildings)
    except (ValueError, OSError, ImportError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    out = result.save(args.out)
    print(f"{result.meta['n_depressions']} depressions written to {out}")
    for name, n in sorted(result.meta.get("class_counts", {}).items()):
        print(f"  {name}: {n}")
    return 0
