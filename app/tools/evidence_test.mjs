// 7.7c checks in headless Chrome, with the data served locally (packs, extras, evidence).
//   node app/tools/evidence_test.mjs CASES_JSON DATA_DIR OUT_DIR
// DATA_DIR holds index.json, packs.bin, extras*, evidence_index.json, evidence.bin (or a live URL
// template https://.../{file}).
// values   every evidence value the app gives for 70 depressions = dataset 0.4
// filter   "Grade A or B only" leaves exactly the A and B depressions drawn (packs in a Houston view)
// overview the national overview's evidence counts = the dataset totals
// page     the colour switch, its link and legend; the panel text with flood days; no errors
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [casesFile, dataDir, outArg] = process.argv.slice(2);
const outDir = resolve(outArg);
mkdirSync(outDir, { recursive: true });
const { cases, totals } = JSON.parse(readFileSync(casesFile, "utf8"));
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8772"], { stdio: "ignore" });
const live = /^https?:/.test(dataDir);
const data = live ? null : spawn(process.execPath, [join(here, "serve.mjs"), resolve(dataDir), "8773"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const base = `http://127.0.0.1:8772/explorer/?packs=${encodeURIComponent(live ? dataDir : "http://127.0.0.1:8773/{file}")}`;
const report = { errors: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const GRADES = ["", "A", "B", "C", "D"], CLASSES = ["", "too_small", "too_few_views", "never_seen_wet", "some_years", "seasonal", "permanent"];

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

  // values
  const v = { checked: 0, equal: 0, mismatches: [] };
  for (const c of cases) {
    const f = await call("findDepression", c.region, c.id);
    const d = f.ok && f.r >= 0 ? await call("depression", c.region, f.r) : { ok: false, e: "not found" };
    v.checked++;
    if (!d.ok) { v.mismatches.push({ id: c.id, error: d.e }); continue; }
    const g = d.r, bad = [];
    if (!g.evidence) bad.push("no evidence file");
    else if (!c.evidence) { if (g.wclass || g.grade || g.radarObserved) bad.push("values where the dataset has none"); }
    else {
      const eq = (k, a, b) => { if (a !== b) bad.push(`${k}: app ${JSON.stringify(a)} dataset ${JSON.stringify(b)}`); };
      eq("water_class", CLASSES[g.wclass], c.water_class); eq("wet_years", g.wetYears, c.wet_years);
      eq("clear_years", g.clearYears, c.clear_years);
      eq("first", g.firstWet ?? -1, c.first); eq("last", g.lastWet ?? -1, c.last); eq("month", g.wettestMonth, c.month);
      eq("observed", g.radarObserved, Math.min(c.observed, 255)); eq("flooded", g.radarFlooded, Math.min(c.flooded, 255));
      eq("days", JSON.stringify(g.floodDays), JSON.stringify(c.days));
      eq("grade", GRADES[g.grade], c.grade); eq("base", "ABC"[g.baseGrade - 1], c.base);
      eq("road_dam", g.roadDam, c.road_dam); eq("bridge", g.bridge, c.bridge);
      if (Math.abs(g.breachedM - c.breached) > 0.0051) bad.push(`breached: app ${g.breachedM} dataset ${c.breached}`);
    }
    if (bad.length) v.mismatches.push({ id: c.id, group: c.group, bad }); else v.equal++;
  }
  report.values = v;

  // filter: in the Houston view, every depression drawn in mode "ab" is graded A or B
  const regions = await page.evaluate(() => [...window.__lowpoints.state.regions.keys()]);
  const fl = { regions: regions.length, levels: {}, keptNotAB: 0, sampled: 0 };
  for (const level of [9, 10]) {
    let all = 0, kept = 0;
    for (const r of regions) {
      const a = await call("pack", r, level, "all"), ab = await call("pack", r, level, "ab");
      if (!a.ok || !ab.ok || a.r.empty) continue;
      all += a.r.fc.features.length; kept += ab.r.fc.features.length;
      for (const i of [...new Set(ab.r.fc.features.map((ft) => ft.properties.dep))].slice(0, 60)) {
        const d = await call("depression", r, i);
        fl.sampled++;
        if (!d.ok || !(d.r.grade === 1 || d.r.grade === 2)) fl.keptNotAB++;
      }
    }
    fl.levels[level] = { hexagonsAll: all, hexagonsAB: kept };
  }
  report.filter = fl;

  // overview: sums at level 4 = dataset totals
  const o = await call("overviewCells", 4, null);
  const sum = (k) => o.r.features.reduce((s, ft) => s + (ft.properties[k] || 0), 0);
  report.overview = { has: sum("eh"), wet: sum("ewc"), flooded: sum("efc"), A: sum("eA"), B: sum("eB"), want: totals };
  report.overview.equal = report.overview.has === totals.has && report.overview.wet === totals.wet
    && report.overview.flooded === totals.flooded && report.overview.A === totals.A && report.overview.B === totals.B;

  // page: colour switch, link, legend; a flooded depression's panel text
  const page_ = {};
  for (const k of ["wet", "flooded", "grade", "depth"]) {
    await page.click(`[data-act="colour-${k}"]`); await sleep(1500);
    await page.waitForFunction(() => window.__lowpoints.state.loading === 0, { timeout: 60000, polling: 300 });
    page_[k] = await page.evaluate((k) => ({ colour: window.__lowpoints.state.colour,
      link: new URLSearchParams(location.hash.slice(1)).get("colour"),
      pressed: document.querySelector(`[data-act="colour-${k}"]`)?.getAttribute("aria-pressed"),
      legend: document.querySelector("#panel-body .legend .label")?.textContent || "" }), k);
    if (k !== "depth") await page.screenshot({ path: join(outDir, `colour_${k}.png`) });
  }
  const fc = cases.find((c) => c.group === "flooded");
  if (fc) {
  const f = await call("findDepression", fc.region, fc.id);
  await page.evaluate((lon, lat) => window.__lowpoints.map.jumpTo({ center: [lon, lat], zoom: 15, pitch: 0, bearing: 0 }), fc.lon, fc.lat);
  await sleep(500);
  await page.waitForFunction(() => window.__lowpoints.state.loading === 0, { timeout: 60000, polling: 300 });
  await page.evaluate((r, i) => window.__lowpoints.selectDepression(r, i), fc.region, f.r);
  await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 60000, polling: 300 });
  await sleep(800);
  page_.panel = await page.evaluate(() => document.querySelector("#panel-body .evidence")?.innerText || "");
  page_.panelHasYear = fc.days.every((d) => page_.panel.includes(d.slice(0, 4)));
  await page.screenshot({ path: join(outDir, "panel_flooded.png") });
  } else page_.panel = "(no flooded case in this run)";
  report.page = page_;
} finally {
  await browser.close();
  app.kill(); data?.kill();
}
const p = report.page || {};
report.pass = report.errors.length === 0 && report.values.equal === report.values.checked && report.filter.keptNotAB === 0
  && report.filter.sampled > 0 && report.overview.equal
  && ["wet", "flooded", "grade"].every((k) => p[k]?.colour === k && p[k]?.link === k && p[k]?.pressed === "true")
  && p.depth?.colour === "depth" && p.depth?.link === null && p.panel.length > 0;
writeFileSync(join(outDir, "report.json"), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ pass: report.pass, errors: report.errors.slice(0, 5), values: `${report.values.equal}/${report.values.checked}`,
  mismatches: report.values.mismatches.slice(0, 3), filter: report.filter, overview: report.overview, page: report.page }, null, 1));
