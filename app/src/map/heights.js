// How tall columns stand and how deep wells go in the 3D views, and their colours. The
// exaggeration is always stated on the map, so the visitor knows heights are not to scale (unless
// they choose x1). Pure (used by the worker too).

export const VIEWS = ["flat", "columns", "wells"];

// Street level (H3 resolution 10): metres drawn per metre of water depth when full (the visitor
// chooses; x1 is true scale)
export const EXAGGERATIONS = [1, 5, 10];
export const DEFAULT_EXAGGERATION = 10;

// Coarser levels: metres drawn per 1 % of the hexagon in depressions, scaled with the hexagon's
// size so columns look alike at every zoom; shares above SHARE_CAP are drawn at the cap
export const SHARE_M_PER_PCT = { 4: 1500, 5: 600, 6: 230, 8: 30, 9: 12 };
export const SHARE_CAP = 0.25;

// Depth in 3D: light sand (shallow) to deep brown (deep); one hue getting darker, so it reads in
// greyscale and for colour-blind visitors, and blue stays reserved for water
export const DEPTH_BREAKS = [0.25, 0.5, 1, 2, 4, 8];
export const EARTH = ["#f1e7d0", "#e3cc9c", "#d0ab6d", "#b78748", "#956533", "#6d4624", "#462c17"];
export function depthClass(d) {
  let i = 0;
  while (i < DEPTH_BREAKS.length && d >= DEPTH_BREAKS[i]) i++;
  return i;
}

// Share levels only (level 10 columns are depth x the chosen exaggeration, computed on the map)
export function columnHeight(level, { s }) {
  return Math.min(s, SHARE_CAP) * 100 * SHARE_M_PER_PCT[level];
}

// Water drawn in a column at one step: water depth (level 10) or share under water (8, 9)
export function waterHeight(level, w, exaggeration) {
  if (!(w > 0)) return 0;
  if (level === 10) return w * exaggeration;
  return Math.min(w, SHARE_CAP) * 100 * SHARE_M_PER_PCT[level];
}

// Mercator helpers (MapLibre's world units: x, y in 0..1; z in the same units per metre)
const EARTH_CIRCUMFERENCE = 2 * Math.PI * 6371008.8;
export const mercX = (lon) => (lon + 180) / 360;
export const mercY = (lat) => (1 - Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / Math.PI) / 2;
export const mercZPerMetre = (lat) => 1 / (EARTH_CIRCUMFERENCE * Math.cos((lat * Math.PI) / 180));
