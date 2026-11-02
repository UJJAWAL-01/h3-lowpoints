// The rain simulator: the same equations as the h3-lowpoints library, on one data pack.
//
//   own runoff   SCS curve number (h3lowpoints.runoff.runoff_mm), the catchment's curve number
//                adjusted to dry/average/wet ground (runoff.adjust_curve_number, NRCS table 10-1)
//   fill, spill  each depression keeps what fits and passes the rest along its spill link
//                (hydro.cascade); inflow from other packs is the pack's precomputed curve
//   level, area  from the depression's measured levels (storms.stage_of)
// Unknown (NaN) curve numbers stay unknown and pass downstream.

// NRCS NEH 630 ch. 10, table 10-1: [ARC II, ARC I, ARC III], sorted by ARC II
const ARC = (
  "0:0:0 5:2:13 10:4:22 15:6:30 20:9:37 25:12:43 30:15:50 31:16:51 32:16:52 33:17:53 34:18:54 " +
  "35:18:55 36:19:56 37:20:57 38:21:58 39:21:59 40:22:60 41:23:61 42:24:62 43:25:63 44:25:64 " +
  "45:26:65 46:27:66 47:28:67 48:29:68 49:30:69 50:31:70 51:31:70 52:32:71 53:33:72 54:34:73 " +
  "55:35:74 56:36:75 57:37:75 58:38:76 59:39:77 60:40:78 61:41:78 62:42:79 63:43:80 64:44:81 " +
  "65:45:82 66:46:82 67:47:83 68:48:84 69:50:84 70:51:85 71:52:86 72:53:86 73:54:87 74:55:88 " +
  "75:57:88 76:58:89 77:59:89 78:60:90 79:62:91 80:63:91 81:64:92 82:66:92 83:67:93 84:68:93 " +
  "85:70:94 86:72:94 87:73:95 88:75:95 89:76:96 90:78:96 91:80:97 92:81:97 93:83:98 94:85:98 " +
  "95:87:98 96:89:99 97:91:99 98:94:99 99:97:100 100:100:100"
).split(" ").map((r) => r.split(":").map(Number));

export const CONDITIONS = ["dry", "average", "wet"];

function interp(x, xs, ys) {                       // numpy.interp: clamped, linear
  if (x <= xs[0]) return ys[0];
  const n = xs.length;
  if (x >= xs[n - 1]) return ys[n - 1];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xs[m] <= x) lo = m; else hi = m; }
  if (xs[lo] === x) return ys[lo];
  return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo]);
}

const ARC_II = ARC.map((r) => r[0]), ARC_I = ARC.map((r) => r[1]), ARC_III = ARC.map((r) => r[2]);

export function adjustCurveNumber(cn, condition) {
  if (Number.isNaN(cn) || condition === "average") return cn;
  return interp(cn, ARC_II, condition === "dry" ? ARC_I : ARC_III);
}

export function runoffMm(rain, cn) {
  if (Number.isNaN(cn) || Number.isNaN(rain)) return NaN;
  const s = 25400 / cn - 254;
  const excess = Math.max(rain - 0.2 * s, 0);
  return excess > 0 ? (excess * excess) / (excess + s) : 0;
}

// storms._stage: (depth at the lowest point, wet area) of a depression holding `s` m3; its levels
// are vols/depths/areas[at .. at + count - 1] (at: the offset of its row in the pack's tables)
export function stage(s, vols, depths, areas, count, at = 0) {
  if (!(s > 0)) return Number.isNaN(s) ? [NaN, NaN] : [0, 0];
  const last = at + count - 1;
  if (s >= vols[last]) return [depths[last], areas[last]];
  if (s < vols[at]) {
    const k = Math.max((areas[at] * depths[at]) / vols[at], 1);
    const r = s / vols[at];
    return [depths[at] * r ** (1 / k), areas[at] * r ** ((k - 1) / k)];
  }
  let j = at + 1;
  while (vols[j] < s) j++;
  const r = s / vols[j - 1];
  const k = Math.log(vols[j] / vols[j - 1]) / Math.log(depths[j] / depths[j - 1]);
  const area = areas[j - 1] > 0 && areas[j] > areas[j - 1]
    ? areas[j - 1] * r ** (Math.log(areas[j] / areas[j - 1]) / Math.log(vols[j] / vols[j - 1]))
    : areas[j - 1];
  return [depths[j - 1] * r ** (1 / k), area];
}

// hydro.cascade._order: every depression before the one it spills into
export function spillOrder(target) {
  const n = target.length, waiting = new Int32Array(n), order = new Int32Array(n);
  for (let i = 0; i < n; i++) if (target[i] >= 0) waiting[target[i]]++;
  let tail = 0;
  for (let i = 0; i < n; i++) if (waiting[i] === 0) order[tail++] = i;
  for (let head = 0; head < tail; head++) {
    const j = target[order[head]];
    if (j >= 0 && --waiting[j] === 0) order[tail++] = j;
  }
  return order.subarray(0, tail);
}

// rain: a number (mm everywhere) or a Float64Array of mm per depression
export function simulate(pack, rain, condition = "average") {
  const n = pack.n, rows = pack.vols.length / n;
  const c = CONDITIONS.indexOf(condition);
  if (c < 0) throw new Error(`condition must be one of ${CONDITIONS}`);
  const rainOf = typeof rain === "number" ? () => rain : (i) => rain[i];
  const inflow = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const cn = adjustCurveNumber(pack.cn[i], condition);
    inflow[i] = (runoffMm(rainOf(i), cn) * pack.catch[i]) / 1000;
  }
  const grid = pack.ext_grid, g = grid.length;
  for (let k = 0; k < pack.ext_dep.length; k++) {
    const i = pack.ext_dep[k], at = (k * 3 + c) * g;
    inflow[i] += interp(rainOf(i), grid, pack.ext_inflow.subarray(at, at + g));
  }
  const order = pack._order || (pack._order = spillOrder(pack.target));
  let leaving = 0;
  for (const i of order) {
    const volume = pack.vols[i * rows + pack.count[i] - 1];
    const extra = inflow[i] - volume, j = pack.target[i];
    if (Number.isNaN(extra)) { if (j >= 0) inflow[j] = NaN; else leaving = NaN; }
    else if (extra > 0) { if (j >= 0) inflow[j] += extra; else leaving += extra; }
  }
  const store = new Float64Array(n), depth = new Float64Array(n), area = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const at = i * rows, volume = pack.vols[at + pack.count[i] - 1];
    store[i] = Number.isNaN(inflow[i]) ? NaN : Math.min(inflow[i], volume);
    [depth[i], area[i]] = stage(store[i], pack.vols, pack.depths, pack.areas, pack.count[i], at);
  }
  const m = pack.cell.length, cellWet = new Float64Array(m);
  for (let r = 0; r < m; r++) cellWet[r] = area[pack.cell_dep[r]] * pack.cell_share[r];
  return { inflow, store, depth, area, cellWet, leaving };
}
