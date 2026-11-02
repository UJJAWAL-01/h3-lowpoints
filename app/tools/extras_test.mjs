// 6.3d2 checks 3 and 4 in headless Chrome, with the data served locally (packs, rarity, extras).
//   node app/tools/extras_test.mjs CASES_JSON DATA_DIR OUT_DIR
// DATA_DIR holds index.json, packs.bin, rarity*.json/bin, extras_index.json, extras.bin.
// check3: rim, ground, road class and distance in the app = the dataset (30 depressions).
// check4: "Hide likely drained" leaves out exactly the drained and road-feature depressions.
// DATA_DIR may be a live URL template (https://.../{file}); 4th arg: gallery (only) or checks (no gallery).
// gallery: a screenshot of each gallery depression, selected, at street scale (for the owner).
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [casesFile, dataDir, outArg, only] = process.argv.slice(2);
const outDir = resolve(outArg);
mkdirSync(join(outDir, "gallery"), { recursive: true });
const cases = JSON.parse(readFileSync(casesFile, "utf8"));
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8772"], { stdio: "ignore" });
const live = /^https?:/.test(dataDir);                  // a live host URL template ({file}) instead of a local folder
const data = live ? null : spawn(process.execPath, [join(here, "serve.mjs"), resolve(dataDir), "8773"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const base = `http://127.0.0.1:8772/explorer/?packs=${encodeURIComponent(live ? dataDir : "http://127.0.0.1:8773/{file}")}`;
const report = { errors: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
  const page = await browser.newPage();
  page.on("pageerror", (e) => report.errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") report.errors.push(`console: ${m.text()}`); });
  await page.setViewport({ width: 1280, height: 860 });
  await page.evaluateOnNewDocument(() => localStorage.setItem("lowpoints.guided", "1"));
  await page.goto(`${base}#map=14.5/29.7604/-95.3698`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.regions.size > 0 && window.__lowpoints.state.loading === 0,
    { timeout: 120000, polling: 300 });
  const call = (type, ...args) => page.evaluate((t, a) => window.__lowpoints.client.call(t, ...a)
    .then((r) => ({ ok: true, r }), (e) => ({ ok: false, e: String(e.message || e) })), type, args);

  if (only !== "gallery") {
  // check 3
  const c3 = { checked: 0, equal: 0, mismatches: [] };
  for (const c of cases.check3) {
    const f = await call("findDepression", c.region, c.id);
    const d = f.ok && f.r >= 0 ? await call("depression", c.region, f.r) : { ok: false, e: "not found" };
    c3.checked++;
    if (!d.ok) { c3.mismatches.push({ id: c.id, error: d.e }); continue; }
    const g = d.r;
    const ok = g.extras && Math.abs(g.rimM - c.rim) < 1e-3 && Math.abs(g.groundM - c.ground) < 0.01 && g.road === c.road
      && (c.roadM == null ? !Number.isFinite(g.roadM) : Math.abs(g.roadM - c.roadM) < 0.01);
    if (ok) c3.equal++; else c3.mismatches.push({ id: c.id, want: c, got: { rim: g.rimM, ground: g.groundM, road: g.road, roadM: g.roadM } });
  }
  report.check3 = c3;

  // check 4: the filter, on the packs in this Houston view, at levels 9 and 10
  const regions = await page.evaluate(() => [...window.__lowpoints.state.regions.keys()]);
  const c4 = { regions: regions.length, levels: {}, keptButHidden: 0, sampled: 0 };
  for (const level of [9, 10]) {
    let all = 0, kept = 0;
    for (const r of regions) {
      const a = await call("pack", r, level, "all"), u = await call("pack", r, level, "undrained");
      if (!a.ok || !u.ok || a.r.empty) continue;
      all += a.r.fc.features.length; kept += u.r.fc.features.length;
      // every depression still drawn must be neither drained nor a road feature
      const deps = [...new Set(u.r.fc.features.map((ft) => ft.properties.dep))].slice(0, 60);
      for (const i of deps) {
        const d = await call("depression", r, i);
        c4.sampled++;
        if (!d.ok || d.r.drained || d.r.road > 0) c4.keptButHidden++;
      }
    }
    c4.levels[level] = { hexagonsAll: all, hexagonsUndrained: kept };
  }
  report.check4 = c4;

  // the filter through the page: button, link, panel
  await page.click('[data-act="show-undrained"]');
  await page.waitForFunction(() => window.__lowpoints.state.loading === 0 && window.__lowpoints.state.regions.size > 0, { timeout: 120000, polling: 300 });
  await sleep(1500);
  report.filterUi = await page.evaluate(() => ({ show: window.__lowpoints.state.show, link: new URLSearchParams(location.hash.slice(1)).get("show"),
    pressed: document.querySelector('[data-act="show-undrained"]')?.getAttribute("aria-pressed") }));
  await page.screenshot({ path: join(outDir, "filter_undrained.png") });
  await page.click('[data-act="show-all"]'); await sleep(2500);
  await page.screenshot({ path: join(outDir, "filter_all.png") });

  writeFileSync(join(outDir, "report.json"), JSON.stringify(report, null, 1));      // the checks, before the slow gallery
  }
  // gallery
  report.gallery = [];
  // jump to the place, let the map draw, then select (a hash-only goto does not reload the page,
  // so a d= link is never read; a fly is still moving when the picture is taken)
  const idle = () => page.evaluate(() => new Promise((res) => { const m = window.__lowpoints.map;
    const t = setTimeout(res, 20000); m.once("idle", () => { clearTimeout(t); res(); }); m.triggerRepaint(); }));
  for (const [k, g] of (only === "checks" ? [] : cases.gallery).entries()) {
    try {
      const f = await call("findDepression", g.region, g.id);
      if (!f.ok || f.r < 0) throw new Error(f.e || "not found");
      await page.evaluate((lon, lat) => window.__lowpoints.map.jumpTo({ center: [lon, lat], zoom: 16.5, pitch: 0, bearing: 0 }), g.lon, g.lat);
      await sleep(500);
      await page.waitForFunction(() => window.__lowpoints.state.loading === 0, { timeout: 60000, polling: 300 });
      await page.evaluate((r, i) => { window.__lowpoints.selectDepression(r, i); }, g.region, f.r);
      await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 60000, polling: 300 });
      await idle(); await sleep(800);
      const file = `class${g.road}_${String(k).padStart(2, "0")}_${g.id}.png`;
      await page.screenshot({ path: join(outDir, "gallery", file) });   // whole window: a clip resizes the page and clears the map canvas
      report.gallery.push({ ...g, file });
    } catch (e) { report.gallery.push({ ...g, error: String(e.message) }); }
  }
} finally {
  await browser.close();
  app.kill(); data?.kill();
}
if (only === "gallery") {
  writeFileSync(join(outDir, "gallery.json"), JSON.stringify(report, null, 1));
  console.log(JSON.stringify({ errors: report.errors, gallery: report.gallery.filter((g) => !g.error).length + "/" + report.gallery.length,
    failed: report.gallery.filter((g) => g.error) }, null, 1));
} else {
  report.pass = report.errors.length === 0 && report.check3.equal === report.check3.checked && report.check4.keptButHidden === 0
    && report.filterUi.show === "undrained" && report.filterUi.link === "undrained";
  writeFileSync(join(outDir, "report.json"), JSON.stringify(report, null, 1));
  console.log(JSON.stringify({ pass: report.pass, errors: report.errors, check3: { ...report.check3, mismatches: report.check3.mismatches.slice(0, 3) },
    check4: report.check4, filterUi: report.filterUi, gallery: report.gallery.filter((g) => !g.error).length + "/" + report.gallery.length }, null, 1));
}
