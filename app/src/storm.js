// One storm over one data pack, in 16 exact steps: the engine of the rain simulator.
//
// A storm is { kind: "amount", mm, hours } (the same depth everywhere) or
// { kind: "rarity", years, hours } (NOAA Atlas 14's depth of that storm at each depression's lowest
// point, from the res-7 tables), plus the ground before it: condition "dry", "average" or "wet".
// Step s (0..15) is the storm with (s + 1) / 16 of its rain, run through the verified simulator
// (sim.js, equal to the library); every step is exact for its depth, nothing is interpolated.
// Storm totals: how the rain is spread over the hours does not change what is held (study 2).
// Pure: no browser or h3 calls, so the Node checks run exactly this code.

import { CONDITIONS, simulate } from "./sim.js";
import { YEARS, depthsAt, stormDepth } from "./rarity.js";
import { full, parentOf } from "./levels.js";

export const STEPS = 16;
export const MAX_MM = 1000;        // the inflow curves from neighbouring areas stop at 1,000 mm
const FILLED = 1 - 1e-9;

export function normaliseStorm(s) {
  if (!s || typeof s !== "object") throw new Error("no storm given");
  const hours = Number(s.hours);
  if (!(hours >= 1 && hours <= 48)) throw new Error("the storm must last 1 to 48 hours");
  const condition = s.condition ?? "average";
  if (!CONDITIONS.includes(condition)) throw new Error(`ground must be one of ${CONDITIONS.join(", ")}`);
  if (s.kind === "amount") {
    const mm = Number(s.mm);
    if (!(mm > 0 && mm <= MAX_MM)) throw new Error(`rain must be more than 0 and at most ${MAX_MM} mm`);
    return { kind: "amount", mm, hours, condition };
  }
  if (s.kind === "rarity") {
    const years = Number(s.years);
    if (!YEARS.includes(years)) throw new Error(`years must be one of ${YEARS.join(", ")}`);
    return { kind: "rarity", years, hours, condition };
  }
  throw new Error("storm kind must be amount or rarity");
}

export const stormKey = (s) => [s.kind, s.kind === "amount" ? s.mm : s.years, s.hours, s.condition].join("|");

// Rain (mm) for every depression: a number, or one depth per depression (NaN where Atlas 14 has
// no data, e.g. Washington and Oregon). `rarity` = { block, res } for the pack's region, or null.
export function stormRain(pk, storm, rarity) {
  if (storm.kind === "amount") return storm.mm;
  const rain = new Float64Array(pk.n).fill(NaN);
  if (!rarity || !rarity.block) return rain;
  const memo = new Map();
  for (let i = 0; i < pk.n; i++) {
    const c = parentOf(pk.low[i], rarity.res);
    let d = memo.get(c);
    if (d === undefined) {
      const table = depthsAt(rarity.block, c);
      d = table ? stormDepth(storm.years, storm.hours, table) : NaN;
      memo.set(c, d);
    }
    rain[i] = d;
  }
  return rain;
}

// The 16 steps. Arrays are step-major: value of depression i at step s is at [s * n + i].
export function runStorm(pk, rain, condition) {
  const n = pk.n;
  const store = new Float32Array(n * STEPS), depth = new Float32Array(n * STEPS), area = new Float32Array(n * STEPS);
  const fillStep = new Int8Array(n).fill(-1), spillStep = new Int8Array(n).fill(-1);
  const volume = new Float64Array(n);
  for (let i = 0; i < n; i++) volume[i] = full(pk, i, pk.vols);
  const scaled = typeof rain === "number" ? null : new Float64Array(n);
  let last = null;
  for (let s = 0; s < STEPS; s++) {
    const f = (s + 1) / STEPS;
    let r;
    if (scaled) { for (let i = 0; i < n; i++) scaled[i] = rain[i] * f; r = scaled; } else r = rain * f;
    last = simulate(pk, r, condition);
    store.set(last.store, s * n); depth.set(last.depth, s * n); area.set(last.area, s * n);
    for (let i = 0; i < n; i++) {
      if (fillStep[i] < 0 && last.store[i] >= volume[i] * FILLED) fillStep[i] = s;
      if (spillStep[i] < 0 && last.inflow[i] > volume[i]) spillStep[i] = s;
    }
  }
  return { n, store, depth, area, fillStep, spillStep, volume, inflow: last.inflow, leaving: last.leaving };
}

// Water per drawn hexagon at each step, [k * STEPS + s]: at level 10 the depth of water at the
// hexagon's deepest point (m), at levels 8 and 9 the share of the hexagon under water. `L` from
// levels.levelCells, `cellAreas[k]` the hexagon's area (m2). NaN where a depression in it is unknown.
export function levelWater(pk, L, run, level, cellAreas) {
  const K = L.cell.length, n = run.n, out = new Float32Array(K * STEPS);
  const unknown = new Uint8Array(K);
  for (let r = 0; r < pk.cell.length; r++) {
    const k = L.rowK[r], d = pk.cell_dep[r], at = k * STEPS;
    if (k < 0) continue;                                   // depression left out (filter)
    if (Number.isNaN(run.store[(STEPS - 1) * n + d])) { unknown[k] = 1; continue; }
    if (level === 10) {
      const floor = full(pk, d, pk.depths) - pk.cell_max_depth[r];      // below the full level
      for (let s = 0; s < STEPS; s++) {
        const w = run.depth[s * n + d] - floor;
        if (w > out[at + s]) out[at + s] = w;
      }
    } else {
      for (let s = 0; s < STEPS; s++) out[at + s] += run.area[s * n + d] * pk.cell_share[r];
    }
  }
  for (let k = 0; k < K; k++) {
    if (unknown[k]) out.fill(NaN, k * STEPS, k * STEPS + STEPS);
    else if (level !== 10) for (let s = 0; s < STEPS; s++) out[k * STEPS + s] /= cellAreas[k];
  }
  return out;
}

// Counts at the end of the storm for the depressions with `pick(i)` true (e.g. lowest point in view).
// likely(i): true for a likely drained depression (counted apart among those that fill).
// rain: the storm's mm (a number, or one per depression) for the range of depths in the view.
export function summarise(pk, run, pick = () => true, likely = null, rain = null) {
  const n = run.n, last = (STEPS - 1) * n;
  const out = { depressions: 0, unknown: 0, fill: 0, fillLikelyDrained: 0, overflow: 0, holdWater: 0,
    wetAreaM2: 0, heldM3: 0, leavingM3: run.leaving, rainMin: Infinity, rainMax: -Infinity, first: [],
    steps: Array.from({ length: STEPS }, () => ({ fill: 0, overflow: 0, holdWater: 0, wetAreaM2: 0, heldM3: 0 })) };
  for (let i = 0; i < n; i++) {
    if (!pick(i)) continue;
    out.depressions++;
    const r = rain == null ? NaN : typeof rain === "number" ? rain : rain[i];
    if (r < out.rainMin) out.rainMin = r;
    if (r > out.rainMax) out.rainMax = r;
    const st = run.store[last + i];
    if (Number.isNaN(st)) { out.unknown++; continue; }
    // the same figures after each step of the storm (the panel follows the timeline)
    for (let s = 0; s < STEPS; s++) {
      const t = out.steps[s], sst = run.store[s * n + i];
      if (run.fillStep[i] >= 0 && run.fillStep[i] <= s) t.fill++;
      if (run.spillStep[i] >= 0 && run.spillStep[i] <= s) t.overflow++;
      if (sst > 0) t.holdWater++;
      t.wetAreaM2 += run.area[s * n + i];
      t.heldM3 += sst;
    }
    if (run.fillStep[i] >= 0) { out.fill++; if (likely && likely(i)) out.fillLikelyDrained++; }
    if (run.spillStep[i] >= 0) out.overflow++;
    if (st > 0) out.holdWater++;
    out.wetAreaM2 += run.area[last + i];
    out.heldM3 += st;
    if (run.fillStep[i] >= 0) out.first.push([run.fillStep[i], -run.volume[i], i]);
  }
  if (!(out.rainMin <= out.rainMax)) out.rainMin = out.rainMax = NaN;
  // fill earliest: the earliest step, then the largest (a tiny hollow filling first says little)
  out.first.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
  out.first = out.first.slice(0, 5).map(([step, v, i]) => ({ i, step, volume: -v }));
  return out;
}

// The whole storm on dry or wet ground: how many fill and overflow (for the range)
export function rangeCounts(pk, rain, condition, pick = () => true) {
  const r = simulate(pk, rain, condition);
  let fill = 0, overflow = 0;
  for (let i = 0; i < pk.n; i++) {
    if (!pick(i) || Number.isNaN(r.store[i])) continue;
    const v = full(pk, i, pk.vols);
    if (r.store[i] >= v * FILLED) fill++;
    if (r.inflow[i] > v) overflow++;
  }
  return { condition, fill, overflow };
}

// One depression through the storm (the gauge in its panel)
export function depressionSteps(run, i) {
  const n = run.n, v = run.volume[i], steps = [];
  for (let s = 0; s < STEPS; s++) {
    const st = run.store[s * n + i];
    steps.push({ full: st / v, depth: run.depth[s * n + i], area: run.area[s * n + i],
      overflowing: run.spillStep[i] >= 0 && s >= run.spillStep[i] });
  }
  return { volume: v, fillStep: run.fillStep[i], spillStep: run.spillStep[i], steps };
}
