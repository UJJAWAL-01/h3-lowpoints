p = "src/h3lowpoints/sources/assets.py"
s = open(p, encoding="utf-8").read()
s = s.replace('def load_clipped(name, bbox_lonlat, crs, folder="data/assets", pad=0.005):',
              'def load_clipped(name, bbox_lonlat, crs, folder="data/assets", pad=0.005, tag="pilot"):')
s = s.replace('cache = folder / f"pilot_{name}.gpkg"', 'cache = folder / f"{tag}_{name}.gpkg"')
s += """

def points_to_xy(points, precision=0.5):
    \"\"\"Unique (x, y) array from a GeoDataFrame of points, rounded to `precision` map units.\"\"\"
    xy = np.array([(g.x, g.y) for g in points.geometry], dtype="float64").reshape(-1, 2)
    if len(xy) == 0:
        return xy
    _, keep = np.unique(np.round(xy / precision) * precision, axis=0, return_index=True)
    return xy[np.sort(keep)]
"""
open(p, "w", encoding="utf-8").write(s)
print("patched")
