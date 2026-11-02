// Everything the app needs to know about where things are. Change hosts here, nowhere else.

export const VERSION = "0.1.0";

export const DATASET = { version: "0.3", doi: "10.5281/zenodo.23731345" };

// Data packs: tried in order; a host that fails is skipped for five minutes.
export const PACK_HOSTS = [
  // pinned to a commit: a later upload cannot mix an old index with new packs
  { name: "Hugging Face", url: "https://huggingface.co/datasets/h3-lowpoints/explorer-data/resolve/110026efd830b923bff8707a910c48c67ba86f5f/{file}" },
  { name: "Zenodo", url: "https://zenodo.org/api/records/23875375/files/{file}/content" },
];
export const PACKS_DOI = "10.5281/zenodo.23875375";

export const BASEMAP_URL =
  "https://huggingface.co/datasets/h3-lowpoints/explorer-basemap/resolve/15e4850ea9f89e60a0842cb5d91ba1decd1f546c/us48_osm_z14.pmtiles";
export const DOCS_URL = "https://ujjawal-01.github.io/h3-lowpoints";

// Files that ship with the app (same origin)
export const LOCAL = { data: "data/", glyphs: "glyphs/{fontstack}/{range}.pbf" };

// Zoom at which single depressions replace the overview, and the levels by zoom (Auto)
export const DETAIL_ZOOM = 10.5;
export const AUTO_LEVELS = [
  { res: 4, below: 5.3 }, { res: 5, below: 7.0 }, { res: 6, below: DETAIL_ZOOM },
  { res: 8, below: 12.0 }, { res: 9, below: 13.4 }, { res: 10, below: Infinity },
];
export const OVERVIEW_LEVELS = [4, 5, 6];
export const DETAIL_LEVELS = [8, 9, 10];

export const HOME = { center: [-97.6, 38.6], zoom: 3.55 };
export const BOUNDS = [[-130, 22], [-63, 51]];
export const MAX_REGION_SOURCES = 18;           // packs drawn at once; farther ones are dropped

// For local testing only: ?packs=URL-template is honoured on localhost, never on the public site.
export function packHosts() {
  const local = ["localhost", "127.0.0.1"].includes(location.hostname);
  const override = new URLSearchParams(location.search).get("packs");
  return local && override ? [{ name: "local", url: override }] : PACK_HOSTS;
}
