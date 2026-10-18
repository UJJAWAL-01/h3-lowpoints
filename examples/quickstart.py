"""Run h3-lowpoints end to end on a small synthetic DEM, with no downloads.

    python examples/quickstart.py [output_folder]

It builds a 1 m terrain with three depressions (a deep pit, a broad sag and a pit under a
"building"), analyzes it, rolls the cells up to a coarser H3 resolution and writes the results.
With the `viz` extra installed it also writes a 3D map, map.html.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from h3lowpoints import analyze


def make_dem(path):
    """A gently sloping 1 m DEM in UTM zone 14N (central Texas) with three depressions."""
    y, x = np.mgrid[0:300, 0:300]
    arr = 100.0 - 0.01 * x - 0.005 * y
    arr[50:70, 60:90] -= 0.9      # a sag
    arr[150:158, 200:208] -= 2.0  # a small deep pit
    arr[220:240, 40:60] -= 0.6    # a depression we will say lies under a building
    profile = {"driver": "GTiff", "height": 300, "width": 300, "count": 1, "dtype": "float32",
               "crs": "EPSG:32614", "transform": from_origin(620000, 3350000, 1, 1),
               "nodata": -9999.0}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype("float32"), 1)
    return path


def main(out_dir=None):
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="h3lowpoints_"))
    out.mkdir(parents=True, exist_ok=True)
    dem = make_dem(out / "dem.tif")

    drains = np.array([[620075.0, 3349940.0]])                   # one inlet next to the sag
    building = [box(620035, 3349755, 620065, 3349785)]           # footprint over the third pit
    res = analyze(str(dem), res=12, drains_xy=drains, buildings=building)

    print(res.depressions[["max_depth_m", "area_m2", "drain_at_core", "building_overlap"]].round(2))
    coarse = res.at_resolution(10)
    print(f"{len(res.cells)} cells at H3 res 12, {len(coarse)} after rolling up to res 10")
    res.save(out)
    try:
        res.to_deck().to_html(str(out / "map.html"), open_browser=False)
    except ImportError:
        print("Install the viz extra (pip install 'h3-lowpoints[viz]') for the 3D map.")
    print("written to", out)
    return res


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
