// Tours (spec 6.4e): both tours run end to end in headless Chrome on local data, with the checks
// fixed in plans/PHASE6_4e_SPEC.md.
//   node app/tools/tour_test.mjs DATA_DIR OUT_DIR [nyc|austin|controls]
// full run   every chapter reached in order; every caption free of NaN, undefined, Infinity and
//            negative numbers; chapter 4 caption = the panel's volume; chapter 6 counts = the panel's;
//            no page errors; a screenshot at each chapter
// controls   Next, Back, chapter dots, Pause (caption and chapter stop changing), Escape, Exit,
//            the #tour= link, touching the map pauses, reduced motion makes no flights, phone layout,
//            axe on the tour bar
// Software graphics: timings are not real-device numbers.
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [dataDir, outArg, only] = process.argv.slice(2);
const out = resolve(outArg);
mkdirSync(out, { recursive: true });
const axeSrc = readFileSync(join(here, "..", "node_modules", "axe-core", "axe.min.js"), "utf8");
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8793"], { stdio: "ignore" });
const data = spawn(process.execPath, [join(here, "serve.mjs"), resolve(dataDir), "8794"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"], protocolTimeout: 900000 });
const base = `http://127.0.0.1:8793/explorer/?packs=${encodeURIComponent("http://127.0.0.1:8794/{file}")}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const BAD = /NaN|undefined|Infinity|null|\[object|(^|[\s(])-\d/;
const report = {};

async function open(hash = "#map=4/38/-97", { phone = false, reduce = false } = {}) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
  await page.setViewport(phone ? { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : { width: 1440, height: 900 });
  if (reduce) await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank */ } });
  await page.goto(`${base}${hash}`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.map.loaded(), { timeout: 120000, polling: 300 });
  return { page, errors };
}
const snap = (page) => page.evaluate(() => ({
  running: window.__lowpoints.tour.running, chapter: window.__lowpoints.tour.chapter, paused: window.__lowpoints.tour.paused,
  title: document.getElementById("tour-title")?.textContent || "", text: document.getElementById("tour-text")?.textContent || "",
  panelH2: document.querySelector("#panel-body h2")?.textContent || "",
  fill: document.querySelector("[data-sv=fill]")?.textContent || null,
}));

async function fullRun(key) {
  const { page, errors } = await open();
  await page.evaluate((k) => window.__lowpoints.tour.start(k), key);
  const seen = [], captions = [], start = Date.now();
  let last = "", lastCh = -1;
  while (Date.now() - start < 12 * 60000) {
    const s = await snap(page);
    if (!s.running) break;
    if (s.chapter !== lastCh) {
      lastCh = s.chapter; seen.push(s.chapter);
      await sleep(1500);
      await page.screenshot({ path: join(out, `${key}_${String(s.chapter + 1).padStart(2, "0")}.png`) });
    }
    const cap = `${s.title} | ${s.text}`;
    if (cap !== last && s.text) { last = cap; captions.push({ chapter: s.chapter, cap, panelH2: s.panelH2, fill: s.fill, t: Date.now() - start }); }
    await sleep(250);
  }
  const ms = Date.now() - start;
  const bad = captions.filter((c) => BAD.test(c.cap));
  const c4 = captions.find((c) => c.chapter === 3 && /holds/.test(c.cap));
  const vol = c4 && /Holds\s+(.+?)\s+of water/.exec(c4.panelH2)?.[1];
  const c6 = captions.find((c) => c.chapter === 5 && /By the end/.test(c.cap));
  const r = {
    seconds: Math.round(ms / 1000), chapters: seen, allChapters: JSON.stringify(seen) === JSON.stringify([...Array(12).keys()]),
    captions: captions.length, badCaptions: bad, volumeMatchesPanel: !!(vol && c4.cap.includes(vol)),
    stormCountMatchesPanel: !!(c6 && c6.fill && c6.cap.includes(`${c6.fill} of the`)),
    roadCutNamed: captions.some((c) => c.chapter === 4 && /road cut or underpass/.test(c.cap)),
    overflowSentence: captions.some((c) => c.chapter === 3 && /^Where the water goes \| When full/.test(c.cap)), errors,
  };
  r.pass = r.allChapters && !bad.length && r.volumeMatchesPanel && r.stormCountMatchesPanel && r.roadCutNamed && r.overflowSentence && !errors.length;
  writeFileSync(join(out, `${key}_captions.json`), JSON.stringify(captions, null, 1));
  await page.close();
  return r;
}

async function controls() {
  const r = {};
  {
    const { page, errors } = await open();
    await page.evaluate(() => window.__lowpoints.tour.start("nyc"));
    await sleep(1500);
    await page.click('.tour [data-t="next"]'); await sleep(800);
    r.next = (await snap(page)).chapter === 1;
    await page.click('.tour [data-t="next"]'); await sleep(800);
    await page.click('.tour [data-t="back"]'); await sleep(800);
    r.back = (await snap(page)).chapter === 1;
    await page.click('.tour-dots li:nth-child(4) button'); await sleep(800);
    r.dots = (await snap(page)).chapter === 3;
    await page.click('.tour [data-t="pause"]'); await sleep(500);
    const a = await snap(page); await sleep(6000); const b = await snap(page);
    r.pause = a.paused && b.paused && a.chapter === b.chapter && a.text === b.text;
    await page.click('.tour [data-t="pause"]'); await sleep(500);
    r.resume = !(await snap(page)).paused;
    r.axe = await page.evaluate(async (src) => { eval(src); const x = await window.axe.run(document.querySelector(".tour")); return x.violations.map((v) => `${v.id}: ${v.nodes.length}`); }, axeSrc);
    // touching the map pauses
    const box = await page.$eval("#map canvas", (c) => { const b = c.getBoundingClientRect(); return [b.left + b.width * 0.7, b.top + b.height * 0.3]; });
    await page.mouse.click(box[0], box[1]); await sleep(500);
    r.touchPauses = (await snap(page)).paused;
    await page.keyboard.press("Escape"); await sleep(500);
    r.escape = !(await snap(page)).running && !(await page.$(".tour"));
    await page.evaluate(() => window.__lowpoints.tour.start("austin")); await sleep(1000);
    await page.click('.tour [data-t="exit"]'); await sleep(500);
    r.exit = !(await snap(page)).running;
    r.errors = errors;
    await page.close();
  }
  {
    const { page, errors } = await open("#map=4/38/-97&tour=austin");
    await sleep(3000);
    const s = await snap(page);
    r.link = s.running && /Austin tour/.test(await page.$eval("#tour-chapter", (e) => e.textContent));
    r.linkErrors = errors;
    await page.close();
  }
  {
    const { page, errors } = await open("#map=4/38/-97", { reduce: true });
    await page.evaluate(() => { const m = window.__lowpoints.map; window.__flights = 0; const fly = m.flyTo.bind(m); m.flyTo = (...a) => { window.__flights++; return fly(...a); }; });
    await page.evaluate(() => window.__lowpoints.tour.start("nyc"));
    await page.waitForFunction(() => window.__lowpoints.tour.chapter >= 3, { timeout: 180000, polling: 500 });
    r.reducedNoFlights = (await page.evaluate(() => window.__flights)) === 0;
    r.reducedErrors = errors;
    await page.close();
  }
  {
    const { page, errors } = await open("#map=4/38/-97", { phone: true });
    await page.evaluate(() => window.__lowpoints.tour.start("nyc"));
    await page.waitForFunction(() => window.__lowpoints.tour.chapter >= 3 && document.getElementById("tour-text").textContent, { timeout: 240000, polling: 500 });
    await sleep(2500);
    await page.screenshot({ path: join(out, "phone_chapter4.png") });
    r.phoneFits = await page.$eval(".tour", (e) => { const b = e.getBoundingClientRect(); return b.left >= 0 && b.right <= innerWidth && b.top >= 0 && b.bottom <= innerHeight; });
    r.phoneErrors = errors;
    await page.close();
  }
  {
    const { page, errors } = await open("#map=11/40.7/-73.95");
    await page.click("#reports");
    await page.waitForFunction(() => !document.getElementById("reports-key").hidden, { timeout: 20000, polling: 200 });
    await sleep(800);
    const k = await page.evaluate(() => ({ text: document.getElementById("reports-key").innerText, pressed: document.getElementById("reports").getAttribute("aria-pressed"),
      vis: window.__lowpoints.map.getLayoutProperty("reports", "visibility"), n: window.__lowpoints.map.getSource("reports")._data?.features?.length ?? window.__lowpoints.map.getSource("reports").serialize().data.features.length }));
    r.reportsOn = k.pressed === "true" && k.vis === "visible" && k.n === 913 + 203;
    r.reportsCredits = /erm2-nwe9/.test(k.text) && /City of Austin, Texas - data\.austintexas\.gov/.test(k.text) && /downloaded/.test(k.text) && /not where water stood/.test(k.text);
    await page.click("#reports"); await sleep(400);
    r.reportsOff = await page.evaluate(() => document.getElementById("reports-key").hidden && window.__lowpoints.map.getLayoutProperty("reports", "visibility") === "none");
    r.reportsErrors = errors;
    await page.close();
  }
  r.pass = r.reportsOn && r.reportsCredits && r.reportsOff && !r.reportsErrors.length && r.next && r.back && r.dots && r.pause && r.resume && !r.axe.length && r.touchPauses && r.escape && r.exit && r.link
    && r.reducedNoFlights && r.phoneFits && ![...r.errors, ...r.linkErrors, ...r.reducedErrors, ...r.phoneErrors].length;
  return r;
}

try {
  for (const k of ["nyc", "austin"]) if (!only || only === k) { report[k] = await fullRun(k); console.log(k, JSON.stringify(report[k])); }
  if (!only || only === "controls") { report.controls = await controls(); console.log("controls", JSON.stringify(report.controls)); }
} finally { await browser.close(); app.kill(); data.kill(); }
report.pass = Object.values(report).every((x) => x.pass);
writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ pass: report.pass }));
