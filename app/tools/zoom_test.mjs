// Zooming through the levels in Columns (tilted) and Flat, in headless Chrome with local data:
// at every stop the right level is drawn, the overview's (kilometre-tall) columns only show at
// overview levels, and how long the level takes to appear. Owner report 2026-11-02 (Belpre, OH).
//   node app/tools/zoom_test.mjs DATA_DIR OUT_DIR
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [dataDir, outArg] = process.argv.slice(2);
const out = resolve(outArg);
mkdirSync(out, { recursive: true });
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe"].find(existsSync);
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8772"], { stdio: "ignore" });
const data = spawn(process.execPath, [join(here, "serve.mjs"), resolve(dataDir), "8773"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const base = `http://127.0.0.1:8772/explorer/?packs=${encodeURIComponent("http://127.0.0.1:8773/{file}")}`;
const R = { errors: [], runs: {} };
const ZOOMS = [6, 8.5, 10.8, 12.2, 13.6, 15.5, 13, 11, 9.5, 7, 12.8, 15];

try {
  const page = await browser.newPage();
  page.on("pageerror", (e) => R.errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") R.errors.push(`console: ${m.text()}`); });
  await page.setViewport({ width: 1360, height: 860 });
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank */ } });
  for (const view of ["columns", "flat"]) {
    await page.goto("about:blank");
    await page.goto(`${base}#map=13/39.2731/-81.5682${view === "columns" ? "/0/55&v=columns" : ""}`, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.loading === 0 && window.__lowpoints.state.res != null, { timeout: 120000, polling: 200 });
    const stops = [];
    for (const [k, z] of ZOOMS.entries()) {
      const t = await page.evaluate(async (zoom) => {
        const L = window.__lowpoints, m = L.map, t0 = performance.now();
        m.easeTo({ zoom, duration: 500 });
        await new Promise((r) => m.once("moveend", r));
        const tMove = performance.now();
        // settled: the level for this zoom is drawn (every pack in view at it) and nothing is loading
        await new Promise((res) => {
          const go = () => {
            const want = L.state.res, auto = want;
            const ok = L.state.loading === 0 && L.state.res === auto && performance.now() - tMove > 150;
            if (ok || performance.now() - t0 > 30000) res(); else setTimeout(go, 30);
          };
          go();
        });
        const vis = (id) => !!m.getLayer(id) && m.getLayoutProperty(id, "visibility") !== "none";
        const res = L.state.res, detail = res >= 8;
        const packCols = [...L.state.regions.keys()].filter((r) => vis(`pk-${r}-col`)).length;
        const packAt = [...L.state.regions.entries()].filter(([r, lv]) => vis(`pk-${r}-fill`) && lv !== res).length;
        return { zoom, res, settledMs: Math.round(performance.now() - tMove), overviewCol: vis("overview-col"), overviewFill: vis("overview-fill"),
          packCols, staleShown: packAt, regions: L.state.regions.size, view: L.views.effective };
      }, z);
      t.ok = view === "columns"
        ? (t.res >= 8 ? !t.overviewCol : !t.packCols)
        : !t.overviewCol && !t.packCols;
      stops.push(t);
      if (k % 3 === 2) await page.screenshot({ path: join(out, `${view}_z${String(z).replace(".", "_")}.png`) });
    }
    R.runs[view] = stops;
  }
  // a pinned level 10 at city zoom, in Columns
  await page.evaluate(() => { const L = window.__lowpoints; L.views.set("columns"); L.map.jumpTo({ zoom: 11.2 }); });
  await page.waitForFunction(() => window.__lowpoints.state.loading === 0, { timeout: 60000, polling: 200 });
  await page.click('[data-level="10"]');
  await page.waitForFunction(() => window.__lowpoints.state.res === 10 && window.__lowpoints.state.loading === 0, { timeout: 120000, polling: 200 });
  await new Promise((r) => setTimeout(r, 1500));
  R.pinned10 = await page.evaluate(() => { const m = window.__lowpoints.map; return { res: window.__lowpoints.state.res, overviewCol: m.getLayoutProperty("overview-col", "visibility") !== "none" }; });
  R.pinned10.ok = R.pinned10.res === 10 && !R.pinned10.overviewCol;
  await page.screenshot({ path: join(out, "pinned10_columns.png") });
  // each 3D view keeps its own depth factor: x1 in Columns leaves Wells at x10
  R.factors = await page.evaluate(() => {
    const v = window.__lowpoints.views, seen = [];
    v.set("columns"); v.setFactor(1); seen.push(["columns", v.exaggeration]);
    v.set("wells"); seen.push(["wells", v.exaggeration]);
    v.set("columns"); seen.push(["columns", v.exaggeration]);
    v.set("flat");
    return seen;
  });
  R.factors.ok = JSON.stringify(R.factors) === JSON.stringify([["columns", 1], ["wells", 10], ["columns", 1]]);
} catch (e) {
  R.errors.push(`test: ${e.message}`);
} finally {
  await browser.close();
  app.kill(); data.kill();
}
const all = Object.values(R.runs).flat();
R.pass = all.length > 0 && all.every((s) => s.ok) && R.pinned10?.ok && R.factors?.ok && R.errors.length === 0;
writeFileSync(join(out, "report.json"), JSON.stringify(R, null, 1));
console.log(JSON.stringify({ pass: R.pass, errors: R.errors.slice(0, 4), pinned10: R.pinned10, factors: R.factors,
  stops: Object.fromEntries(Object.entries(R.runs).map(([k, v]) => [k, v.map((s) => `z${s.zoom}->L${s.res} ${s.settledMs}ms ${s.ok ? "ok" : "BAD"}${s.overviewCol ? " ovCol" : ""}${s.staleShown ? ` stale${s.staleShown}` : ""}`)])) }, null, 1));
