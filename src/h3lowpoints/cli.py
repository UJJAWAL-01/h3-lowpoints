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
    a.add_argument("--res", default="auto",
                   help="H3 resolution 0-15, or 'auto' (default) for the finest the DEM supports")
    a.add_argument("--min-depth", type=float, default=0.15, help="minimum depression depth in metres")
    a.add_argument("--tile", type=int, default=0,
                   help="process in tiles of this many pixels (for DEMs larger than memory), writing "
                        "the tables as they are produced; 0 = whole raster (default). The result "
                        "does not depend on the tile size")

    a.add_argument("--format", default="csv", choices=["csv", "parquet"],
                   help="table format: csv (default) or parquet (needs pyarrow)")
    a.add_argument("--map", help="also write an interactive web map to this .html file")
    a.add_argument("--workers", type=int, default=1,
                   help="label pixels with H3 cells in this many processes: the same result in "
                        "less time (4 is a good choice on most laptops)")
    a.add_argument("--details", action="store_true",
                   help="also describe each depression further: ground and water levels, length, "
                        "width and orientation, the area and volume as the water drops, and a "
                        "depression_cells table on H3 cells")
    a.add_argument("--catchments", action="store_true",
                   help="also follow the water: each depression's catchment area, the depression it "
                        "overflows into and where, and a catchments table on H3 cells")

    m = sub.add_parser("map", help="make an interactive web map from a results folder")
    m.add_argument("results", help="folder written by 'analyze' or 'build'")
    m.add_argument("--out", default="map.html", help="output .html file (default map.html)")
    m.add_argument("--title", help="title shown on the map")

    f = sub.add_parser("fetch-dem", help="download a DEM for a box: global 30 m, or US 30 m, 10 m or 1 m")
    _bbox_out(f)
    f.add_argument("--from", dest="dataset", default="copernicus",
                   choices=["copernicus", "3dep", "3dep10", "lidar"],
                   help="copernicus: global 30 m surface model (default); 3dep: US 30 m bare earth; "
                        "3dep10: US 10 m bare earth; lidar: US 1 m lidar")
    f.add_argument("--source", help="folder with tiles instead of the public bucket (not for lidar)")
    f.add_argument("--max-tiles", type=int, default=16, help="refuse boxes touching more 1-degree tiles")

    lc = sub.add_parser("fetch-landcover", help="download land cover for a box")
    _bbox_out(lc)
    lc.add_argument("--from", dest="dataset", default="worldcover",
                    choices=["worldcover", "nlcd", "nlcd-landcover", "soil-groups", "curve-numbers"],
                    help="worldcover: global 10 m classes (default); nlcd: US 30 m percent "
                         "impervious; nlcd-landcover: US 30 m land cover classes; soil-groups: US "
                         "soil hydrologic groups (gNATSGO); curve-numbers: US 30 m SCS curve "
                         "numbers from NLCD and the soil groups")
    lc.add_argument("--year", type=int, help="NLCD year (default 2024 from Annual NLCD, 2021 from MRLC)")
    lc.add_argument("--nlcd-source", choices=["annual", "mrlc"], default="annual",
                    help="annual: Annual NLCD 1985-2024 (default); mrlc: the older NLCD editions "
                         "from the MRLC web service (2001-2021)")
    lc.add_argument("--cache-dir", help="soil-groups and curve-numbers: keep the soil table here "
                                        "(default: the output's folder)")

    b = sub.add_parser("build", help="analyze a US region of any size from USGS 3DEP (streams the tiles)")
    b.add_argument("--bbox", nargs=4, type=float, required=True,
                   metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="bounding box in degrees")
    b.add_argument("--out", required=True, help="output folder")
    b.add_argument("--from", dest="dataset", default="3dep", choices=["3dep", "3dep10"],
                   help="3dep: 30 m bare earth (default); 3dep10: 10 m bare earth")
    b.add_argument("--res", default="auto", help="H3 resolution 0-15, or 'auto' (default)")
    b.add_argument("--min-depth", type=float, default=0.15, help="minimum depression depth in metres")
    b.add_argument("--tile", type=int, default=4096, help="tile size in pixels (memory, not result)")
    b.add_argument("--cache-dir", help="download each 3DEP tile here once instead of streaming it")
    b.add_argument("--format", default="csv", choices=["csv", "parquet"],
                   help="table format: csv (default) or parquet (needs pyarrow; smaller and faster)")
    b.add_argument("--water-mask", action="store_true",
                   help="treat permanent water (ESA WorldCover) as open water, not terrain: lakes and "
                        "reservoirs then drain the land around them instead of filling as depressions")
    b.add_argument("--hours", type=float, metavar="H",
                   help="stop after about H hours, at the end of a tile, and exit with code 3; run the "
                        "same command again to continue (every tile is saved as it finishes)")
    b.add_argument("--spot-check", type=int, default=0, metavar="N",
                   help="also re-run N random windows tiled and whole and require identical answers")
    b.add_argument("--map", help="also write an interactive web map to this .html file")
    b.add_argument("--workers", type=int, default=1,
                   help="label pixels with H3 cells in this many processes: the same result in "
                        "less time (4 is a good choice on most laptops)")
    b.add_argument("--details", action="store_true",
                   help="also describe each depression further: ground and water levels, length, "
                        "width and orientation, the area and volume as the water drops, and a "
                        "depression_cells table on H3 cells")
    b.add_argument("--catchments", action="store_true",
                   help="also follow the water: each depression's catchment area, the depression it "
                        "overflows into and where, and a catchments table on H3 cells")

    c = sub.add_parser("clip", help="keep the part of a saved result inside a boundary (for example the US)")
    c.add_argument("results", help="folder written by 'analyze' or 'build'")
    c.add_argument("--boundary", required=True,
                   help="a vector file (GeoPackage, GeoJSON, Shapefile), or 'us48' for the lower 48 US "
                        "states and DC from the US Census Bureau")
    c.add_argument("--out", required=True, help="output folder for the clipped result")
    c.add_argument("--format", choices=["csv", "parquet"], help="table format (default: as the input)")

    v = sub.add_parser("verify", help="check a saved result against invariants that always hold")
    v.add_argument("results", help="folder written by 'analyze' or 'build'")
    v.add_argument("--dem", help="also spot-check this DEM: tiled and whole runs on random windows")
    v.add_argument("--windows", type=int, default=3, help="windows for the spot check (default 3)")

    val = sub.add_parser("validate", help="do observations (flood reports, claims, ...) fall in or near "
                                          "depressions more often than chance?")
    val.add_argument("results", help="folder written by 'analyze', 'build' or 'clip'")
    val.add_argument("--observations", required=True,
                     help="observed locations: CSV with lon/lat columns, or a vector file")
    val.add_argument("--controls", help="where observations could have come from (addresses, "
                                        "buildings), in the same forms")
    val.add_argument("--boundary", help="the area studied (vector file): points outside are left "
                                        "out, and random controls are placed in it without --controls")
    val.add_argument("--n-random", type=int, metavar="N",
                     help="random controls without --controls (default 10 per observation, at least 1000)")
    val.add_argument("--max-rings", type=int, default=5, help="largest distance, in H3 rings (default 5)")
    val.add_argument("--classes", help="a label per depression (.csv or .parquet with id and class "
                                       "columns): also split the lift by class and compare classes")
    val.add_argument("--class-order", metavar="A,B,...",
                     help="the classes in order, comma-separated (default: sorted)")
    val.add_argument("--block-res", type=int, default=6,
                     help="H3 resolution of the bootstrap blocks (default 6)")
    val.add_argument("--boot", type=int, default=1000, help="bootstrap samples (default 1000)")
    val.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    val.add_argument("--keep-suspect", action="store_true",
                     help="keep the ground of depressions flagged deeper_than_wide (left out by default)")
    val.add_argument("--out", default="validation.json", help="report file (default validation.json)")
    val.add_argument("--points", help="also write the per-point table (.csv or .parquet)")

    ix = sub.add_parser("index", help="how easily each depression fills: runoff, rain to fill and "
                                      "design storms (needs a result made with --catchments)")
    ix.add_argument("results", help="folder written by 'analyze', 'build' or 'clip' with --catchments")
    ix.add_argument("--curve-numbers",
                    help="SCS curve numbers: a raster (US: 'fetch-landcover --from curve-numbers'), "
                         "or a .csv/.parquet table of h3 and curve_number sorted by h3")
    ix.add_argument("--curve-number", type=float,
                    help="instead of --curve-numbers: one curve number (0 to 100) for all ground")
    ix.add_argument("--impervious",
                    help="optional: percent paved (raster or table of h3 and impervious_pct), "
                         "reported as impervious_pct")
    ix.add_argument("--rain",
                    help="design storms: 'atlas14' (US: 24-hour storms of 1 to 1000 years from NOAA "
                         "Atlas 14) or one uniform depth in mm; without it, only the rain-to-fill "
                         "columns")
    ix.add_argument("--min-coverage", type=float, default=0.9,
                    help="share of a catchment an input must cover (default 0.9)")
    ix.add_argument("--cache-dir", help="keep downloaded rainfall grids here")
    ix.add_argument("--workers", type=int, default=1,
                    help="label raster pixels with H3 cells in this many processes")

    dr = sub.add_parser("drainage", help="drained or landlocked: mapped channels and roads near each "
                                         "depression, and its land cover")
    dr.add_argument("results", help="folder written by 'analyze', 'build' or 'clip'")
    dr.add_argument("--streams", help="stream and river lines (vector file, any CRS)")
    dr.add_argument("--canals", help="canal, ditch, conduit and pipeline lines (vector file, any CRS)")
    dr.add_argument("--roads", help="road lines (vector file, any CRS)")
    dr.add_argument("--nhd", action="store_true",
                    help="US: fetch streams and canals from the USGS National Hydrography Dataset "
                         "for the result's area (instead of --streams and --canals)")
    dr.add_argument("--tiger", action="store_true",
                    help="US: fetch highways and ramps (TIGER classes S1100, S1200, S1630) from "
                         "Census TIGER/Line for the result's area (instead of --roads; needs "
                         "--cache-dir)")
    dr.add_argument("--all-roads", action="store_true",
                    help="with --tiger: every road, not only highways and ramps")
    dr.add_argument("--land-cover", help="raster of land cover classes, read at each lowest point "
                                         "(for example from 'fetch-landcover')")
    dr.add_argument("--near-m", type=float, default=30.0,
                    help="a channel this close to the lowest point, or a road this close to the "
                         "spill point, drains the depression (default 30)")
    dr.add_argument("--min-share", type=float,
                    help="also count as drained when channels cross cells holding at least this "
                         "share of its area (off by default)")
    dr.add_argument("--max-distance-m", type=float, default=1000.0,
                    help="distances beyond this are left empty (default 1000)")
    dr.add_argument("--cache-dir", help="keep downloaded road files here")

    r = sub.add_parser("rainfall", help="design rainfall from NOAA Atlas 14 for a point in the US")
    r.add_argument("lat", type=float)
    r.add_argument("lon", type=float)
    r.add_argument("--duration", help="one duration, for example 60-min or 24-hr")
    r.add_argument("--years", type=int, help="one return period in years, for example 10 or 100")
    return parser


def _bbox_out(parser):
    parser.add_argument("--bbox", nargs=4, type=float, required=True,
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="bounding box in degrees")
    parser.add_argument("--out", required=True, help="output GeoTIFF")


def _fetch(args):
    bbox = tuple(args.bbox)
    if args.dataset == "copernicus":
        from .sources.copernicus import fetch_dem

        out = fetch_dem(bbox, args.out, source=args.source, max_tiles=args.max_tiles)
    elif args.dataset in ("3dep", "3dep10"):
        from .sources.usgs import fetch_3dep

        out = fetch_3dep(bbox, args.out, resolution=30 if args.dataset == "3dep" else 10,
                         source=args.source, max_tiles=args.max_tiles)
    else:
        from .sources.usgs import fetch_lidar

        out = fetch_lidar(bbox, args.out)
    with rasterio.open(out) as src:
        unit = "degrees" if src.crs.is_geographic else (src.crs.linear_units or "map units")
        print(f"{src.width} x {src.height} pixels ({src.res[0]:.6g} {unit} each) written to {out}")
    return 0


def _landcover(args):
    from .sources import landcover

    bbox = tuple(args.bbox)
    if args.dataset == "worldcover":
        out = landcover.fetch_worldcover(bbox, args.out)
    elif args.dataset == "nlcd":
        out = landcover.fetch_impervious(bbox, args.out, year=args.year, source=args.nlcd_source)
    elif args.dataset == "nlcd-landcover":
        out = landcover.fetch_land_cover(bbox, args.out, year=args.year, source=args.nlcd_source)
    elif args.dataset == "soil-groups":
        from .sources.soils import fetch_soil_groups

        out = fetch_soil_groups(bbox, args.out, cache_dir=args.cache_dir or Path(args.out).parent)
    else:
        from .runoff import fetch_curve_numbers

        out = fetch_curve_numbers(bbox, args.out, cache_dir=args.cache_dir or Path(args.out).parent,
                                  year=args.year, source=args.nlcd_source)
    with rasterio.open(out) as src:
        print(f"{src.width} x {src.height} pixels written to {out}")
    return 0


def _rainfall(args):
    from .sources.rainfall import design_rainfall, rainfall_depth

    table = design_rainfall(args.lat, args.lon)
    if args.duration or args.years:
        depth = rainfall_depth(args.lat, args.lon, args.duration or "60-min", args.years or 10,
                               table=table)
        print(f"{depth:g} mm")
    else:
        print("NOAA Atlas 14 depths in mm (rows: duration, columns: return period in years)")
        print(table.to_string())
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

        # tables go straight to the output folder, so their size is not limited by memory
        result = analyze_tiled(dem, drains_xy=drains, buildings=buildings, tile=args.tile,
                               res=args.res, min_depth=args.min_depth, out_dir=args.out,
                               format=args.format, catchments=args.catchments,
                               details=args.details, workers=args.workers)
        out = result.path
    else:
        result = analyze(dem, drains_xy=drains, res=args.res, min_depth=args.min_depth,
                         buildings=buildings, catchments=args.catchments,
                         details=args.details, workers=args.workers)
        out = result.save(args.out, format=args.format)
    print(f"{result.meta['n_depressions']} depressions on H3 resolution {result.res} written to {out}")
    print(f"  {result.meta['n_edge']} cut off by the data border")
    if args.map:
        print(f"map written to {result.load().to_html(args.map)}")
    return 0


def _build(args):
    import json
    import time

    from .sources.usgs import build_3dep_vrt
    from .store import write_meta
    from .tiling import Paused, analyze_tiled
    from .verify import check_saved, spot_check

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    timings = {}
    start = time.perf_counter()
    vrt = build_3dep_vrt(tuple(args.bbox), out / "dem.vrt", resolution=30 if args.dataset == "3dep" else 10,
                         cache_dir=args.cache_dir)
    timings["tiles"] = time.perf_counter() - start
    began = time.perf_counter()                   # --hours covers the mask and the analysis
    mask = None
    try:
        if args.water_mask:
            from .sources.landcover import build_water_mask

            mask = out / "water.vrt"
            if not mask.exists():                 # built once, tile by tile; later parts reuse it
                start = time.perf_counter()
                print("building the water mask from ESA WorldCover", flush=True)
                build_water_mask(vrt, mask, cache_dir=args.cache_dir, tile=args.tile,
                                 hours=args.hours, verbose=True)
                timings["water_mask"] = time.perf_counter() - start
        hours = None
        if args.hours is not None:
            hours = args.hours - (time.perf_counter() - began) / 3600
            if hours <= 0:
                print("part finished: the time limit was used building the water mask. "
                      "Run the same command again to continue.")
                return 3
        start = time.perf_counter()
        # the tables go to disk as they are produced, so the region is not limited by memory
        result = analyze_tiled(str(vrt), tile=args.tile, res=args.res, min_depth=args.min_depth,
                               verbose=True, out_dir=out, format=args.format, hours=hours,
                               mask_path=None if mask is None else str(mask),
                               catchments=args.catchments, details=args.details,
                               workers=args.workers)
    except Paused as stop:
        print(f"part finished ({100 * stop.done / stop.total:.0f}% of this stage done): {stop}")
        return 3
    timings["analysis"] = time.perf_counter() - start
    result.meta["build"] = {"dataset": args.dataset, "bbox": list(args.bbox),
                            "seconds": {k: round(v, 1) for k, v in timings.items()}}
    if mask is not None:
        from .sources.landcover import WATER_RULE

        result.meta["build"]["water_mask"] = f"{WATER_RULE}; file water.vrt"
    write_meta(out, result.meta)
    problems = check_saved(out)
    if args.spot_check:
        problems += spot_check(str(vrt), windows=args.spot_check, res=result.res, min_depth=args.min_depth)
    (out / "verify.json").write_text(json.dumps({"problems": problems}, indent=2))
    print(f"{result.meta['n_depressions']:,} depressions on H3 resolution {result.res} written to {out} "
          f"({timings['tiles']:.0f} s for tiles, {timings['analysis']:.0f} s for the analysis)")
    print("verification: " + ("all invariants hold" if not problems else f"{len(problems)} problem(s), see verify.json"))
    if args.map:
        print(f"map written to {result.load().to_html(args.map)}")
    return 0 if not problems else 1


def _clip(args):
    import json

    from .clip import clip_result
    from .verify import check_saved

    boundary, name = args.boundary, None
    if args.boundary == "us48":
        from .sources.boundaries import us_lower48

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        boundary = out / "boundary_us48.gpkg"
        if not boundary.exists():
            us_lower48(boundary)
        name = "Lower 48 US states and DC (US Census Bureau cartographic boundaries, 1:500,000)"
    out = clip_result(args.results, boundary, args.out, format=args.format, name=name)
    meta = json.loads((out / "meta.json").read_text())
    print(f"{meta['n_depressions']:,} of {meta['clip']['depressions_before']:,} depressions and "
          f"{meta['output']['n_cells']:,} of {meta['clip']['cells_before']:,} cells kept, written to {out}")
    problems = check_saved(out)
    print("verification: " + ("all invariants hold" if not problems else f"{len(problems)} problem(s): {problems}"))
    return 0 if not problems else 1


def _verify(args):
    from .verify import check_saved, spot_check

    problems = check_saved(args.results)
    if args.dem:
        import json

        meta = json.loads((Path(args.results) / "meta.json").read_text())
        problems += spot_check(args.dem, windows=args.windows, res=meta["params"]["res"],
                               min_depth=meta["params"]["min_depth"])
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print("  - " + p)
        return 1
    print("all invariants hold" + (f"; tiled and whole agree on {args.windows} random windows" if args.dem else ""))
    return 0


def _validate(args):
    import warnings

    from .validation import validate

    with warnings.catch_warnings():              # printed below, once, from the report
        warnings.simplefilter("ignore", UserWarning)
        result = validate(args.results, args.observations, args.controls, boundary=args.boundary,
                          n_random=args.n_random, max_rings=args.max_rings, classes=args.classes,
                          class_order=args.class_order and args.class_order.split(","),
                          block_res=args.block_res, n_boot=args.boot, seed=args.seed,
                          exclude_suspect=not args.keep_suspect)
    print(result.summary())
    for note in result.warnings:
        print(f"\nwarning: {note}")
    print(f"\nreport written to {result.save(args.out, points=args.points)}")
    if args.points:
        print(f"points written to {args.points}")
    return 0


def _index(args):
    import numpy as np

    from .susceptibility import susceptibility, write_susceptibility
    from .verify import check_saved

    rain = args.rain
    if rain is not None and rain != "atlas14":
        try:
            rain = float(rain)
        except ValueError:
            raise ValueError(f"--rain must be 'atlas14' or a depth in mm, got {rain!r}") from None
    table = susceptibility(args.results, curve_numbers=args.curve_numbers,
                           curve_number=args.curve_number, impervious=args.impervious, rain=rain,
                           min_coverage=args.min_coverage, cache_dir=args.cache_dir,
                           workers=args.workers)
    path = write_susceptibility(args.results, table)
    n = len(table)
    known = int(np.isfinite(table["rain_to_fill_mm"]).sum())
    print(f"{n:,} depressions; rain to fill known for {known:,}; median "
          f"{np.nanmedian(table['rain_to_fill_mm']) if known else float('nan'):.0f} mm "
          f"(own catchment), {np.nanmedian(table['rain_to_fill_upstream_mm']) if known else float('nan'):.0f}"
          f" mm (with water from upstream)")
    if "fills_in_years" in table:
        f = table["fills_in_years"]
        print(f"design storms known for {int(f.notna().sum()):,}; filled by the 2-year storm: "
              f"{int((f <= 2).sum()):,}, by the 100-year storm: {int((f <= 100).sum()):,}")
    elif "fill_ratio" in table:
        print(f"filled by the storm: {int((table['fill_ratio'] >= 1).sum()):,}")
    print(f"written to {path}")
    problems = check_saved(args.results)
    print("verification: " + ("all invariants hold" if not problems else f"{len(problems)} problem(s): {problems}"))
    return 0 if not problems else 1


def _drainage(args):
    from .drainage import drainage_flags
    from .susceptibility import write_susceptibility
    from .verify import check_saved

    streams, canals, roads = args.streams, args.canals, args.roads
    if args.nhd or args.tiger:
        bbox = _result_bbox(args.results)
    if args.nhd:
        from .sources.nhd import CANAL_FTYPES, STREAM_FTYPES, fetch_flowlines

        lines = fetch_flowlines(bbox)
        streams = lines[lines["ftype"].isin(STREAM_FTYPES)]
        canals = lines[lines["ftype"].isin(CANAL_FTYPES)]
        print(f"NHD: {len(streams):,} stream and {len(canals):,} canal lines")
    if args.tiger:
        if not args.cache_dir:
            raise ValueError("--tiger needs --cache-dir (one road file per county is downloaded)")
        from .sources.roads import fetch_roads

        from .drainage import MAJOR_ROADS

        roads = fetch_roads(bbox, cache_dir=args.cache_dir)
        if not args.all_roads:
            roads = roads[roads["MTFCC"].isin(MAJOR_ROADS)]
        print(f"TIGER: {len(roads):,} {'road' if args.all_roads else 'highway and ramp'} lines")
    table = drainage_flags(args.results, streams=streams, canals=canals, roads=roads,
                           land_cover=args.land_cover, near_m=args.near_m,
                           min_share=args.min_share, max_distance_m=args.max_distance_m)
    path = write_susceptibility(args.results, table)
    n, drained = len(table), int(table["drained"].sum())
    print(f"{n:,} depressions: {drained:,} drained, {n - drained:,} landlocked "
          f"(no channel within {args.near_m:g} m, no road at the spill point)")
    print(f"written to {path}")
    problems = check_saved(args.results)
    print("verification: " + ("all invariants hold" if not problems else f"{len(problems)} problem(s): {problems}"))
    return 0 if not problems else 1


def _result_bbox(folder, margin=0.01):
    """Lon/lat box around a saved result's lowest and spill points, with a margin in degrees."""
    import pandas as pd

    from .store import iter_table

    cols = ["lat", "lon", "spill_lat", "spill_lon"]
    d = pd.concat(list(iter_table(folder, "depressions", columns=cols)), ignore_index=True)
    if len(d) == 0:
        raise ValueError(f"{folder} has no depressions")
    lat = pd.concat([d[c] for c in ("lat", "spill_lat") if c in d])
    lon = pd.concat([d[c] for c in ("lon", "spill_lon") if c in d])
    return (lon.min() - margin, lat.min() - margin, lon.max() + margin, lat.max() + margin)


def _map(args):
    from .viz.webmap import map_from_dir

    print(f"map written to {map_from_dir(args.results, args.out, title=args.title)}")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        commands = {"analyze": _analyze, "fetch-dem": _fetch, "map": _map,
                    "fetch-landcover": _landcover, "rainfall": _rainfall, "build": _build,
                    "verify": _verify, "clip": _clip, "validate": _validate, "index": _index,
                    "drainage": _drainage}
        return commands[args.command](args)
    except (ValueError, OSError, ImportError) as exc:
        raise SystemExit(f"error: {exc}") from exc
