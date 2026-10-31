// The data worker: everything heavy happens here, so the map never stutters.
// It reads the national overview and the data packs, builds the hexagons to draw, and answers
// questions about single depressions. The page talks to it through src/data/client.js.

import { cellArea, cellToBoundary, cellToLatLng, gridDisk, latLngToCell, polygonToCells } from "h3-js";
import { Hosts } from "./data/hosts.js";
import { gunzip, parsePack } from "./pack.js";

const MAX_PACKS = 48;                    // decoded packs kept in memory (about 2-5 MB each)
let hosts, local, index = null, indexPromise = null;
const overview = {};                     // res -> decoded overview
const packs = new Map();                 // region -> {pack, derived}, in least-recently-used order
const pending = new Map();               // region -> promise (one request per pack at a time)

const handlers = { init, national, overviewCells, regionsIn, pack, depression, findDepression, regionOf, example };

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
const RES_SHIFT = 52n, RES_MASK = 0xfn << 52n;
function parentOf(cell, res) {
  const r = BigInt(res);
  const unused = (1n << (3n * (15n - r))) - 1n;
  return ((cell & ~RES_MASK) | (r << RES_SHIFT)) | unused;
}
const hex = (b) => b.toString(16);

const ring = (h) => cellToBoundary(h, true);       // GeoJSON order, already closed

// ------------------------------------------------------------------ national overview
async function loadOverview(res) {
  if (overview[res]) return overview[res];
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
  }
  return { type: "FeatureCollection", features };
}

// ------------------------------------------------------------------ packs
async function packIndex() {
  if (index) return index;
  if (!indexPromise) indexPromise = hosts.json("index.json").then((j) => (index = j));
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
    const v = { pack: pk, levels: {}, upstream: null };
    packs.set(region, v);
    while (packs.size > MAX_PACKS) packs.delete(packs.keys().next().value);
    return v;
  })();
  pending.set(region, p);
  try { return await p; } finally { pending.delete(region); }
}

const full = (pk, i, arr) => arr[i * 7 + pk.count[i] - 1];

// Cells to draw at one level: deepest point, depressed area, the depression with most area there
function levelCells(v, level) {
  if (v.levels[level]) return v.levels[level];
  const pk = v.pack, m = pk.cell.length;
  const keys = new Map();
  const out = { cell: [], depth: [], area: [], dep: [], depArea: [], n: [] };
  for (let r = 0; r < m; r++) {
    const c = level === 10 ? pk.cell[r] : parentOf(pk.cell[r], level);
    const d = pk.cell_dep[r];
    const a = pk.cell_share[r] * full(pk, d, pk.areas);
    let k = keys.get(c);
    if (k === undefined) {
      k = out.cell.length;
      keys.set(c, k);
      out.cell.push(c); out.depth.push(0); out.area.push(0); out.dep.push(d); out.depArea.push(-1); out.n.push(new Set());
    }
    out.depth[k] = Math.max(out.depth[k], pk.cell_max_depth[r]);
    out.area[k] += a;
    out.n[k].add(d);
    if (a > out.depArea[k]) { out.depArea[k] = a; out.dep[k] = d; }
  }
  out.n = out.n.map((s) => s.size);
  v.levels[level] = out;
  return out;
}

async function pack(region, level) {
  const v = await getPack(region);
  if (!v) return { region, empty: true };
  const L = levelCells(v, level);
  const features = new Array(L.cell.length);
  for (let k = 0; k < L.cell.length; k++) {
    const h = hex(L.cell[k]);
    features[k] = {
      type: "Feature", id: k,
      geometry: { type: "Polygon", coordinates: [ring(h)] },
      // levels 8 and 9 are shaded by the share of the hexagon in depressions (s), level 10 by depth (d)
      properties: level === 10
        ? { h, d: Math.round(L.depth[k] * 100) / 100, a: Math.round(L.area[k]), dep: L.dep[k], n: L.n[k] }
        : { h, s: L.area[k] / cellArea(h, "m2"), d: Math.round(L.depth[k] * 100) / 100, a: Math.round(L.area[k]), dep: L.dep[k], n: L.n[k] },
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
  const chain = [lngLat(pk.low[i])], chainIdx = [i];
  const seen = new Set([i]);
  let j = pk.target[i], leaves = j < 0;
  while (j >= 0 && !seen.has(j) && chain.length < 200) {
    chain.push(lngLat(pk.low[j]));
    chainIdx.push(j);
    seen.add(j);
    if (pk.target[j] < 0) { leaves = true; break; }
    j = pk.target[j];
  }
  const up = upstreamOf(v).get(i) || [];
  let receivesOutside = false;
  for (let k = 0; k < pk.ext_dep.length; k++) if (pk.ext_dep[k] === i) { receivesOutside = true; break; }
  const [lat, lon] = cellToLatLng(hex(pk.low[i]));
  const t = pk.target[i];
  return {
    region, index: i, id: pk.id[i].toString(), low: hex(pk.low[i]), lat, lon,
    depth: full(pk, i, pk.depths), area: full(pk, i, pk.areas), volume: full(pk, i, pk.vols),
    catchment: pk.catch[i], curveNumber: pk.cn[i], rainToFill: pk.rain_to_fill[i],
    fillsInYears: pk.fills_in_years[i], drained: (pk.flags[i] & 1) === 1, landlocked: (pk.flags[i] & 2) === 2,
    target: t, targetLowLngLat: t >= 0 ? lngLat(pk.low[t]) : null,
    chainSteps: chain.length - 1, chainLeaves: leaves, chainIdx, cellCount: cells.length,
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
