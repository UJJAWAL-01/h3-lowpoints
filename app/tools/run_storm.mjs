// node run_storm.mjs PACK_FILE RARITY_FILE|- RARITY_CELLS RARITY_RES STORM_JSON OUT_FILE
// Runs app/src/storm.js (16 steps) on one gzipped pack and its gzipped rarity block; writes float64:
// [n, K8, K9, K10], rain(n), store(16n), depth(16n), area(16n), fillStep(n), spillStep(n),
// water at levels 8, 9, 10 (K * 16 each, in the order levels.levelCells draws them), ms.
import { readFileSync, writeFileSync } from "node:fs";
import { cellArea } from "h3-js";
import { levelCells } from "../src/levels.js";
import { gunzip, parsePack } from "../src/pack.js";
import { parseRarity } from "../src/rarity.js";
import { levelWater, normaliseStorm, runStorm, stormRain } from "../src/storm.js";

const [file, rarityFile, rarityCells, rarityRes, stormJson, out] = process.argv.slice(2);
const pack = parsePack(await gunzip(readFileSync(file)));
const storm = normaliseStorm(JSON.parse(stormJson));
const rarity = rarityFile === "-" ? null
  : { block: parseRarity(await gunzip(readFileSync(rarityFile)), Number(rarityCells)), res: Number(rarityRes) };
const rain = stormRain(pack, storm, rarity);
const t0 = performance.now();
const run = runStorm(pack, rain, storm.condition);
const ms = performance.now() - t0;
const levels = [8, 9, 10].map((level) => {
  const L = levelCells(pack, level);
  const areas = L.cell.map((c) => cellArea(c.toString(16), "m2"));
  return levelWater(pack, L, run, level, areas);
});
const n = pack.n;
const rainArr = typeof rain === "number" ? new Float64Array(n).fill(rain) : rain;
const parts = [Float64Array.of(n, ...levels.map((l) => l.length / 16)), rainArr, run.store, run.depth, run.area,
  run.fillStep, run.spillStep, ...levels, Float64Array.of(ms)];
const total = parts.reduce((a, p) => a + p.length, 0);
const all = new Float64Array(total);
let at = 0;
for (const p of parts) { all.set(p, at); at += p.length; }
writeFileSync(out, Buffer.from(all.buffer));
