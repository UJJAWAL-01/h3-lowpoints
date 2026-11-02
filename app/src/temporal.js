// When the rain falls: NOAA Atlas 14 temporal distributions (data/temporal.json, from
// app/tools/temporal.py), as the h3-lowpoints library uses them. Pure (worker and page).
//
// In this model what a depression holds depends only on the rain fallen so far (SCS runoff of
// the cumulative rain, method A, no drains, no seepage), so the state at any hour of the storm is
// the state at the rain fallen by then: time = the hour at which that share of the rain has
// fallen, on the region's curve. Rain reaches a depression as it falls (no travel time over the
// ground), as in the library, so times are the earliest a depression can fill.

export const PUBLISHED = [6, 12, 24, 96];
export const CASES = ["all", "first", "second", "third", "fourth"];

// the published duration nearest in log time (library: nearest_temporal_hours)
export function nearestHours(hours) {
  let best = PUBLISHED[0];
  for (const h of PUBLISHED) if (Math.abs(Math.log(h) - Math.log(hours)) < Math.abs(Math.log(best) - Math.log(hours))) best = h;
  return best;
}

// share of the rain fallen at time share t (0..1); curve = cumulative shares at equal times
export function rainAt(curve, t) {
  const n = curve.length - 1, x = Math.max(0, Math.min(1, t)) * n, i = Math.min(Math.floor(x), n - 1);
  return curve[i] + (curve[i + 1] - curve[i]) * (x - i);
}

// the first time share at which share r of the rain has fallen (curve rising, flat parts allowed)
export function timeAt(curve, r) {
  const n = curve.length - 1;
  if (r <= curve[0]) return 0;
  if (r >= curve[n]) { let i = n; while (i > 0 && curve[i - 1] >= curve[n]) i--; return i / n; }
  let i = 0;
  while (curve[i + 1] < r) i++;
  const d = curve[i + 1] - curve[i];
  return (i + (d > 0 ? (r - curve[i]) / d : 0)) / n;
}

// "35 min", "9 h 30 min", "3 days 4 h"
export function duration(hours) {
  const m = Math.max(0, Math.round(hours * 60));
  if (m < 60) return `${m} min`;
  if (m < 48 * 60) { const h = Math.floor(m / 60), r = m % 60; return r ? `${h} h ${r} min` : `${h} h`; }
  const d = Math.floor(m / 1440), h = Math.round((m % 1440) / 60);
  return h ? `${d} days ${h} h` : `${d} days`;
}

// region at a point: data.regions[res-4 cell][index of the res-5 cell among its sorted children]
export function regionAt(data, r4, r5, children) {
  const list = data.regions[r4];
  if (!list) return null;
  const k = children.slice().sort().indexOf(r5);
  const i = k >= 0 ? list[k] : -1;
  return i >= 0 ? data.names[i] : null;
}
