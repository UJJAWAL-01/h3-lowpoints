// How tall columns stand and how deep wells go in the 3D views. Stated in the legend, so the
// visitor always knows the heights are exaggerated and by how much. Pure (used by the worker too).

export const VIEWS = ["flat", "columns", "wells"];

// Street level (H3 resolution 10): metres drawn per metre of water depth when full
export const DEPTH_EXAGGERATION = 30;

// Coarser levels: metres drawn per 1 % of the hexagon in depressions, scaled with the hexagon's
// size so columns look alike at every zoom; shares above SHARE_CAP are drawn at the cap
export const SHARE_M_PER_PCT = { 4: 1500, 5: 600, 6: 230, 8: 30, 9: 12 };
export const SHARE_CAP = 0.25;

export function columnHeight(level, { d, s }) {
  if (level === 10) return d * DEPTH_EXAGGERATION;
  return Math.min(s, SHARE_CAP) * 100 * SHARE_M_PER_PCT[level];
}

// Water drawn in a column at one step: water depth (level 10) or share under water (8, 9)
export function waterHeight(level, w) {
  if (!(w > 0)) return 0;
  if (level === 10) return w * DEPTH_EXAGGERATION;
  return Math.min(w, SHARE_CAP) * 100 * SHARE_M_PER_PCT[level];
}

// Mercator helpers (MapLibre's world units: x, y in 0..1; z in the same units per metre)
const EARTH_CIRCUMFERENCE = 2 * Math.PI * 6371008.8;
export const mercX = (lon) => (lon + 180) / 360;
export const mercY = (lat) => (1 - Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / Math.PI) / 2;
export const mercZPerMetre = (lat) => 1 / (EARTH_CIRCUMFERENCE * Math.cos((lat * Math.PI) / 180));
