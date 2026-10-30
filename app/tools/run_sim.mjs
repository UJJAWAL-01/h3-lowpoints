// node run_sim.mjs PACK_FILE DEPTH_MM CONDITION OUT_FILE
// Runs app/src/sim.js on one gzipped pack; writes float64: n, m, inflow, store, depth, area, cellWet.
import { readFileSync, writeFileSync } from "node:fs";
import { gunzip, parsePack } from "../src/pack.js";
import { simulate } from "../src/sim.js";

const [file, depth, condition, out] = process.argv.slice(2);
const pack = parsePack(await gunzip(readFileSync(file)));
const r = simulate(pack, Number(depth), condition);
const parts = [Float64Array.of(pack.n, pack.cell.length), r.inflow, r.store, r.depth, r.area, r.cellWet];
const total = parts.reduce((a, p) => a + p.length, 0);
const all = new Float64Array(total);
let at = 0;
for (const p of parts) { all.set(p, at); at += p.length; }
writeFileSync(out, Buffer.from(all.buffer));
