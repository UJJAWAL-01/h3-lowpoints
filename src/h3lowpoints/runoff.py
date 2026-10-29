"""Runoff from rain: SCS curve numbers (NRCS TR-55) from land cover, imperviousness and soil.

    from h3lowpoints.runoff import curve_numbers, runoff_mm, rain_for_runoff_mm
    cn = curve_numbers(land_cover, impervious, groups)     # arrays on one grid
    runoff_mm(100.0, 80.0)                                  # 100 mm of rain on CN 80: 50.5 mm runs off

The curve number (CN, 0 to 100) says how much of a storm runs off: 98 for pavement and roofs,
about 30 to 90 for vegetated ground depending on cover and soil. For a storm of P mm, NRCS's
runoff equation (TR-55, 1986) gives the runoff Q:

    S = 25400 / CN - 254          the most the ground can still hold, mm
    Ia = 0.2 S                    what is held before any water runs off
    Q = (P - Ia)^2 / (P - Ia + S) when P > Ia, else 0

and its exact inverse gives the rain that makes a given runoff. Average conditions of soil
moisture (antecedent runoff condition II) are assumed.

`curve_numbers` gives each 30 m pixel the composite CN of TR-55: its paved share at 98, the rest
at the CN of its land cover (NLCD class) on its soil's hydrologic group (CN_TABLE). Dual groups
(A/D, B/D, C/D) and map units without a group are taken as D: in a depression a wet soil is
usually not drained, and the ungrouped units are mostly urban land, pits and rock. Wetlands take
group D whatever the map says (their soils are saturated). Open water and ice are 98.
"""
import numpy as np

IMPERVIOUS_CN = 98.0
# NLCD class -> (CN on group A, B, C, D) of its pervious ground, and the TR-55 row used
CN_TABLE = {
    11: ((98, 98, 98, 98), "open water: as impervious"),
    12: ((98, 98, 98, 98), "perennial ice and snow: as impervious"),
    21: ((39, 61, 74, 80), "open space, good condition (grass cover over 75%)"),
    22: ((39, 61, 74, 80), "open space, good condition, for the unpaved part"),
    23: ((39, 61, 74, 80), "open space, good condition, for the unpaved part"),
    24: ((39, 61, 74, 80), "open space, good condition, for the unpaved part"),
    31: ((77, 86, 91, 94), "newly graded areas (pervious, no vegetation)"),
    41: ((36, 60, 73, 79), "woods, fair condition"),
    42: ((36, 60, 73, 79), "woods, fair condition"),
    43: ((36, 60, 73, 79), "woods, fair condition"),
    51: ((35, 56, 70, 77), "brush-weed-grass mixture, fair condition"),
    52: ((35, 56, 70, 77), "brush-weed-grass mixture, fair condition"),
    71: ((49, 69, 79, 84), "pasture, grassland or range, fair condition"),
    72: ((49, 69, 79, 84), "pasture, grassland or range, fair condition"),
    73: ((49, 69, 79, 84), "pasture, grassland or range, fair condition"),
    74: ((49, 69, 79, 84), "pasture, grassland or range, fair condition"),
    81: ((49, 69, 79, 84), "pasture, grassland or range, fair condition"),
    82: ((67, 78, 85, 89), "row crops, straight row, good condition"),
    90: ((79, 79, 79, 79), "woody wetlands: woods, fair condition, on group D"),
    95: ((84, 84, 84, 84), "emergent herbaceous wetlands: pasture, fair condition, on group D"),
}
# soil group code (sources.soils.GROUPS) -> column of CN_TABLE: dual groups, none and no data as D
GROUP_COLUMN = np.array([3, 0, 1, 2, 3, 3, 3, 3, 3])
RULE = ("SCS curve number (NRCS TR-55): composite CN = 98 x paved share + CN of the NLCD land cover "
        "on the soil's hydrologic group (dual groups and ungrouped soil as D, wetlands as D) x the "
        "rest; runoff Q = (P - 0.2 S)^2 / (P + 0.8 S), S = 25400 / CN - 254 mm; average soil "
        "moisture")


def curve_numbers(land_cover, impervious, groups):
    """Composite curve number of each pixel (float; NaN where the land cover is unknown).

    land_cover  NLCD classes (CN_TABLE); others, and 0, are unknown
    impervious  percent paved, 0 to 100 (values above 100, such as NLCD's no data 255, count as 0)
    groups      soil hydrologic group codes of `sources.soils` (1 A ... 7 C/D, 8 none, 0 no data)
    """
    lc = np.asarray(land_cover)
    imp = np.asarray(impervious, dtype=float)
    imp = np.where((imp >= 0) & (imp <= 100), imp, 0.0) / 100.0
    col = GROUP_COLUMN[np.clip(np.asarray(groups, dtype=np.int64), 0, len(GROUP_COLUMN) - 1)]
    table = np.full((256, 4), np.nan)
    for cls, (values, _) in CN_TABLE.items():
        table[cls] = values
    pervious = table[np.clip(lc.astype(np.int64), 0, 255), col]
    return imp * IMPERVIOUS_CN + (1.0 - imp) * pervious


def retention_mm(cn):
    """S: the most the ground can still hold, in mm, for curve number `cn` (0 < cn <= 100)."""
    cn = np.asarray(cn, dtype=float)
    return 25400.0 / cn - 254.0


def runoff_mm(rain_mm, cn):
    """Runoff Q in mm from a storm of `rain_mm` on ground of curve number `cn`."""
    p = np.asarray(rain_mm, dtype=float)
    s = retention_mm(cn)
    excess = np.maximum(p - 0.2 * s, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(excess > 0, excess ** 2 / (excess + s), 0.0)


def rain_for_runoff_mm(runoff, cn):
    """The storm, in mm, whose runoff on curve number `cn` is `runoff` mm (inverse of runoff_mm)."""
    q = np.asarray(runoff, dtype=float)
    s = retention_mm(cn)
    return 0.2 * s + (q + np.sqrt(q * q + 4.0 * q * s)) / 2.0


# NRCS National Engineering Handbook Part 630, chapter 10 (July 2004), table 10-1: curve number
# for average conditions (ARC II) -> ARC I (dry) and ARC III (wet), "CN:I:III"
_TABLE_10_1 = (
    "100:100:100 99:97:100 98:94:99 97:91:99 96:89:99 95:87:98 94:85:98 93:83:98 92:81:97 91:80:97 "
    "90:78:96 89:76:96 88:75:95 87:73:95 86:72:94 85:70:94 84:68:93 83:67:93 82:66:92 81:64:92 "
    "80:63:91 79:62:91 78:60:90 77:59:89 76:58:89 75:57:88 74:55:88 73:54:87 72:53:86 71:52:86 "
    "70:51:85 69:50:84 68:48:84 67:47:83 66:46:82 65:45:82 64:44:81 63:43:80 62:42:79 61:41:78 "
    "60:40:78 59:39:77 58:38:76 57:37:75 56:36:75 55:35:74 54:34:73 53:33:72 52:32:71 51:31:70 "
    "50:31:70 49:30:69 48:29:68 47:28:67 46:27:66 45:26:65 44:25:64 43:25:63 42:24:62 41:23:61 "
    "40:22:60 39:21:59 38:21:58 37:20:57 36:19:56 35:18:55 34:18:54 33:17:53 32:16:52 31:16:51 "
    "30:15:50 25:12:43 20:9:37 15:6:30 10:4:22 5:2:13 0:0:0")
ARC_TABLE = np.array(sorted(tuple(int(v) for v in row.split(":")) for row in _TABLE_10_1.split()),
                     dtype=float)
CONDITIONS = {"dry": "ARC I", "average": "ARC II", "wet": "ARC III"}


def adjust_curve_number(cn, condition="average"):
    """Curve number for dry ("dry", ARC I), average or wet ("wet", ARC III) conditions before a
    storm, from the average one by NRCS's table 10-1 (NEH 630 ch. 10), linear between its rows.

    NRCS describes ARC I and III as the low and high ends of the spread of runoff seen for the
    same ground (about the 10% and 90% curve numbers), which past practice tied to rain in the
    5 days before; they are not exact soil-moisture states.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {list(CONDITIONS)}, got {condition!r}")
    cn = np.asarray(cn, dtype=float)
    if condition == "average":
        return cn
    column = 1 if condition == "dry" else 2
    out = np.interp(cn, ARC_TABLE[:, 0], ARC_TABLE[:, column])
    return np.where(np.isnan(cn), np.nan, out)


# Green-Ampt wetting-front suction head, mm, by USDA texture class: Rawls, Brakensiek and Miller
# (1983), J. Hydraul. Eng. 109(1), table 2, rows for the whole class. Silt has no row there (too
# few samples): it takes silt loam's value, an assumption
SUCTION_MM = {"sand": 49.5, "loamy sand": 61.3, "sandy loam": 110.1, "loam": 88.9,
              "silt loam": 166.8, "silt": 166.8, "sandy clay loam": 218.5, "clay loam": 208.8,
              "silty clay loam": 273.0, "sandy clay": 239.0, "silty clay": 292.2, "clay": 316.3}


def texture_class(name):
    """The USDA texture class of an NRCS texture name ("Fine sandy loam" -> "sandy loam"), or
    None for names that are not mineral textures (muck, peat, bedrock, missing)."""
    if not isinstance(name, str):
        return None
    words = name.lower().replace("very fine", "").replace("coarse", "").replace("fine", "").split()
    base = " ".join(words)
    return base if base in SUCTION_MM else None


def fetch_curve_numbers(bbox, out_path, *, cache_dir, year=None, source="annual"):
    """Write composite curve numbers (float32, NaN for no data) for a lon/lat box in the lower 48
    US states: NLCD land cover and imperviousness of `year` (Annual NLCD 2024 by default; `source`
    and `year` as in sources.landcover.fetch_land_cover), and gNATSGO soil groups, on NLCD's
    30 m grid. The three inputs are kept beside `out_path` (`*_land_cover.tif`,
    `*_impervious.tif`, `*_soil_groups.tif`). Returns the path."""
    from pathlib import Path

    import rasterio

    from .sources._common import write_raster
    from .sources.landcover import fetch_impervious, fetch_land_cover
    from .sources.soils import fetch_soil_groups

    out = Path(out_path)
    stem = out.with_suffix("")
    lc = fetch_land_cover(bbox, f"{stem}_land_cover.tif", year=year, source=source)
    imp = fetch_impervious(bbox, f"{stem}_impervious.tif", year=year, source=source)
    soil = fetch_soil_groups(None, f"{stem}_soil_groups.tif", cache_dir=cache_dir, like=lc)
    with rasterio.open(lc) as a, rasterio.open(imp) as b, rasterio.open(soil) as c:
        if not (a.shape == b.shape == c.shape and a.transform == b.transform == c.transform):
            raise ValueError("NLCD land cover, imperviousness and soil groups are not on one grid")
        cn = curve_numbers(a.read(1), b.read(1), c.read(1)).astype("float32")
        transform, crs = a.transform, a.crs
    return write_raster(out, cn, transform, crs, float("nan"), "float32")
