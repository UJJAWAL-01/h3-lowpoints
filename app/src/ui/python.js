// "Do this in Python": the h3-lowpoints code that recomputes what a panel shows, from public data,
// for a box around it. app/tools/python_check.py runs these exact snippets with the library and
// compares their numbers with the map's (see docs/explorer.md).

export const LIBRARY = "0.5.1";
export const MARGIN = 0.05;          // degrees around the place: room for the land draining into it
export const MAX_VIEW = 0.25;        // degrees: larger views take too long on a laptop

const r6 = (x) => Number(x.toFixed(6));
const boxText = (b) => `(${b.map(r6).join(", ")})`;

function setup(boxLine, folder) {
  return `# pip install "h3-lowpoints[sources,parquet]==${LIBRARY}"
# Downloads public data for the box once (USGS 3DEP elevation, ESA WorldCover, Annual NLCD 2024,
# USDA gNATSGO soils, NOAA Atlas 14) into ./lowpoints_cache; a few minutes on a laptop.
from pathlib import Path

import h3
from h3lowpoints import analyze_tiled, susceptibility
from h3lowpoints.runoff import fetch_curve_numbers
from h3lowpoints.sources.landcover import build_water_mask
from h3lowpoints.sources.usgs import build_3dep_vrt

${boxLine}
out, cache = Path("${folder}"), Path("lowpoints_cache")

dem = build_3dep_vrt(box, out / "dem.vrt", cache_dir=cache)          # 30 m bare earth
water = build_water_mask(dem, out / "water.vrt", cache_dir=cache)    # open water leaves, not fills
result = analyze_tiled(str(dem), mask_path=str(water), min_depth=0.5, catchments=True,
                       details=True, out_dir=out / "result", format="parquet")
cn = fetch_curve_numbers(box, out / "cn.tif", cache_dir=cache)       # SCS curve numbers
index = susceptibility.susceptibility(result, curve_numbers=str(cn), rain="atlas14", cache_dir=cache)
table = result.load().depressions.merge(index, on="id")
`;
}

// One depression, found by the H3 cell of its lowest point (a cell can hold more than one)
export function depressionSnippet(d) {
  const lat = d.lat, lon = d.lon;
  const box = [lon - MARGIN, lat - MARGIN, lon + MARGIN, lat + MARGIN];
  return `# Recompute this depression with h3-lowpoints ${LIBRARY}, the library behind this map.
${setup(`CELL = "${d.low}"                     # the H3 cell of its lowest point
box = ${boxText(box)}  # west, south, east, north: about 10 km around it`, `lowpoints_${d.low}`)}
here = table[table.h3_lowpoint == CELL]      # a cell can hold more than one: the map's has volume ${Math.round(d.volume ?? NaN)} m3
print(here[["max_depth_m", "area_m2", "volume_m3", "catchment_area_m2", "curve_number",
            "rain_to_fill_mm", "fills_in_years"]].T)
`;
}

// The storm in the view: how many depressions whose lowest point is in the view fill and overflow
export function stormSnippet(view, storm) {
  const which = storm.kind === "rarity"
    ? `years=${storm.years}` : `depth_mm=${r6(storm.mm)}`;
  return `# Recompute this storm with h3-lowpoints ${LIBRARY}, the library behind this map.
${setup(`view = ${boxText(view)}  # west, south, east, north of the map
box = (view[0] - ${MARGIN}, view[1] - ${MARGIN}, view[2] + ${MARGIN}, view[3] + ${MARGIN})  # with room for water from upstream`, `lowpoints_${view.map((x) => x.toFixed(4)).join("_")}`)}
from h3lowpoints.storms import storm_response

# The water at the end of the storm does not depend on how the rain is spread in time.
storm = storm_response(result, hours=${r6(storm.hours)}, ${which}, curve_numbers=str(cn),
                       condition="${storm.condition}", pattern="uniform", cache_dir=cache)
t = table.merge(storm[["id", "peak_fill", "overflow_m3", "water_left_m3"]], on="id")
lat, lon = zip(*[h3.cell_to_latlng(c) for c in t.h3_lowpoint])
t["lat_cell"], t["lon_cell"] = lat, lon
in_view = t[t.lon_cell.between(view[0], view[2]) & t.lat_cell.between(view[1], view[3])]
print(len(in_view), "depressions;",
      int((in_view.peak_fill >= 1 - 1e-9).sum()), "full;",
      int((in_view.overflow_m3 > 0).sum()), "overflowing;",
      round(in_view.water_left_m3.sum()), "m3 of water held")
`;
}

export function viewTooLarge(view) {
  return view[2] - view[0] > MAX_VIEW || view[3] - view[1] > MAX_VIEW;
}

// What the dialog says above the code
export function sayHtml(kind, show) {
  const what = kind === "depression" ? "This code recomputes this depression" : "This code reruns this storm";
  const filter = kind === "storm" && show === "undrained"
    ? "<p class=\"note\">It counts every depression; the map's counts leave out the likely drained ones you have hidden.</p>" : "";
  const count = kind === "storm" ? " and counts the depressions whose lowest point is in the view" : "";
  return `<p>${what} from public data with h3-lowpoints ${LIBRARY}, the open-source library behind this map${count}. It runs on your computer: install Python 3.10 or later, then the library as on the first line.</p>
    ${filter}
    <p class="note">${AGREEMENT[kind]} <a href="https://ujjawal-01.github.io/h3-lowpoints/limitations.html" target="_blank" rel="noopener">Limits</a></p>`;
}

// From app/tools/python_check.py (2 November 2026, library 0.5.1, dataset 0.3)
export const AGREEMENT = {
  depression: "Checked: run on 12 depressions in Houston, New York City and North Dakota, this code gave the map's depth, area, volume, land draining in, curve number, rain from its own land and the storm that fills it, each within 1 part in 10 million. Elsewhere it can differ slightly next to open water, or when the land draining in reaches beyond the box.",
  storm: "Checked: run on 6 storms in Houston, New York City and North Dakota, it gave the map's numbers of depressions, full and overflowing. For storms by amount the water held also matched (within 1 part in a million); for storms by rarity it was within 0.2%, because the map takes each depression's storm depth at its lowest point and the library averages it over the land draining in.",
};
