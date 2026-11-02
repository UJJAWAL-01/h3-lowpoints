// Grouping a pack's res-10 cells into the hexagons drawn at one level (8, 9 or 10). Pure: used by
// the data worker and by the Node checks, so both see exactly the same hexagons in the same order.

const RES_SHIFT = 52n, RES_MASK = 0xfn << 52n;

export function parentOf(cell, res) {
  const r = BigInt(res);
  const unused = (1n << (3n * (15n - r))) - 1n;
  return ((cell & ~RES_MASK) | (r << RES_SHIFT)) | unused;
}

export const full = (pk, i, arr) => arr[i * 7 + pk.count[i] - 1];

// Per hexagon: deepest point when full, depressed area, the depression with most area there, the
// number of depressions; per pack row (res-10 cell x depression): the hexagon it belongs to (rowK)
export function levelCells(pk, level) {
  const m = pk.cell.length;
  const keys = new Map();
  const out = { cell: [], depth: [], area: [], dep: [], depArea: [], n: [], rowK: new Int32Array(m) };
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
    out.rowK[r] = k;
    out.depth[k] = Math.max(out.depth[k], pk.cell_max_depth[r]);
    out.area[k] += a;
    out.n[k].add(d);
    if (a > out.depArea[k]) { out.depArea[k] = a; out.dep[k] = d; }
  }
  out.n = out.n.map((s) => s.size);
  return out;
}
