// The data worker: everything heavy happens here, so the map never stutters.
// It reads the national overview and the data packs, builds the hexagons to draw, and answers
// questions about single depressions. The page talks to it through src/data/client.js.

import { cellArea, cellToBoundary, cellToLatLng, gridDisk, latLngToCell, polygonToCells } from "h3-js";
import { Hosts } from "./data/hosts.js";
import { columnHeight, depthClass, mercX, mercY, mercZPerMetre } from "./map/heights.js";
import { full, levelCells as groupCells, parentOf } from "./levels.js";
import { gunzip, parsePack } from "./pack.js";
import { YEARS, depthsAt, parseRarity, returnPeriod, stormDepths } from "./rarity.js";
import { STEPS, depressionSteps, levelWater, normaliseStorm, rangeCounts, runStorm, stormKey, stormRain,
  summarise } from "./storm.js";

const MAX_PACKS = 48;                    // decoded packs kept in memory (about 2-5 MB each)
const MAX_RUNS = 2;                      // storms kept per pack (the current one and the last)
const MAX_PATH = 200, MAX_PATH_PACKS = 4; // overflow path: steps, and extra packs it may load
let hosts, local, index = null, indexPromise = null;
const overview = {};                     // res -> decoded overview
const packs = new Map();                 // region -> {pack, derived}, in least-recently-used order
const pending = new Map();               // region -> promise (one request per pack at a time)

const handlers = { init, national, overviewCells, regionsIn, pack, depression, findDepression, regionOf, example,
  rarityAt, stormLevel, stormSummary, stormDepression, wellMesh, nearestDepression };

self.onmessage = async (e) => {
  const { id, type, args } = e.data;
  try {
    const result = await handlers[type](...(args || []));
    self.postMessage({ id, result });
  } catch (err) {
    self.postMessage({ id, error: String((err && err.message) || err) });
  }
};

function init(config) {
  hosts = new Hosts(config.hosts);
  local = config.local;
  return true;
}

// ------------------------------------------------------------------ H3 helpers on BigInt cells
const hex = (b) => b.toString(16);

const ring = (h) => cellToBoundary(h, true);       // GeoJSON order, already closed

// ------------------------------------------------------------------ national overview
const overviewPending = {};
function loadOverview(res) {
  if (overview[res]) return Promise.resolve(overview[res]);
  if (!overviewPending[res]) {
    overviewPending[res] = readOverview(res).finally(() => { delete overviewPending[res]; });
  }
  return overviewPending[res];
}

async function readOverview(res) {
  const r = await fetch(`${local}overview_${res}.bin`);
  if (!r.ok) throw new Error(`overview ${res}: HTTP ${r.status}`);
  const raw = await gunzip(new Uint8Array(await r.arrayBuffer()));
  const buf = raw.byteOffset % 8 === 0 ? raw : raw.slice();
  const n = buf.byteLength / (8 + 12 + 12);
  const cells = new BigUint64Array(buf.buffer, buf.byteOffset, n);
  const f = new Float32Array(buf.buffer, buf.byteOffset + 8 * n, 3 * n);
  const u = new Uint32Array(buf.buffer, buf.byteOffset + 20 * n, 3 * n);
  const o = { n, cells, covered: f.subarray(0, n), depressed: f.subarray(n, 2 * n), volume: f.subarray(2 * n),
    count: u.subarray(0, n), fills2: u.subarray(n, 2 * n), landlocked: u.subarray(2 * n) };
  if (res > 4) {                                    // cells of each res-4 region, for drawing a view
    o.byRegion = new Map();
    for (let i = 0; i < n; i++) {
      const p = parentOf(cells[i], 4);
      let list = o.byRegion.get(p);
      if (!list) o.byRegion.set(p, (list = []));
      list.push(i);
    }
  }
  overview[res] = o;
  return o;
}

async function national() {
  const o = await loadOverview(4);
  let cnt = 0, f2 = 0, ll = 0, dep = 0, cov = 0, vol = 0;
  for (let i = 0; i < o.n; i++) {
    cnt += o.count[i]; f2 += o.fills2[i]; ll += o.landlocked[i];
    dep += o.depressed[i]; cov += o.covered[i]; vol += o.volume[i];
  }
  // depressed area as an area, not a share: the dataset's cells cover only ground with depressions
  return { depressions: cnt, fills2Share: f2 / cnt, landlockedShare: ll / cnt, depressedM2: dep, volumeM3: vol };
}

// Res-4 regions whose hexagon touches a box [w, s, e, n] (with a margin)
function regionsIn(box, margin = 0.15) {
  const [w0, s0, e0, n0] = box;
  const dx = (e0 - w0) * margin, dy = (n0 - s0) * margin;
  const w = w0 - dx, s = s0 - dy, e = e0 + dx, n = n0 + dy;
  const seeds = new Set(polygonToCells([[[w, s], [e, s], [e, n], [w, n], [w, s]]], 4, true));
  for (const [x, y] of [[w, s], [e, s], [e, n], [w, n], [(w + e) / 2, (s + n) / 2]]) seeds.add(latLngToCell(y, x, 4));
  const out = new Set();
  for (const c of seeds) {
    for (const k of gridDisk(c, 1)) {
      if (out.has(k)) continue;
      const b = cellToBoundary(k, true);
      let bw = 180, bs = 90, be = -180, bn = -90;
      for (const [x, y] of b) { bw = Math.min(bw, x); be = Math.max(be, x); bs = Math.min(bs, y); bn = Math.max(bn, y); }
      if (bw <= e && be >= w && bs <= n && bn >= s) out.add(k);
    }
  }
  return [...out];
}

async function overviewCells(res, box) {
  const o = await loadOverview(res);
  let idx;
  if (box && o.byRegion) {
    idx = [];
    for (const r of regionsIn(box, 0.25)) {
      const list = o.byRegion.get(BigInt("0x" + r));
      if (list) idx.push(...list);
    }
  } else {
    idx = Array.from({ length: o.n }, (_, i) => i);
  }
  const features = new Array(idx.length);
  for (let k = 0; k < idx.length; k++) {
    const i = idx[k], h = hex(o.cells[i]), c = o.count[i];
    features[k] = {
      type: "Feature", id: i,
      geometry: { type: "Polygon", coordinates: [ring(h)] },
      // s: share of the whole hexagon in depressions (hexagons at coasts and borders are partly
      // outside the data, so theirs is low)
      properties: { h, s: o.depressed[i] / cellArea(h, "m2"), c,
        f: c ? o.fills2[i] / c : 0, l: c ? o.landlocked[i] / c : 0, v: o.volume[i], a: o.depressed[i] },
    };
    features[k].properties.hc = Math.round(columnHeight(res, features[k].properties));
  }
  return { type: "FeatureCollection", features };
}

// ------------------------------------------------------------------ packs
async function packIndex() {
  if (index) return index;
  if (!indexPromise) {
    indexPromise = hosts.json("index.json").then((j) => (index = j));
    indexPromise.catch(() => { indexPromise = null; });       // retry on the next request
  }
  return indexPromise;
}

async function getPack(region) {
  if (packs.has(region)) {
    const v = packs.get(region);
    packs.delete(region);
    packs.set(region, v);
    return v;
  }
  if (pending.has(region)) return pending.get(region);
  const p = (async () => {
    const idx = await packIndex();
    const entry = idx.packs[region];
    if (!entry) return null;
    const bytes = await hosts.range(idx.file, entry[0], entry[1]);
    const pk = parsePack(await gunzip(bytes));
    if (pk.header.region !== region) throw new Error(`pack mismatch: asked ${region}, got ${pk.header.region}`);
    const v = { region, pack: pk, levels: {}, areas: {}, upstream: null, ids: null, lowLL: null, runs: new Map() };
    packs.set(region, v);
    while (packs.size > MAX_PACKS) packs.delete(packs.keys().next().value);
    return v;
  })();
  pending.set(region, p);
  try { return await p; } finally { pending.delete(region); }
}

// Cells to draw at one level: deepest point, depressed area, the depression with most area there
function levelCells(v, level) {
  return v.levels[level] || (v.levels[level] = groupCells(v.pack, level));
}

function cellAreas(v, level) {
  if (v.areas[level]) return v.areas[level];
  const L = levelCells(v, level), a = new Float64Array(L.cell.length);
  for (let k = 0; k < a.length; k++) a[k] = cellArea(hex(L.cell[k]), "m2");
  return (v.areas[level] = a);
}

function indexOfId(v, id) {
  if (!v.ids) { v.ids = new Map(); v.pack.id.forEach((x, i) => v.ids.set(x, i)); }
  const i = v.ids.get(BigInt(id));
  return i === undefined ? -1 : i;
}

function lowLatLng(v) {
  if (v.lowLL) return v.lowLL;
  const pk = v.pack, ll = new Float64Array(2 * pk.n);
  for (let i = 0; i < pk.n; i++) { const [la, lo] = cellToLatLng(hex(pk.low[i])); ll[2 * i] = la; ll[2 * i + 1] = lo; }
  return (v.lowLL = ll);
}

async function pack(region, level) {
  const v = await getPack(region);
  if (!v) return { region, empty: true };
  const L = levelCells(v, level);
  const features = new Array(L.cell.length);
  for (let k = 0; k < L.cell.length; k++) {
    const h = hex(L.cell[k]);
    const p = level === 10
      ? { h, d: Math.round(L.depth[k] * 100) / 100, a: Math.round(L.area[k]), dep: L.dep[k], n: L.n[k] }
      : { h, s: L.area[k] / cellArea(h, "m2"), d: Math.round(L.depth[k] * 100) / 100, a: Math.round(L.area[k]), dep: L.dep[k], n: L.n[k] };
    if (level !== 10) p.hc = Math.round(columnHeight(level, p) * 10) / 10;   // share columns (m); level 10 = d x chosen factor
    features[k] = {
      type: "Feature", id: k,
      geometry: { type: "Polygon", coordinates: [ring(h)] },
      // levels 8 and 9 are shaded by the share of the hexagon in depressions (s), level 10 by depth (d)
      properties: p,
    };
  }
  return { region, level, depressions: v.pack.n, fc: { type: "FeatureCollection", features } };
}

function upstreamOf(v) {
  if (v.upstream) return v.upstream;
  const t = v.pack.target, up = new Map();
  for (let j = 0; j < t.length; j++) {
    if (t[j] >= 0) {
      let list = up.get(t[j]);
      if (!list) up.set(t[j], (list = []));
      list.push(j);
    }
  }
  v.upstream = up;
  return up;
}

const lngLat = (cell) => { const [la, lo] = cellToLatLng(hex(cell)); return [lo, la]; };

async function depression(region, i) {
  const v = await getPack(region);
  if (!v) throw new Error("no data for this area");
  const pk = v.pack;
  if (!(i >= 0 && i < pk.n)) throw new Error("no such depression");
  const cells = [];
  for (let r = 0; r < pk.cell.length; r++) {
    if (pk.cell_dep[r] === i) {
      const h = hex(pk.cell[r]);
      cells.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [ring(h)] },
        properties: { d: pk.cell_max_depth[r] } });
    }
  }
  const path = await overflowPath(v, i);
  const chain = path.coords, chainIdx = path.idx, leaves = path.leaves;
  const up = upstreamOf(v).get(i) || [];
  let receivesOutside = false;
  for (let k = 0; k < pk.ext_dep.length; k++) if (pk.ext_dep[k] === i) { receivesOutside = true; break; }
  const [lat, lon] = cellToLatLng(hex(pk.low[i]));
  const t = chainIdx.length > 1 ? chainIdx[1] : -1;
  return {
    region, index: i, id: pk.id[i].toString(), low: hex(pk.low[i]), lat, lon,
    depth: full(pk, i, pk.depths), area: full(pk, i, pk.areas), volume: full(pk, i, pk.vols),
    catchment: pk.catch[i], curveNumber: pk.cn[i], rainToFill: pk.rain_to_fill[i],
    fillsInYears: pk.fills_in_years[i], drained: (pk.flags[i] & 1) === 1, landlocked: (pk.flags[i] & 2) === 2,
    target: t, targetRegion: t >= 0 ? path.regions[1] : null, targetLowLngLat: t >= 0 ? chain[1] : null,
    chainSteps: chain.length - 1, chainLeaves: leaves, chainIdx, chainRegions: path.regions, chainIds: path.ids,
    chainOtherAreas: path.packs, chainCut: path.cut, cellCount: cells.length,
    upstream: up.length, receivesOutside,
    cells: { type: "FeatureCollection", features: cells },
    chain: { type: "Feature", geometry: { type: "LineString", coordinates: chain }, properties: {} },
    upstreamPoints: { type: "FeatureCollection", features: up.slice(0, 500).map((j) => ({
      type: "Feature", geometry: { type: "LineString", coordinates: [lngLat(pk.low[j]), chain[0]] }, properties: { i: j } })) },
  };
}

async function findDepression(region, id) {
  const v = await getPack(region);
  if (!v) return -1;
  const want = BigInt(id);
  const ids = v.pack.id;
  for (let i = 0; i < ids.length; i++) if (ids[i] === want) return i;
  return -1;
}

// A good depression to show in the guide: it overflows on through at least two more, others
// overflow into it, and it is big enough to see; the nearest such one to a point
async function example(region, lat, lon) {
  const v = await getPack(region);
  if (!v) return -1;
  const pk = v.pack, up = upstreamOf(v);
  let best = -1, bestD = Infinity;
  for (let i = 0; i < pk.n; i++) {
    const t = pk.target[i];
    if (t < 0 || pk.target[t] < 0 || !up.has(i) || full(pk, i, pk.areas) < 20000) continue;
    const [la, lo] = cellToLatLng(hex(pk.low[i]));
    const d = (la - lat) ** 2 + ((lo - lon) * Math.cos(lat * Math.PI / 180)) ** 2;
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

function regionOf(lat, lon) {
  return latLngToCell(lat, lon, 4);
}

// ------------------------------------------------------------------ overflow path, across packs
// From depression i, follow where each full depression overflows, into neighbouring packs too
// (format 2: target_id and target_region). Ends where the overflow leaves the map (spill_to 0),
// or is cut after MAX_PATH steps or MAX_PATH_PACKS extra packs.
async function overflowPath(v, i) {
  const coords = [], idx = [], regions = [], ids = [], seen = new Set();
  const others = new Set();
  let cur = v, j = i, leaves = false, cut = false;
  while (true) {
    const pk = cur.pack, key = `${cur.region}.${j}`;
    if (seen.has(key)) { cut = true; break; }         // never happens in valid data; guards a loop
    seen.add(key);
    coords.push(lngLat(pk.low[j])); idx.push(j); regions.push(cur.region); ids.push(pk.id[j].toString());
    if (coords.length > MAX_PATH) { cut = true; break; }
    if (pk.target[j] >= 0) { j = pk.target[j]; continue; }
    if (!pk.target_region) { cut = true; break; }      // format 1: where it goes is not stored
    const tr = pk.target_region[j];
    if (!tr) { leaves = true; break; }                 // spill_to 0: leaves the map
    const region = hex(tr);
    if (!others.has(region) && others.size >= MAX_PATH_PACKS) { cut = true; break; }
    let next;
    try { next = await getPack(region); } catch { next = null; }
    if (!next) { cut = true; break; }
    const k = indexOfId(next, pk.target_id[j]);
    if (k < 0) { cut = true; break; }
    others.add(region);
    cur = next; j = k;
  }
  return { coords, idx, regions, ids, leaves, cut, packs: others.size };
}

// ------------------------------------------------------------------ rarity tables (NOAA Atlas 14)
let rarityIndex = null, rarityIndexPromise = null;
const rarityBlocks = new Map(), rarityPending = new Map();

async function getRarityIndex() {
  if (rarityIndex) return rarityIndex;
  if (!rarityIndexPromise) {
    rarityIndexPromise = hosts.json("rarity_index.json").then((j) => (rarityIndex = j));
    rarityIndexPromise.catch(() => { rarityIndexPromise = null; });
  }
  return rarityIndexPromise;
}

// { block, res } for a res-4 region, or { block: null } where there is no table
async function rarityOf(region) {
  if (rarityBlocks.has(region)) return rarityBlocks.get(region);
  if (rarityPending.has(region)) return rarityPending.get(region);
  const p = (async () => {
    const ri = await getRarityIndex();
    const entry = ri.regions[region];
    let out = { block: null, res: ri.res || 7 };
    if (entry) {
      const [off, len, n] = entry;
      out.block = parseRarity(await gunzip(await hosts.range(ri.file, off, len)), n);
    }
    rarityBlocks.set(region, out);
    while (rarityBlocks.size > MAX_PACKS) rarityBlocks.delete(rarityBlocks.keys().next().value);
    return out;
  })();
  rarityPending.set(region, p);
  try { return await p; } finally { rarityPending.delete(region); }
}

// How rare `mm` in `hours` is at a point, and the depth of each return period's storm there
async function rarityAt(lat, lon, hours, mm) {
  const r = await rarityOf(latLngToCell(lat, lon, 4));
  if (!r.block) return { covered: false, years: YEARS };
  const table = depthsAt(r.block, BigInt("0x" + latLngToCell(lat, lon, r.res)));
  if (!table || Array.from(table).some(Number.isNaN)) return { covered: false, years: YEARS };
  const depths = stormDepths(table, hours);
  return { covered: true, years: YEARS, depths, returnPeriod: mm > 0 ? returnPeriod(mm, hours, table) : null };
}

// ------------------------------------------------------------------ the storm on packs
async function stormRun(region, stormIn) {
  const storm = normaliseStorm(stormIn);
  const v = await getPack(region);
  if (!v) return null;
  const key = stormKey(storm);
  if (v.runs.has(key)) { const r = v.runs.get(key); v.runs.delete(key); v.runs.set(key, r); return r; }
  const rarity = storm.kind === "rarity" ? await rarityOf(region) : null;
  const rain = stormRain(v.pack, storm, rarity);
  const t0 = performance.now();
  const run = runStorm(v.pack, rain, storm.condition);
  run.rain = rain;
  run.ms = performance.now() - t0;
  v.runs.set(key, run);
  while (v.runs.size > MAX_RUNS) v.runs.delete(v.runs.keys().next().value);
  return run;
}

// Water in every hexagon that pack() draws for this region and level, in the same order:
// values[k * steps + s] (m of water at level 10, share under water at 8 and 9; NaN unknown)
async function stormLevel(region, level, storm) {
  const run = await stormRun(region, storm);
  if (!run) return { region, level, empty: true };
  const v = packs.get(region) || await getPack(region);
  const values = levelWater(v.pack, levelCells(v, level), run, level, cellAreas(v, level));
  return { region, level, steps: STEPS, values, ms: run.ms };
}

// The storm over the depressions whose lowest point is inside box [w, s, e, n], in these regions
async function stormSummary(regions, box, stormIn) {
  const storm = normaliseStorm(stormIn);
  const [w, s, e, n] = box;
  const total = { depressions: 0, unknown: 0, fill: 0, overflow: 0, holdWater: 0, wetAreaM2: 0, heldM3: 0,
    first: [], range: {}, regions: 0, ms: 0 };
  const firsts = [];
  for (const region of regions) {
    const run = await stormRun(region, storm);
    if (!run) continue;
    const v = await getPack(region), ll = lowLatLng(v);
    const pick = (i) => ll[2 * i] >= s && ll[2 * i] <= n && ll[2 * i + 1] >= w && ll[2 * i + 1] <= e;
    const sm = summarise(v.pack, run, pick);
    for (const k of ["depressions", "unknown", "fill", "overflow", "holdWater", "wetAreaM2", "heldM3"]) total[k] += sm[k];
    for (const f of sm.first) firsts.push({ ...f, region, id: v.pack.id[f.i].toString(), lngLat: [ll[2 * f.i + 1], ll[2 * f.i]] });
    for (const cond of ["dry", "wet"]) {
      if (cond === storm.condition) continue;
      const rc = rangeCounts(v.pack, run.rain, cond, pick);
      const t = total.range[cond] || (total.range[cond] = { fill: 0, overflow: 0 });
      t.fill += rc.fill; t.overflow += rc.overflow;
    }
    total.regions++;
    total.ms += run.ms;
  }
  total.range[storm.condition] = { fill: total.fill, overflow: total.overflow };
  firsts.sort((a, b) => a.step - b.step || b.ratio - a.ratio || (a.id < b.id ? -1 : 1));
  total.first = firsts.slice(0, 5);
  total.steps = STEPS;
  return total;
}

// One depression through the storm: how full at each step, how deep, when it overflows
async function stormDepression(region, i, storm) {
  const run = await stormRun(region, storm);
  if (!run) throw new Error("no data for this area");
  if (!(i >= 0 && i < run.n)) throw new Error("no such depression");
  const out = depressionSteps(run, i);
  out.rainMm = typeof run.rain === "number" ? run.rain : run.rain[i];
  return out;
}

// ------------------------------------------------------------------ wells (3D view, level 10)
// Geometry of the wells for the level-10 hexagons of a region whose centre lies in box [w, s, e, n]:
// all positions relative to the box centre in Mercator units (precise in float32), heights in
// Mercator units per metre BEFORE exaggeration (the layer multiplies). Each hexagon's floor is its
// depth when full (deepest point) below the level at which its depression overflows; walls stand
// where neighbouring hexagons differ in depth (or border ground with no depression).
//   open   [x, y] triangles: the hexagon openings
//   pit    [x, y, z, colour] triangles: floors and walls (colour = depth class 0..6; walls add a
//          lighting fraction 0.05..0.95, floors have none)
//   water  [x, y, floorDepthM, zPerMetre, k] triangles, k = the hexagon's index in pack(region, 10)
async function wellMesh(region, box) {
  const v = await getPack(region);
  if (!v) return null;
  const L = levelCells(v, 10);
  if (!v.centres10) {
    const c = new Float64Array(2 * L.cell.length);
    L.cell.forEach((x, k) => { const [la, lo] = cellToLatLng(hex(x)); c[2 * k] = la; c[2 * k + 1] = lo; });
    v.centres10 = c;
  }
  const C = v.centres10, [w, s, e, n] = box;
  const ox = mercX((w + e) / 2), oy = mercY((s + n) / 2);
  const pick = [];
  for (let k = 0; k < L.cell.length; k++) {
    const la = C[2 * k], lo = C[2 * k + 1];
    if (la >= s && la <= n && lo >= w && lo <= e) pick.push(k);
  }
  const rings = pick.map((k) => cellToBoundary(hex(L.cell[k]), true).slice(0, -1));   // [lng, lat], open ring
  const key = (a) => `${a[0].toFixed(9)},${a[1].toFixed(9)}`;
  const edges = new Map();                     // edge -> [{t, a, b, depth}]
  rings.forEach((ring, t) => {
    for (let j = 0; j < ring.length; j++) {
      const a = ring[j], b = ring[(j + 1) % ring.length];
      const ka = key(a), kb = key(b), id = ka < kb ? ka + "|" + kb : kb + "|" + ka;
      let list = edges.get(id);
      if (!list) edges.set(id, (list = []));
      list.push({ t, a, b });
    }
  });
  const depthOf = (t) => L.depth[pick[t]];
  const P = (lng, lat) => [mercX(lng) - ox, mercY(lat) - oy, mercZPerMetre(lat)];
  const open = [], pit = [], water = [];
  rings.forEach((ring, t) => {
    const d = depthOf(t), k = pick[t], pts = ring.map(([lng, lat]) => P(lng, lat));
    const floorShade = depthClass(d);                         // floor colour: the depth class (sand .. brown)
    for (let j = 1; j + 1 < pts.length; j++) {
      for (const q of [pts[0], pts[j], pts[j + 1]]) {
        open.push(q[0], q[1]);
        pit.push(q[0], q[1], -d * q[2], floorShade);
        water.push(q[0], q[1], d, q[2], k);
      }
    }
  });
  for (const list of edges.values()) {
    let top, bottom, deeper;
    if (list.length === 1) { deeper = list[0]; top = 0; bottom = depthOf(deeper.t); }
    else {
      const [x, y] = list, dx = depthOf(x.t), dy = depthOf(y.t);
      if (dx === dy) continue;
      deeper = dx > dy ? x : y; top = Math.min(dx, dy); bottom = Math.max(dx, dy);
    }
    const a = P(...deeper.a), b = P(...deeper.b);
    // light from the north-west; the wall faces into the deeper hexagon
    const lat = (deeper.a[1] + deeper.b[1]) / 2;
    const ex = (deeper.b[0] - deeper.a[0]) * Math.cos((lat * Math.PI) / 180), ey = deeper.b[1] - deeper.a[1];
    const len = Math.hypot(ex, ey) || 1, nx = ey / len, ny = -ex / len;   // outward for a counter-clockwise ring
    const lit = -(nx * -0.6 + ny * 0.8);                                   // inner face towards the light
    const shade = depthClass(bottom) + 0.05 + 0.9 * Math.min(Math.max(0.5 + 0.5 * lit, 0), 1);   // class + light
    const quad = [[a, top], [b, top], [b, bottom], [a, top], [b, bottom], [a, bottom]];
    for (const [q, depth] of quad) pit.push(q[0], q[1], -depth * q[2], shade);
  }
  return { region, origin: [ox, oy], cells: pick.length, box,
    open: new Float32Array(open), pit: new Float32Array(pit), water: new Float32Array(water) };
}

// The depression nearest to a point that is worth seeing as a well (at least 1 m deep and
// 5,000 m2), in the point's res-4 area or the six around it: where "Wells" takes the visitor
async function nearestDepression(lat, lon) {
  const start = latLngToCell(lat, lon, 4);
  for (const ring of [[start], gridDisk(start, 1).filter((c) => c !== start)]) {
    let best = null, bd = Infinity;
    for (const r of ring) {
      let v;
      try { v = await getPack(r); } catch { continue; }
      if (!v) continue;
      const pk = v.pack, ll = lowLatLng(v), k = Math.cos((lat * Math.PI) / 180);
      for (let i = 0; i < pk.n; i++) {
        if (full(pk, i, pk.depths) < 1 || full(pk, i, pk.areas) < 5000) continue;
        const d = (ll[2 * i] - lat) ** 2 + ((ll[2 * i + 1] - lon) * k) ** 2;
        if (d < bd) { bd = d; best = { region: r, index: i, lngLat: [ll[2 * i + 1], ll[2 * i]] }; }
      }
    }
    if (best) return best;
  }
  return null;
}
