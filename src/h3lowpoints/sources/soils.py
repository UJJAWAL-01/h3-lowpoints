"""Soil hydrologic groups for the conterminous US, from the USDA soil survey (gNATSGO).

    from h3lowpoints.sources.soils import fetch_soil_groups
    fetch_soil_groups((-95.6, 29.6, -95.2, 29.9), "soil.tif", cache_dir="soils")

A soil's hydrologic group says how easily rain soaks into it when wet: A (sand, gravel; soaks in
fast), B, C, D (clay, shallow soil over rock, a high water table; sheds most rain). Dual groups
(A/D, B/D, C/D) are wet soils: the first letter if artificially drained, D if not.

The groups come from the gridded National Soil Survey Geographic Database (gNATSGO, USDA NRCS):
a 10 m grid of soil map units (`mukey`) for the lower 48 states, and its map-unit table
(`muaggatt`), whose `hydgrpdcd` gives each unit's group (dominant condition). The library reads
the July 2020 edition published on Microsoft's Planetary Computer (CC0, no account: a read token
is issued anonymously by its public API). The grid is read by parts over the internet, at its
full 10 m resolution.

Output codes (GROUPS): 1 A, 2 B, 3 C, 4 D, 5 A/D, 6 B/D, 7 C/D; 8 a map unit with no group
(urban land, water, rock outcrop and the like); 0 no data (outside the survey).
"""
import json
import math
import time
import urllib.parse

import numpy as np

from ._common import check_bbox, read_bytes, write_raster

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/soils/gnatsgo"
TABLE = "https://soils.blob.core.windows.net/gnatsgo/july2020/parquet/muaggatt.parquet/part.0.parquet"
COLLECTION = "gnatsgo-rasters"
GROUPS = {1: "A", 2: "B", 3: "C", 4: "D", 5: "A/D", 6: "B/D", 7: "C/D", 8: "none"}
CODES = {v: k for k, v in GROUPS.items()}
MUKEY_NODATA = 2147483647
NLCD_OFFSET = 15.0          # NLCD's 30 m pixel corners lie 15 m past multiples of 30 m
EDITION = "gNATSGO July 2020 (USDA NRCS), via Microsoft Planetary Computer"
_token = {"value": None, "until": 0.0}


def read_token(get=read_bytes):
    """A read token for the soil storage, reused until shortly before it expires."""
    if _token["value"] is None or time.time() > _token["until"]:
        answer = json.loads(get(TOKEN))
        _token["value"] = answer["token"]
        _token["until"] = time.time() + 30 * 60          # tokens last about an hour
    return _token["value"]


def group_table(cache_dir, get=read_bytes):
    """(sorted map-unit keys, their group codes) from the gNATSGO map-unit table.

    The table (12.5 MB) is downloaded once into `cache_dir`.
    """
    import pyarrow.parquet as pq

    from pathlib import Path

    local = Path(cache_dir) / "gnatsgo_muaggatt.parquet"
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(local.name + ".part")
        part.write_bytes(get(f"{TABLE}?{read_token(get)}"))
        part.replace(local)
    table = pq.read_table(local, columns=["mukey", "hydgrpdcd"]).to_pandas()
    table = table.drop_duplicates("mukey").sort_values("mukey")
    codes = table["hydgrpdcd"].astype(object).map(CODES).fillna(CODES["none"]).astype(np.uint8)
    return table["mukey"].to_numpy(dtype=np.int64), codes.to_numpy()


def tiles(bbox, get=read_bytes):
    """URLs of the gNATSGO map-unit tiles intersecting a lon/lat box."""
    params = {"collections": COLLECTION, "bbox": ",".join(str(v) for v in check_bbox(bbox)),
              "limit": 100}
    answer = json.loads(get(f"{STAC}?{urllib.parse.urlencode(params)}"))
    return [f["assets"]["mukey"]["href"] for f in answer.get("features", [])]


def to_groups(mukeys, keys, codes):
    """Group codes of map units (0 where the key is no data or unknown)."""
    mukeys = np.asarray(mukeys, dtype=np.int64)
    at = np.minimum(np.searchsorted(keys, mukeys), len(keys) - 1)
    found = (keys[at] == mukeys) & (mukeys != MUKEY_NODATA)
    return np.where(found, codes[at], 0).astype(np.uint8)


def mukey_grid(bbox, *, like=None, resolution=30.0, get=read_bytes, opener=None):
    """(map-unit keys, transform, crs) of the gNATSGO grid for a box, on the output grid of
    `fetch_soil_groups` (see there); MUKEY_NODATA outside the survey."""
    import rasterio
    from affine import Affine
    from rasterio.errors import WindowError
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    from ._common import gdal_env

    if like is not None:
        with rasterio.open(like) as ref:
            crs, transform, (height, width) = ref.crs, ref.transform, ref.shape
            resolution = abs(transform.a)
            bounds = ref.bounds
        bbox = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    else:
        bbox = check_bbox(bbox)
        crs = "EPSG:5070"
        left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
        offset = NLCD_OFFSET if resolution == 30.0 else 0.0
        left = math.floor((left - offset) / resolution) * resolution + offset
        top = math.ceil((top - offset) / resolution) * resolution + offset
        width = int(math.ceil((right - left) / resolution))
        height = int(math.ceil((top - bottom) / resolution))
        transform = from_origin(left, top, resolution, resolution)
    out_bounds = rasterio.transform.array_bounds(height, width, transform)
    mukey = np.full((height, width), MUKEY_NODATA, dtype=np.int64)
    found = tiles(bbox, get)
    if not found:
        raise ValueError("no gNATSGO soil tiles cover this box (it covers the lower 48 US states)")
    with gdal_env():
        for url in found:
            path = opener(url) if opener else f"/vsicurl/{url}?{read_token(get)}"
            with rasterio.open(path) as src:
                # the tile's part of the output, in whole pixels of its full 10 m grid. Reading
                # an overview, or a window cut at fractional pixels, resamples the grid at an
                # offset that depends on the box asked for, so the same place could get another
                # map unit near unit boundaries (3% of pixels in a test region)
                b = transform_bounds(crs, src.crs, out_bounds[0], out_bounds[1], out_bounds[2],
                                     out_bounds[3], densify_pts=21)
                try:
                    win = from_bounds(*b, src.transform)
                except WindowError:
                    continue
                col0 = max(0, math.floor(win.col_off) - 1)
                row0 = max(0, math.floor(win.row_off) - 1)
                col1 = min(src.width, math.ceil(win.col_off + win.width) + 1)
                row1 = min(src.height, math.ceil(win.row_off + win.height) + 1)
                if col1 <= col0 or row1 <= row0:              # the tile only touches the box
                    continue
                win = Window(col0, row0, col1 - col0, row1 - row0)
                data = src.read(1, window=win).astype(np.int32)
                part = np.full((height, width), MUKEY_NODATA, dtype=np.int32)
                # each output pixel takes the map unit at its centre; a centre exactly on a soil
                # pixel's edge (NLCD's 30 m centres fall on the 10 m grid's lines) takes the
                # pixel to its east or south, whatever the box: the centres are moved by a
                # millionth of a pixel so that rounding cannot decide
                reproject(data, part, src_transform=src.window_transform(win), src_crs=src.crs,
                          src_nodata=MUKEY_NODATA, dst_transform=transform @ Affine.translation(1e-6, 1e-6),
                          dst_crs=crs, dst_nodata=MUKEY_NODATA, resampling=Resampling.nearest)
            take = (mukey == MUKEY_NODATA) & (part != MUKEY_NODATA)
            mukey[take] = part[take]
    return mukey, transform, crs


def fetch_soil_groups(bbox, out_path, *, cache_dir, like=None, resolution=30.0, get=read_bytes,
                      opener=None):
    """Write soil hydrologic group codes (GROUPS) for a lon/lat box to a GeoTIFF.

    By default the grid is NAD83 / Conus Albers (EPSG:5070) at `resolution` metres covering the
    box, with pixel edges where NLCD's are (15 m past multiples of 30 m, for 30 m). To combine
    the groups with an NLCD raster pixel for pixel, pass it as `like`: its grid (extent, size,
    CRS) is then used exactly, and `bbox` may be None (the NLCD service chooses its own edge
    pixels, so the same box does not give the same extent).
    Each pixel takes the map unit at its centre in the full 10 m grid (a centre on a grid line
    takes the soil pixel to its east or south), so a place gets the same group whatever box it is
    read in. `opener(url)` opens a tile (for tests). Returns the path.
    """
    keys, codes = group_table(cache_dir, get)
    mukey, transform, crs = mukey_grid(bbox, like=like, resolution=resolution, get=get, opener=opener)
    groups = to_groups(mukey, keys, codes)
    if not groups.any():
        raise ValueError("the soil survey holds no data for this box")
    return write_raster(out_path, groups, transform, crs, 0, "uint8")


# ------------------------------------------- how fast water soaks in (Green-Ampt parameters)

SDA = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
SDA_EDITION = "SSURGO via USDA Soil Data Access (current edition when read)"
KSAT_DEPTH_CM = 50          # NRCS judges a soil's group by its least conductive layer in 0-50 cm
FALLBACK_KSAT_MM_H = 3.6    # 1 micrometre per second: the most a group D soil conducts (NEH 630 ch. 7)
INFILTRATION_COLUMNS = ["mukey", "ksat_mm_h", "suction_mm", "theta_sat", "theta_fc", "theta_wp",
                        "texture", "component", "comppct"]
_SQL = """SELECT c.mukey, c.cokey, c.compname, c.comppct_r, h.hzdept_r, h.hzdepb_r, h.ksat_r,
h.wsatiated_r, h.wthirdbar_r, h.wfifteenbar_r, t.texcl
FROM component c
JOIN chorizon h ON h.cokey = c.cokey
LEFT JOIN chtexturegrp g ON g.chkey = h.chkey AND g.rvindicator = 'Yes'
LEFT JOIN chtexture t ON t.chtgkey = g.chtgkey
WHERE c.mukey IN ({keys})"""


def _post(data):
    import urllib.request

    req = urllib.request.Request(SDA, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def sda_query(sql, post=_post):
    """Rows (list of dicts) of an SQL query to USDA Soil Data Access (public, no account)."""
    from ._common import with_retries

    body = json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode()
    answer = json.loads(with_retries(lambda: post(body)) or b"{}")
    table = answer.get("Table", [])
    if not table:
        return []
    return [dict(zip(table[0], row)) for row in table[1:]]


def infiltration_parameters(horizons):
    """Green-Ampt parameters of each map unit from its components' horizons (rows of `_SQL`).

    For each map unit, the component with the largest share that has a usable surface horizon
    (a mineral texture class, and water contents at saturation, 1/3 bar and 15 bar) and a
    conductivity for every horizon starting above 50 cm gives:
      ksat_mm_h   the least saturated conductivity of those horizons (as NRCS judges soil groups)
      suction_mm  the wetting-front suction of the surface horizon's texture (runoff.SUCTION_MM)
      theta_sat, theta_fc, theta_wp   surface water content at saturation, 1/3 bar and 15 bar
    Map units without such a component are left out.
    """
    import pandas as pd

    from ..runoff import SUCTION_MM, texture_class

    frame = pd.DataFrame(horizons)
    if frame.empty:
        return pd.DataFrame({c: pd.Series(dtype=object if c in ("texture", "component") else float)
                             for c in INFILTRATION_COLUMNS}).astype({"mukey": "int64"})
    for c in ("comppct_r", "hzdept_r", "hzdepb_r", "ksat_r", "wsatiated_r", "wthirdbar_r", "wfifteenbar_r"):
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame["mukey"] = frame["mukey"].astype(np.int64)
    rows = []
    for mukey, unit in frame.groupby("mukey", sort=True):
        comps = unit.groupby("cokey").agg(pct=("comppct_r", "first"), name=("compname", "first"))
        for cokey in comps.sort_values("pct", ascending=False, kind="stable").index:
            hz = unit[unit["cokey"] == cokey].drop_duplicates(["hzdept_r", "hzdepb_r"])
            top = hz[hz["hzdept_r"] == 0]
            upper = hz[hz["hzdept_r"] < KSAT_DEPTH_CM]["ksat_r"]
            if top.empty or upper.empty or upper.isna().any():
                continue
            top = top.iloc[0]
            texture = texture_class(top["texcl"])
            water = top[["wsatiated_r", "wthirdbar_r", "wfifteenbar_r"]].astype(float)
            if texture is None or water.isna().any():
                continue
            rows.append((mukey, float(upper.min()) * 3.6, SUCTION_MM[texture], water.iloc[0] / 100,
                         water.iloc[1] / 100, water.iloc[2] / 100, texture, comps.loc[cokey, "name"],
                         float(comps.loc[cokey, "pct"])))
            break
    return pd.DataFrame(rows, columns=INFILTRATION_COLUMNS)


def infiltration_table(mukeys, cache_dir, *, batch=500, post=_post):
    """infiltration_parameters of the map units `mukeys`, read from Soil Data Access in batches
    and kept in `cache_dir` (sda_infiltration.parquet, and the keys already asked for in
    sda_infiltration_asked.npy), so each map unit is asked for once."""
    from pathlib import Path

    import pandas as pd

    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    known_path, asked_path = folder / "sda_infiltration.parquet", folder / "sda_infiltration_asked.npy"
    known = pd.read_parquet(known_path) if known_path.exists() else infiltration_parameters([])
    asked = np.load(asked_path) if asked_path.exists() else np.zeros(0, dtype=np.int64)
    wanted = np.unique(np.asarray(mukeys, dtype=np.int64))
    wanted = wanted[(wanted != MUKEY_NODATA) & ~np.isin(wanted, asked)]
    for i in range(0, len(wanted), batch):
        part = wanted[i:i + batch]
        sql = _SQL.format(keys=",".join(str(int(k)) for k in part))
        got = infiltration_parameters(sda_query(sql, post))
        known = pd.concat([known, got], ignore_index=True) if len(known) else got
        asked = np.concatenate([asked, part])
        known.to_parquet(known_path.with_suffix(".part"))
        known_path.with_suffix(".part").replace(known_path)
        np.save(asked_path, asked)
    return known.drop_duplicates("mukey").sort_values("mukey").reset_index(drop=True)


def fetch_soil_infiltration(bbox, out_stem, *, cache_dir, like=None, resolution=30.0, get=read_bytes,
                            opener=None, post=_post):
    """Write rasters of Green-Ampt soil parameters for a box, on the grid of `fetch_soil_groups`
    (same `bbox`, `like`, `resolution`). Map units come from the gNATSGO grid (July 2020), their
    horizons from Soil Data Access (`infiltration_table`). Files, named after `out_stem`:

      <stem>_ksat.tif              saturated conductivity, mm/h
      <stem>_suction.tif           wetting-front suction, mm
      <stem>_deficit_average.tif   water the surface soil can still take in, from field capacity
                                   (1/3 bar) to saturation, as a share of its volume
      <stem>_deficit_dry.tif       the same from the wilting point (15 bar)
      <stem>_fallback.tif          1 where the survey had no usable data for the map unit, 0 where
                                   it had, 255 outside the survey

    Where a map unit has no usable data (urban land, water, pits, rock; units renamed since 2020),
    the fallback is used: conductivity 3.6 mm/h (1 um/s, the most a group D soil conducts), no
    water deficit (as if already saturated) and suction 0, so the ground takes water only at that
    slow rate. Pixels outside the survey are NaN. Returns a dict of the paths.
    """
    from pathlib import Path

    mukey, transform, crs = mukey_grid(bbox, like=like, resolution=resolution, get=get, opener=opener)
    surveyed = mukey != MUKEY_NODATA
    if not surveyed.any():
        raise ValueError("the soil survey holds no data for this box")
    table = infiltration_table(np.unique(mukey[surveyed]), cache_dir, post=post)
    keys = table["mukey"].to_numpy(dtype=np.int64)
    at = np.minimum(np.searchsorted(keys, mukey), max(len(keys) - 1, 0))
    found = surveyed & (keys[at] == mukey) if len(keys) else np.zeros(mukey.shape, bool)

    def layer(values, fallback):
        out = np.full(mukey.shape, np.nan, dtype=np.float32)
        out[surveyed] = fallback
        if len(keys):
            out[found] = np.asarray(values, dtype=np.float32)[at[found]]
        return out
    sat, fc, wp = (table[c].to_numpy(float) for c in ("theta_sat", "theta_fc", "theta_wp"))
    layers = {"ksat": layer(table["ksat_mm_h"], FALLBACK_KSAT_MM_H),
              "suction": layer(table["suction_mm"], 0.0),
              "deficit_average": layer(np.maximum(sat - fc, 0.0), 0.0),
              "deficit_dry": layer(np.maximum(sat - wp, 0.0), 0.0)}
    stem = Path(out_stem)
    paths = {name: write_raster(f"{stem}_{name}.tif", values, transform, crs, float("nan"), "float32")
             for name, values in layers.items()}
    flag = np.where(surveyed, (~found).astype(np.uint8), 255).astype(np.uint8)
    paths["fallback"] = write_raster(f"{stem}_fallback.tif", flag, transform, crs, 255, "uint8")
    return paths
