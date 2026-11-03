// 311 flood reports of one real storm in New York City and Austin (data/reports_*.json, made by
// app/tools/tour_places.py): small dots over the map, off until asked for. Where people reported
// flooding, not where water stood.

import { LOCAL } from "../config.js";

export const REPORT_CITIES = ["nyc", "austin"];
let loaded = null;

export function loadReports() {
  if (!loaded) {
    loaded = Promise.all(REPORT_CITIES.map(async (c) => {
      const r = await fetch(`${LOCAL.data}reports_${c}.json`);
      if (!r.ok) throw new Error(`reports ${c}: HTTP ${r.status}`);
      return { key: c, ...(await r.json()) };
    })).catch((e) => { loaded = null; throw e; });
  }
  return loaded;
}

export async function showReports(map, on) {
  if (!on) {
    if (map.getLayer("reports")) map.setLayoutProperty("reports", "visibility", "none");
    return null;
  }
  const sets = await loadReports();
  if (!map.getSource("reports")) {
    map.addSource("reports", { type: "geojson", data: { type: "FeatureCollection", features: sets.flatMap((s) =>
      s.points.map(([x, y]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [x, y] }, properties: { c: s.key } }))) } });
  }
  if (!map.getLayer("reports")) {
    map.addLayer({ id: "reports", type: "circle", source: "reports", paint: {
      "circle-color": "#8a2a1c", "circle-opacity": 0.9,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2, 13, 3.5, 16, 6],
      "circle-stroke-color": "#ffffff", "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 0.5, 15, 1.5] } });
  }
  map.setLayoutProperty("reports", "visibility", "visible");
  map.moveLayer("reports");
  return sets;
}

// The key: what the dots are, and the credits each city's terms ask for
export function reportsKey(sets) {
  return sets.map((s) => `${s.city}: ${s.points.length.toLocaleString("en-US")} reports on ${dayText(s.day)}. Source: ${s.credit}, downloaded ${dayText(s.downloaded)}; ${s.changes}.`).join(" ");
}

export function dayText(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}
