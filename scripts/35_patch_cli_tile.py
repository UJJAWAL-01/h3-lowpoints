from pathlib import Path

p = Path("src/h3lowpoints/cli.py")
s = p.read_text(encoding="utf-8")
pairs = [
    ('    a.add_argument("--min-depth", type=float, default=0.15, help="minimum depression depth in metres")\n',
     '    a.add_argument("--min-depth", type=float, default=0.15, help="minimum depression depth in metres")\n'
     '    a.add_argument("--tile", type=int, default=0,\n'
     '                   help="process in tiles of this many cells (for large DEMs); 0 = whole raster")\n'
     '    a.add_argument("--buffer", type=int, default=256, help="tile overlap in cells (default 256)")\n'),
    ('        result = analyze(args.dem, drains_xy=drains, res=args.res,\n'
     '                         min_depth=args.min_depth, buildings=buildings)\n',
     '        if args.tile:\n'
     '            from .tiling import analyze_tiled\n\n'
     '            result = analyze_tiled(args.dem, drains_xy=drains, buildings=buildings,\n'
     '                                   tile=args.tile, buffer=args.buffer, res=args.res,\n'
     '                                   min_depth=args.min_depth)\n'
     '        else:\n'
     '            result = analyze(args.dem, drains_xy=drains, res=args.res,\n'
     '                             min_depth=args.min_depth, buildings=buildings)\n'),
]
for old, new in pairs:
    assert s.count(old) == 1, "pattern not found exactly once: " + old[:60]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("patched cli.py")
