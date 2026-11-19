// Hexagon layers and their legends. One ink ramp for both scales; blue is kept for water.

import { DEFAULT_EXAGGERATION, DEPTH_BREAKS, EARTH } from "./heights.js";
import { LABELS_FROM } from "./style.js";

// Hexagons sit above the whole street map (labels stay on top); the selection sits above the
// hexagons. HEX_TOP is an invisible marker layer between the two, so packs loaded later go under it.
export const HEX_TOP = "hex-top";

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
    "fill-color": fillColour(currentColour, "overview"),
    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.9, 8, 0.84, 10, 0.82],
    "fill-opacity-transition": { duration: 300 } } }, LABELS_FROM);
  map.addLayer({ id: "overview-line", type: "line", source: "overview", paint: {
    "line-color": "#f2f3f1", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.3, 7, 0.8, 10, 1.2],
    "line-opacity": 0.9 } }, LABELS_FROM);
  map.addLayer({ id: "overview-hover", type: "line", source: "overview", paint: {
    "line-color": "#121513", "line-width": 1.6,
    "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0] } }, LABELS_FROM);
  map.addLayer(columnLayer("overview-col", "overview", step("s", SHARE.breaks)), LABELS_FROM);
  map.addLayer({ id: HEX_TOP, type: "background", paint: { "background-opacity": 0 } }, LABELS_FROM);

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
  map.addLayer({ id: "spill-arrows", type: "symbol", source: "spill", filter: ["==", ["geometry-type"], "LineString"],
    layout: { "symbol-placement": "line", "symbol-spacing": 90, "text-field": "→", "text-font": ["Noto Sans Medium"],
      "text-size": 26, "text-keep-upright": false, "text-allow-overlap": true, "text-ignore-placement": true,
      "text-rotation-alignment": "map" },
    paint: { "text-color": "#1d3f9e", "text-halo-color": "#ffffff", "text-halo-width": 2 } });
  map.addLayer({ id: "spill-points", type: "circle", source: "spill", filter: ["==", ["geometry-type"], "Point"], paint: {
    "circle-radius": 5, "circle-color": "#ffffff", "circle-stroke-color": "#1d3f9e", "circle-stroke-width": 2.5 } });
  // wide invisible strips so the thin lines are easy to point at
  map.addLayer({ id: "spill-hit", type: "line", source: "spill", filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": "#000", "line-opacity": 0, "line-width": 16 } });
  map.addLayer({ id: "upstream-hit", type: "line", source: "upstream", paint: { "line-color": "#000", "line-opacity": 0, "line-width": 14 } });
  map.addLayer({ id: "cellsel-line", type: "line", source: "cellsel", paint: {
    "line-color": "#121513", "line-width": 2.2 } }, LABELS_FROM);
}

export function addPackLayers(map, region) {
  const src = `pk-${region}`;
  if (map.getSource(src)) return;
  map.addSource(src, { type: "geojson", data: empty(), tolerance: 0.1, buffer: 32 });
  map.addLayer({ id: `${src}-fill`, type: "fill", source: src, paint: {
    "fill-color": fillColour(currentColour, "pack"),
    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 10, 0.84, 15, 0.8, 18, 0.74],
    "fill-opacity-transition": { duration: 250 } } }, HEX_TOP);
  map.addLayer({ id: `${src}-line`, type: "line", source: src, minzoom: 11.5, paint: {
    "line-color": "#f2f3f1", "line-width": ["interpolate", ["linear"], ["zoom"], 11.5, 0.3, 14, 0.8, 17, 1.2],
    "line-opacity": 0.85 } }, HEX_TOP);
  map.addLayer({ id: `${src}-hover`, type: "line", source: src, paint: {
    "line-color": "#121513", "line-width": 1.6,
    "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0] } }, HEX_TOP);
  map.addLayer(waterColumnLayer(`${src}-colw`, src), HEX_TOP);
  map.addLayer(columnLayer(`${src}-col`, src, HEX_COLOUR), HEX_TOP);
}

export function removePackLayers(map, region) {
  const src = `pk-${region}`;
  for (const id of [`${src}-col`, `${src}-colw`, `${src}-hover`, `${src}-line`, `${src}-fill`]) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(src)) map.removeSource(src);
}

// ------------------------------------------------------------------ 3D columns
// Height from the worker (property hc, see heights.js); in a storm the blue water column (height in
// feature-state "w", metres drawn) stands at the foot and the grey column starts on top of it.
// columns: share levels in ink, depth (level 10) in the earth colours of the 3D views
const earth = ["step", ["get", "d"], ...EARTH.flatMap((c, i) => (i < DEPTH_BREAKS.length ? [c, DEPTH_BREAKS[i]] : [c]))];
const HEX_COLOUR = ["case", ["has", "s"], step("s", SHARE.breaks), earth];
let exaggeration = DEFAULT_EXAGGERATION;
const height = () => ["case", ["has", "s"], ["get", "hc"], ["*", ["get", "d"], exaggeration]];
const SELECTED = ["boolean", ["feature-state", "sel"], false];

function columnLayer(id, source, colour) {
  return { id, type: "fill-extrusion", source, layout: { visibility: "none" }, paint: {
    "fill-extrusion-color": ["case", SELECTED, "#121513", colour],
    "fill-extrusion-base": ["coalesce", ["feature-state", "w"], 0],
    "fill-extrusion-height": ["max", source === "overview" ? ["get", "hc"] : height(), ["coalesce", ["feature-state", "w"], 0]],
    "fill-extrusion-opacity": 0.94, "fill-extrusion-vertical-gradient": true } };
}

function waterColumnLayer(id, source) {
  return { id, type: "fill-extrusion", source, layout: { visibility: "none" }, paint: {
    "fill-extrusion-color": "#1d3f9e", "fill-extrusion-base": 0,
    "fill-extrusion-height": ["coalesce", ["feature-state", "w"], 0], "fill-extrusion-opacity": 0.94 } };
}

// Which layers show for a view: flat fills, columns, or (wells) fills kept invisible for pointing
export const FLAT_OPACITY = { overview: ["interpolate", ["linear"], ["zoom"], 3, 0.9, 8, 0.84, 10, 0.82],
  pack: ["interpolate", ["linear"], ["zoom"], 10, 0.84, 15, 0.8, 18, 0.74] };

export function applyView(map, view, sources) {
  for (const src of sources) {
    const kind = src === "overview" ? "overview" : "pack";
    const col = view === "columns";
    setVisible(map, [`${src}-col`], col);
    if (kind === "pack") setVisible(map, [`${src}-colw`], false);   // storm water: the water layer (water.js)
    setVisible(map, [`${src}-line`], !col);                 // in Wells the outline is each opening's rim
    if (map.getLayer(`${src}-fill`)) map.setPaintProperty(`${src}-fill`, "fill-opacity", col || view === "wells" ? 0 : FLAT_OPACITY[kind]);
  }
}

// During a storm the columns turn see-through, so the water rising inside them shows
export function setColumnGlass(map, on, sources) {
  for (const src of sources) if (map.getLayer(`${src}-col`)) map.setPaintProperty(`${src}-col`, "fill-extrusion-opacity", on ? 0.32 : 0.94);
}

// the visitor's depth factor (x1, x5, x10): level-10 columns of every pack source
export function setExaggeration(map, e, sources) {
  exaggeration = e;
  for (const src of sources) {
    if (src === "overview" || !map.getLayer(`${src}-col`)) continue;
    map.setPaintProperty(`${src}-col`, "fill-extrusion-height", ["max", height(), ["coalesce", ["feature-state", "w"], 0]]);
  }
}

export function setVisible(map, ids, on) {
  for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

export function empty() {
  return { type: "FeatureCollection", features: [] };
}

// ------------------------------------------------------------------ evidence views (dataset 0.4)
// Colour by: depth (the default above), seen wet, seen flooded, grade. Blue stays for water; the
// grade uses ink, darkest for A. Zoomed in, each hexagon takes the evidence of the depression with
// most area in it (properties ew, ef, eg); zoomed out, the share of the hexagon's depressions
// (properties ew, ef, eg as shares; -1 where none has evidence).
export const BLUE = ["#eef2fa", "#c9d5f1", "#a0b4e5", "#7491d6", "#4d6ec4", "#2c4fa6", "#163780"];
const NONE = "#f4f4f2";
export const EVIDENCE = {
  wet: { title: "Seen wet by Landsat, 1984-2021",
    classes: [["#e9ebe7", "Too small or too seldom seen to tell", [1, 2]], ["#b3b9b5", "Never seen wet", [3]],
      ["#a0b4e5", "Wet in some years", [4]], ["#4d6ec4", "Wet in at least half the years", [5]], ["#163780", "Permanent water", [6]]],
    share: { breaks: [0.005, 0.01, 0.02, 0.05, 0.1, 0.2], labels: ["0", "0.5", "1", "2", "5", "10", "20%+"],
      title: "Share of depressions seen wet (%)" } },
  flooded: { title: "Seen flooded by radar, 2015-2025",
    classes: [["#e9ebe7", "No radar scene saw it", [0]], ["#8f9692", "Seen, never flooded", [1]], ["#163780", "Flooded in at least one scene", [2]]],
    share: { breaks: [0.0005, 0.001, 0.002, 0.005, 0.01, 0.02], labels: ["0", "0.05", "0.1", "0.2", "0.5", "1", "2%+"],
      title: "Share of depressions seen flooded (%)" } },
  grade: { title: "Confidence grade",
    classes: [["#202422", "A", [1]], ["#69706c", "B", [2]], ["#b3b9b5", "C", [3]], ["#c9a27a", "D (road dam or bridge)", [4]]],
    share: { breaks: [0.2, 0.3, 0.4, 0.5, 0.6, 0.7], labels: ["0", "20", "30", "40", "50", "60", "70%+"],
      title: "Share of depressions graded A or B (%)" } },
};
const EV_PROP = { wet: "ew", flooded: "ef", grade: "eg" };

function classColour(colour) {
  const prop = EV_PROP[colour], e = ["match", ["get", prop]];
  for (const [c, , codes] of EVIDENCE[colour].classes) e.push(codes.length === 1 ? codes[0] : codes, c);
  e.push(NONE);
  return ["case", ["has", prop], e, NONE];
}

function shareColour(colour) {
  const prop = EV_PROP[colour], b = EVIDENCE[colour].share.breaks, ramp = colour === "grade" ? INK : BLUE;
  return ["case", ["all", ["has", prop], [">=", ["get", prop], 0]],
    ["step", ["get", prop], ramp[0], b[0], ramp[1], b[1], ramp[2], b[2], ramp[3], b[3], ramp[4], b[4], ramp[5], b[5], ramp[6]], NONE];
}

// fill colour for a source in a colour mode ("depth" = the original shading)
export function fillColour(colour, kind) {
  if (!EVIDENCE[colour]) return kind === "overview" ? step("s", SHARE.breaks) : ["case", ["has", "s"], step("s", SHARE.breaks), step("d", DEPTH.breaks)];
  if (kind === "overview") return shareColour(colour);
  return ["case", ["has", "s"], classColour(colour), classColour(colour)];
}

let currentColour = "depth";                     // packs added later take the colour mode in force
export function setColour(map, colour, sources) {
  currentColour = colour;
  for (const src of sources) {
    const kind = src === "overview" ? "overview" : "pack";
    if (map.getLayer(`${src}-fill`)) map.setPaintProperty(`${src}-fill`, "fill-color", fillColour(colour, kind));
  }
}

export function evidenceLegendHtml(colour, zoomedOut) {
  const E = EVIDENCE[colour];
  if (zoomedOut) {
    const ramp = colour === "grade" ? INK : BLUE;
    return `<div class="legend" aria-label="${E.share.title}"><span class="label">${E.share.title}</span>
      <div class="ramp">${ramp.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
      <div class="ramp-labels">${E.share.labels.map((l) => `<span>${l}</span>`).join("")}</div></div>`;
  }
  return `<div class="legend classes" aria-label="${E.title}"><span class="label">${E.title}</span><ul>
    ${E.classes.map(([c, l]) => `<li><i style="background:${c}" aria-hidden="true"></i>${l}</li>`).join("")}
    <li><i style="background:${NONE}" aria-hidden="true"></i>No evidence (about 1% of depressions)</li></ul></div>`;
}

export function legendHtml(kind, us, colours = INK) {
  const L = kind === "share" ? SHARE.labels : us ? DEPTH.labelsFt : DEPTH.labelsM;
  const title = kind === "share" ? "Share of each hexagon in depressions (%)" : `Water depth when full (${us ? "ft" : "m"})`;
  return `<div class="legend" aria-label="${title}"><span class="label">${title}</span>
    <div class="ramp">${colours.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
    <div class="ramp-labels">${L.map((l) => `<span>${l}</span>`).join("")}</div></div>`;
}
