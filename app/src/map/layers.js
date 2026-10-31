// Hexagon layers and their legends. One ink ramp for both scales; blue is kept for water.

import { OVERLAY_BEFORE, LABELS_FROM } from "./style.js";

export const INK = ["#e9ebe7", "#d1d5d1", "#b3b9b5", "#8f9692", "#69706c", "#454b48", "#202422"];

// Zoomed out: share of the hexagon that lies in depressions (national median about 1%)
export const SHARE = { breaks: [0.0025, 0.005, 0.01, 0.025, 0.05, 0.1],
  labels: ["0", "0.25", "0.5", "1", "2.5", "5", "10%+"] };
// Zoomed in: how deep water stands at the cell's deepest point when its depression is full (m)
export const DEPTH = { breaks: [0.25, 0.5, 1, 2, 4, 8], labelsM: ["0", "0.25", "0.5", "1", "2", "4", "8+"],
  labelsFt: ["0", "0.8", "1.6", "3.3", "6.6", "13", "26+"] };

const step = (prop, b) => ["step", ["get", prop], INK[0], b[0], INK[1], b[1], INK[2], b[2], INK[3], b[3], INK[4], b[4], INK[5], b[5], INK[6]];

export function addBaseLayers(map) {
  map.addSource("overview", { type: "geojson", data: empty(), tolerance: 0.2 });
  map.addLayer({ id: "overview-fill", type: "fill", source: "overview", paint: {
    "fill-color": step("s", SHARE.breaks),
    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.92, 8, 0.8, 10, 0.7],
    "fill-opacity-transition": { duration: 300 } } }, OVERLAY_BEFORE);
  map.addLayer({ id: "overview-line", type: "line", source: "overview", paint: {
    "line-color": "#f2f3f1", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.3, 7, 0.8, 10, 1.2],
    "line-opacity": 0.9 } }, OVERLAY_BEFORE);
  map.addLayer({ id: "overview-hover", type: "line", source: "overview", paint: {
    "line-color": "#121513", "line-width": 1.6,
    "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0] } }, LABELS_FROM);

  for (const [id, props] of [["selection", {}], ["spill", {}], ["upstream", {}], ["cellsel", {}]]) {
    map.addSource(id, { type: "geojson", data: empty(), ...props });
  }
  map.addLayer({ id: "selection-fill", type: "fill", source: "selection", paint: {
    "fill-color": "#121513", "fill-opacity": 0.12 } }, LABELS_FROM);
  map.addLayer({ id: "selection-line", type: "line", source: "selection", paint: {
    "line-color": "#121513", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1, 15, 2.2] } }, LABELS_FROM);
  map.addLayer({ id: "upstream-line", type: "line", source: "upstream", layout: { "line-cap": "round" }, paint: {
    "line-color": "#5b635e", "line-width": 1.2, "line-dasharray": [1, 2.2] } }, LABELS_FROM);
  map.addLayer({ id: "spill-line", type: "line", source: "spill", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#1d3f9e", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.6, 15, 3],
      "line-dasharray": [2, 1.4] } }, LABELS_FROM);
  map.addLayer({ id: "spill-points", type: "circle", source: "spill", filter: ["==", ["geometry-type"], "Point"], paint: {
    "circle-radius": 3.5, "circle-color": "#ffffff", "circle-stroke-color": "#1d3f9e", "circle-stroke-width": 2 } },
    LABELS_FROM);
  map.addLayer({ id: "cellsel-line", type: "line", source: "cellsel", paint: {
    "line-color": "#121513", "line-width": 2.2 } }, LABELS_FROM);
}

export function addPackLayers(map, region) {
  const src = `pk-${region}`;
  if (map.getSource(src)) return;
  map.addSource(src, { type: "geojson", data: empty(), tolerance: 0.1, buffer: 32 });
  map.addLayer({ id: `${src}-fill`, type: "fill", source: src, paint: {
    "fill-color": ["case", ["has", "s"], step("s", SHARE.breaks), step("d", DEPTH.breaks)],
    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 10, 0.72, 15, 0.62, 18, 0.5],
    "fill-opacity-transition": { duration: 250 } } }, OVERLAY_BEFORE);
  map.addLayer({ id: `${src}-line`, type: "line", source: src, minzoom: 11.5, paint: {
    "line-color": "#f2f3f1", "line-width": ["interpolate", ["linear"], ["zoom"], 11.5, 0.3, 14, 0.8, 17, 1.2],
    "line-opacity": 0.85 } }, OVERLAY_BEFORE);
  map.addLayer({ id: `${src}-hover`, type: "line", source: src, paint: {
    "line-color": "#121513", "line-width": 1.6,
    "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0] } }, LABELS_FROM);
}

export function removePackLayers(map, region) {
  const src = `pk-${region}`;
  for (const id of [`${src}-hover`, `${src}-line`, `${src}-fill`]) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(src)) map.removeSource(src);
}

export function setVisible(map, ids, on) {
  for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

export function empty() {
  return { type: "FeatureCollection", features: [] };
}

export function legendHtml(kind, us) {
  const L = kind === "share" ? SHARE.labels : us ? DEPTH.labelsFt : DEPTH.labelsM;
  const title = kind === "share" ? "Share of each hexagon in depressions (%)" : `Water depth when full (${us ? "ft" : "m"})`;
  return `<div class="legend" aria-label="${title}"><span class="label">${title}</span>
    <div class="ramp">${INK.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
    <div class="ramp-labels">${L.map((l) => `<span>${l}</span>`).join("")}</div></div>`;
}
