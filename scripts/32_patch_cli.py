from pathlib import Path


def patch(path, pairs):
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert s.count(old) == 1, f"{path}: pattern not found exactly once: {old[:60]!r}"
        s = s.replace(old, new)
    p.write_text(s, encoding="utf-8")
    print("patched", path)


patch("src/h3lowpoints/pipeline.py", [(
    "    invalid = np.isnan(arr)\n",
    '    units = (crs.linear_units or "").lower() if crs is not None and crs.is_projected else ""\n'
    '    if units not in ("metre", "meter"):\n'
    "        raise ValueError(\n"
    '            f"The DEM needs a projected CRS in metres (for example a UTM zone), got {crs}. "\n'
    '            "Reproject it first, for example with gdalwarp -t_srs EPSG:xxxxx."\n'
    "        )\n"
    "    invalid = np.isnan(arr)\n")])

patch("pyproject.toml", [(
    "[project.urls]\n",
    '[project.scripts]\nh3lowpoints = "h3lowpoints.cli:main"\n\n[project.urls]\n')])

patch(".github/workflows/ci.yml", [(
    'pip install -e ".[dev]"', 'pip install -e ".[sources,dev]"')])

cli_doc = (
    "## Command line\n\n"
    "```\n"
    "h3lowpoints analyze dem.tif --drains drains.csv --buildings buildings.gpkg --out results/\n"
    "```\n\n"
    "Drains: a point file (GeoPackage, GeoJSON, Shapefile) or a CSV with `x,y` (in the DEM's CRS) or\n"
    "`lon,lat` columns. Buildings: a polygon file. The DEM must be in a projected CRS with metres.\n"
    "Outputs: `depressions.csv`, `cells.csv` and `meta.json` (parameters and versions).\n\n"
)
marker = "## What we measured (and what we didn't)\n"
patch("README.md", [(marker, cli_doc + marker)])
