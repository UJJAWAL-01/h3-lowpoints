// How rare a storm is: NOAA Atlas 14, from the per-cell tables written by app/tools/rarity.py.
// The same rule as h3lowpoints.sources.rainfall.Atlas14Storms.return_period: depth log-log
// between durations, then log(years) linear in depth between return periods; 0 below the 1-year
// storm, Infinity above the 1000-year storm, NaN where Atlas 14 has no data.

export const HOURS = [1, 2, 3, 6, 12, 24, 48];
export const YEARS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000];

// One region's block: uint64 cells at the index's resolution (sorted), then float32 depths [cells][7][10].
export function parseRarity(bytes, nCells) {
  const buf = bytes.byteOffset % 8 === 0 ? bytes : bytes.slice();
  const cells = new BigUint64Array(buf.buffer, buf.byteOffset, nCells);
  const depths = new Float32Array(buf.buffer, buf.byteOffset + 8 * nCells, nCells * 70);
  return { cells, depths };
}

export function depthsAt(block, cell) {
  const key = BigInt.asUintN(64, BigInt(cell));
  let lo = 0, hi = block.cells.length - 1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1, v = block.cells[m];
    if (v === key) return block.depths.subarray(m * 70, m * 70 + 70);
    if (v < key) lo = m + 1; else hi = m - 1;
  }
  return null;
}

// depths of the `hours`-long storm at each return period, from one cell's [7][10] table
export function stormDepths(table, hours) {
  if (!(hours >= HOURS[0] && hours <= HOURS[HOURS.length - 1])) throw new Error("hours must be 1 to 48");
  let hi = 0;
  while (HOURS[hi] < hours) hi++;
  const row = (k) => table.subarray(k * 10, k * 10 + 10);
  if (HOURS[hi] === hours) return Array.from(row(hi));
  const w = (Math.log(hours) - Math.log(HOURS[hi - 1])) / (Math.log(HOURS[hi]) - Math.log(HOURS[hi - 1]));
  const a = row(hi - 1), b = row(hi);
  return YEARS.map((_, i) => Math.exp((1 - w) * Math.log(a[i]) + w * Math.log(b[i])));
}

export function returnPeriod(depthMm, hours, table) {
  const row = stormDepths(table, hours);
  if (Number.isNaN(depthMm) || row.some(Number.isNaN)) return NaN;
  if (depthMm < row[0]) return 0;
  if (depthMm > row[row.length - 1]) return Infinity;
  let i = 0;
  while (row[i + 1] < depthMm) i++;
  if (row[i] === depthMm) return YEARS[i];
  const t = (depthMm - row[i]) / (row[i + 1] - row[i]);
  return Math.exp(Math.log(YEARS[i]) + t * (Math.log(YEARS[i + 1]) - Math.log(YEARS[i])));
}

export function stormDepth(years, hours, table) {
  const i = YEARS.indexOf(years);
  if (i < 0) throw new Error(`years must be one of ${YEARS}`);
  return stormDepths(table, hours)[i];
}
