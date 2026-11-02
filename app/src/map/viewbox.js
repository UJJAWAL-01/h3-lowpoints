// The part of the map in view, [w, s, e, n], for loading data and for "in this view" figures.
// Seen from above it is the map's bounds. Tilted (3D), the plain bounds run out to the horizon and
// would load data for land too far away to see; then the box is the ground actually in view, from
// the bottom of the screen to a little below the top, capped at 3 times the flat view's size.

const CAP = 3;

export function viewBox(map) {
  const b = map.getBounds();
  const flat = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  if (map.getPitch() < 5) return flat;
  const c = map.getCanvas(), w = c.clientWidth, h = c.clientHeight;
  const pts = [[0, h], [w, h], [0, h * 0.12], [w, h * 0.12], [w / 2, h * 0.12]].map((p) => map.unproject(p));
  let west = Math.min(...pts.map((p) => p.lng)), east = Math.max(...pts.map((p) => p.lng));
  let south = Math.min(...pts.map((p) => p.lat)), north = Math.max(...pts.map((p) => p.lat));
  // the flat view's half-size at this zoom (512-pixel tiles)
  const centre = map.getCenter(), deg = 360 / (512 * 2 ** map.getZoom());
  const hx = (w / 2) * deg * CAP, hy = (h / 2) * deg * Math.cos((centre.lat * Math.PI) / 180) * CAP;
  west = Math.max(west, centre.lng - hx); east = Math.min(east, centre.lng + hx);
  south = Math.max(south, centre.lat - hy); north = Math.min(north, centre.lat + hy);
  return [west, south, east, north];
}
