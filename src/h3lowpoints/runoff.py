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
