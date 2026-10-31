// The basemap: OpenStreetMap vector tiles (Protomaps schema) from our own PMTiles file, in two
// looks: "detailed" (Protomaps' light style: parks, water, road classes, places of interest, road
// shields) and "quiet" (paper, no icons). Glyphs and icons ship with the app.

import { layers, namedFlavor } from "@protomaps/basemaps";
import { BASEMAP_URL, LOCAL } from "../config.js";

const PAPER = {
  background: "#e4e7e5", earth: "#f2f3f1", water: "#d5dcdf",
  park_a: "#e5e9e2", park_b: "#e5e9e2", wood_a: "#e2e6df", wood_b: "#e2e6df", scrub_a: "#e8ebe5",
  scrub_b: "#e8ebe5", glacier: "#f4f5f4", sand: "#efefe9", beach: "#efefe9", hospital: "#ebedea",
  industrial: "#eaecea", school: "#ebedea", pedestrian: "#eef0ed", zoo: "#e5e9e2", military: "#e8eae7",
  aerodrome: "#e9ebe8", runway: "#dfe2df", pier: "#e3e6e3", buildings: "#e1e4e0",
  railway: "#c2c8c4", boundaries: "#8b938e",
  highway: "#ffffff", highway_casing_early: "#bcc3be", highway_casing_late: "#bcc3be",
  major: "#ffffff", major_casing_early: "#ccd2cd", major_casing_late: "#ccd2cd",
  link: "#ffffff", link_casing: "#d3d8d4", minor_a: "#fcfcfb", minor_b: "#fcfcfb",
  minor_casing: "#dce0dc", minor_service: "#fafbfa", minor_service_casing: "#e2e5e2", other: "#f7f8f6",
  ocean_label: "#8b938e", roads_label_minor: "#848c87", roads_label_minor_halo: "#f2f3f1",
  roads_label_major: "#6b736e", roads_label_major_halo: "#f2f3f1", subplace_label: "#7b837e",
  subplace_label_halo: "#f2f3f1", city_label: "#2b302d", city_label_halo: "#f2f3f1",
  state_label: "#8b938e", state_label_halo: "#f2f3f1", country_label: "#5b635e",
  address_label: "#8b938e", address_label_halo: "#f2f3f1",
};
const DROP = new Set(["roads_oneway", "roads_shields", "pois", "address_label"]);
export const OVERLAY_BEFORE = "roads_pier";        // hexagons go above buildings, below roads
export const LABELS_FROM = "water_waterway_label"; // first label layer

export const BASEMAPS = ["detailed", "quiet"];

export function basemapStyle(kind = "detailed") {
  const base = namedFlavor("light");
  if (kind === "detailed") {
    // beyond the edge of our US extract there are no tiles: paint it as sea so the edge disappears
    const list = layers("protomaps", { ...base, background: base.water }, { lang: "en" });
    return { version: 8, glyphs: glyphsUrl(), sprite: new URL("sprites/light", location.href).href,
      sources: source(), layers: list };
  }
  const flavor = { ...base, ...PAPER };
  for (const k of Object.keys(base)) {
    if (k.startsWith("tunnel_")) flavor[k] = k.endsWith("casing") ? "#d9ddd9" : "#eef0ed";
    if (k.startsWith("bridges_")) flavor[k] = k.endsWith("casing") ? "#c4cac5" : "#ffffff";
  }
  if (base.landcover) {
    flavor.landcover = { barren: "#efefea", farmland: "#eff0ea", forest: "#e3e7e0", glacier: "#f4f5f4",
      grassland: "#eaede6", scrub: "#e8ebe5", urban_area: "#eceeeb" };
  }
  const list = layers("protomaps", flavor, { lang: "en" })
    .filter((l) => !DROP.has(l.id))
    .map((l) => {
      if (l.layout && l.layout["icon-image"]) {
        const { "icon-image": _, "icon-size": __, ...layout } = l.layout;
        return { ...l, layout };
      }
      return l;
    });
  return { version: 8, glyphs: glyphsUrl(), sources: source(), layers: list };
}

function glyphsUrl() {
  return new URL(LOCAL.glyphs, location.href).href.replace("%7Bfontstack%7D", "{fontstack}").replace("%7Brange%7D", "{range}");
}

function source() {
  return { protomaps: { type: "vector", url: `pmtiles://${BASEMAP_URL}`, attribution: "© OpenStreetMap contributors · Protomaps" } };
}

// Swap the basemap and keep everything of ours (sources with their data, layers, order).
export function swapBasemap(map, kind) {
  map.setStyle(basemapStyle(kind), {
    diff: false,
    transformStyle: (prev, next) => {
      if (!prev) return next;
      const cut = prev.layers.findIndex((l) => l.id === OVERLAY_BEFORE);
      const mine = prev.layers.map((l, i) => [l, i]).filter(([l]) => l.source && l.source !== "protomaps");
      const low = mine.filter(([, i]) => i < cut).map(([l]) => l);
      const high = mine.filter(([, i]) => i >= cut).map(([l]) => l);
      const layers = [];
      for (const l of next.layers) {
        if (l.id === OVERLAY_BEFORE) layers.push(...low);
        if (l.id === LABELS_FROM) layers.push(...high);
        layers.push(l);
      }
      const sources = { ...next.sources };
      for (const [k, v] of Object.entries(prev.sources)) if (k !== "protomaps") sources[k] = v;
      return { ...next, sources, layers };
    },
  });
}
