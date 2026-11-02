// Spec 6.3 engine checks in headless Chrome, through the app's own data worker and the live hosts.
//   node app/tools/check_engine_browser.mjs CASES_JSON OUT_JSON
// CASES_JSON from app/tools/engine_cases.py. Checks: overflow paths across areas equal the dataset's
// spill_to chain; view summaries equal the library; bad storms are refused; Atlas 14 gaps are
// unknown, not zero; timings inside the worker (software graphics: not real-device numbers).
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [casesFile, outFile] = process.argv.slice(2);
const cases = JSON.parse(readFileSync(casesFile, "utf8"));
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const port = 8768, base = `http://127.0.0.1:${port}/explorer/`;
const server = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), String(port)], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 700));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const report = { errors: [] };
const near = (a, b, tol) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));

try {
  const page = await browser.newPage();
  page.on("pageerror", (e) => report.errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") report.errors.push(`console: ${m.text()}`); });
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(`${base}#map=12.5/29.76/-95.37`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.regions.size > 0, { timeout: 120000 });
  const call = (type, ...args) => page.evaluate((t, a) => window.__lowpoints.client.call(t, ...a)
    .then((r) => ({ ok: true, r: JSON.parse(JSON.stringify(r, (k, v) => (ArrayBuffer.isView(v) ? Array.from(v) : v))) }),
      (e) => ({ ok: false, e: String(e.message || e) })), type, args);

  // 1. overflow paths
  report.paths = { checked: 0, equal: 0, crossArea: 0, cut: 0, mismatches: [] };
  for (const c of cases.paths) {
    const f = await call("findDepression", c.region, c.id);
    const d = f.ok && f.r >= 0 ? await call("depression", c.region, f.r) : f;
    report.paths.checked++;
    if (!d.ok) { report.paths.mismatches.push({ id: c.id, error: d.e || "not found" }); continue; }
    const got = d.r.chainIds, same = got.length === c.ids.length && got.every((x, k) => x === c.ids[k]);
    const ends = d.r.chainLeaves === c.leaves && d.r.chainCut === c.cut;
    if (same && ends) report.paths.equal++;
    else report.paths.mismatches.push({ id: c.id, want: c.ids.length, got: got.length, firstDiff: got.findIndex((x, k) => x !== c.ids[k]),
      leaves: [d.r.chainLeaves, c.leaves], cut: [d.r.chainCut, c.cut] });
    if (new Set(d.r.chainRegions).size > 1) report.paths.crossArea++;
    if (c.cut) report.paths.cut++;
  }

  // 2. summaries of a whole pack against the library
  report.summaries = [];
  for (const s of cases.summaries) {
    const r = await call("stormSummary", [s.region], [-180, -90, 180, 90], s.storm);
    if (!r.ok) { report.summaries.push({ name: s.name, error: r.e }); continue; }
    const g = r.r;
    const checks = {
      depressions: g.depressions === s.depressions, unknown: g.unknown === s.unknown, fill: g.fill === s.fill,
      overflow: g.overflow === s.overflow, holdWater: g.holdWater === s.holdWater,
      heldM3: near(g.heldM3, s.heldM3, 1e-6), wetAreaM2: near(g.wetAreaM2, s.wetAreaM2, 1e-6),
      range: ["dry", "wet", s.storm.condition].every((c) => g.range[c].fill === s.range[c].fill && g.range[c].overflow === s.range[c].overflow),
    };
    report.summaries.push({ name: s.name, storm: `${s.storm.kind} ${s.storm.mm ?? s.storm.years} ${s.storm.hours}h ${s.storm.condition}`,
      ok: Object.values(checks).every(Boolean), checks, app: { fill: g.fill, overflow: g.overflow, heldM3: g.heldM3, range: g.range } });
  }

  // 3. storms that must be refused
  const bad = [{ kind: "amount", mm: 0, hours: 6 }, { kind: "amount", mm: -5, hours: 6 }, { kind: "amount", mm: 5000, hours: 6 },
    { kind: "amount", mm: "abc", hours: 6 }, { kind: "amount", mm: 50, hours: 0 }, { kind: "amount", mm: 50, hours: 72 },
    { kind: "rarity", years: 3, hours: 24 }, { kind: "rarity", years: 100, hours: 24, condition: "soggy" }, { kind: "flood" }, null];
  report.refused = [];
  for (const b of bad) {
    const r = await call("stormSummary", ["84446cbffffffff"], [-180, -90, 180, 90], b);
    report.refused.push({ storm: b, refused: !r.ok, message: r.e || null });
  }

  // 4. where Atlas 14 has no data (Seattle): rarity storms unknown, amount storms computed
  const sea = await call("regionOf", 47.61, -122.33);
  const seaR = await call("stormSummary", [sea.r], [-180, -90, 180, 90], { kind: "rarity", years: 100, hours: 24 });
  const seaA = await call("stormSummary", [sea.r], [-180, -90, 180, 90], { kind: "amount", mm: 50, hours: 24 });
  const seaAt = await call("rarityAt", 47.61, -122.33, 24, 50);
  const houAt = await call("rarityAt", 29.76, -95.37, 6, 127);
  report.atlasGap = { region: sea.r, rarityStorm: seaR.ok && { depressions: seaR.r.depressions, unknown: seaR.r.unknown },
    amountStorm: seaA.ok && { depressions: seaA.r.depressions, unknown: seaA.r.unknown, fill: seaA.r.fill },
    rarityAt: seaAt.r, houston127mm6h: houAt.r,
    ok: seaR.ok && seaR.r.unknown === seaR.r.depressions && seaA.ok && seaA.r.unknown === 0 && seaAt.ok && seaAt.r.covered === false
      && houAt.ok && houAt.r.covered === true && houAt.r.returnPeriod > 1 };

  // 5. timings inside the worker: the views of a city and the widest detail view
  report.timing = [];
  for (const [label, hash] of [["Houston z12.5", "#map=12.5/29.76/-95.37"], ["Chicago z10.6 (widest detail view)", "#map=10.6/41.85/-87.75"]]) {
    await page.goto(`${base}${hash}`, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.regions.size > 0 && window.__lowpoints.state.loading === 0,
      { timeout: 120000, polling: 250 });
    await new Promise((r) => setTimeout(r, 1500));
    const t = await page.evaluate(async () => {
      const L = window.__lowpoints, regions = [...L.state.regions.keys()], b = L.map.getBounds();
      const box = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], level = L.state.res;
      const storm = { kind: "rarity", years: 10, hours: 24 };
      const time = async (f) => { const t0 = performance.now(); const r = await f(); return [performance.now() - t0, r]; };
      const [cold, sm] = await time(() => L.client.call("stormSummary", regions, box, storm));
      const [warm] = await time(() => L.client.call("stormSummary", regions, box, storm));
      let bytes = 0;
      const [levels] = await time(async () => { for (const r of regions) { const x = await L.client.call("stormLevel", r, level, storm); bytes += x.values ? x.values.byteLength : 0; } });
      const [other] = await time(() => L.client.call("stormSummary", regions, box, { kind: "amount", mm: 100, hours: 3, condition: "dry" }));
      return { regions: regions.length, level, depressionsInView: sm.depressions, engineMsInWorker: Math.round(sm.ms),
        summaryColdMs: Math.round(cold), summaryWarmMs: Math.round(warm), levelValuesMs: Math.round(levels),
        levelPayloadMB: +(bytes / 1e6).toFixed(2), newStormMs: Math.round(other) };
    });
    report.timing.push({ view: label, ...t });
  }
} finally {
  await browser.close();
  server.kill();
}
report.pass = report.errors.length === 0 && report.paths.equal === report.paths.checked
  && report.summaries.every((s) => s.ok) && report.refused.every((r) => r.refused) && report.atlasGap.ok;
writeFileSync(outFile, JSON.stringify(report, null, 1));
console.log(JSON.stringify({ pass: report.pass, errors: report.errors.length, paths: { ...report.paths, mismatches: report.paths.mismatches.length },
  summaries: report.summaries.map((s) => [s.name, s.storm, s.ok]), refused: report.refused.filter((r) => r.refused).length + "/" + report.refused.length,
  atlasGap: report.atlasGap.ok, timing: report.timing }, null, 1));
