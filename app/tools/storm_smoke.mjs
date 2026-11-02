// Quick look at the storm bar in headless Chrome (local data): errors, a storm, play frame times,
// screenshots at the start, middle and end of play in Flat, Columns and Wells.
//   node app/tools/storm_smoke.mjs DATA_DIR OUT_DIR
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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const report = { errors: [] };
try {
  const page = await browser.newPage();
  page.on("pageerror", (e) => report.errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") report.errors.push(`${m.type()}: ${m.text()}`); });
  await page.setViewport({ width: 1360, height: 860 });
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* about:blank */ } });
  await page.goto(`${base}#map=15.2/29.7604/-95.3698`, { waitUntil: "domcontentloaded" });
  const ready = () => page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.regions.size > 0 && window.__lowpoints.state.loading === 0, { timeout: 120000, polling: 300 });
  await ready();
  const shot = (n) => page.screenshot({ path: join(out, `${n}.png`) });
  for (const view of ["flat", "columns", "wells"]) {
    if (view !== "flat") { await page.evaluate((v) => window.__lowpoints.views.set(v), view); await sleep(2500); await ready(); }
    if (view === "flat") {
      await page.click("#rain");
      try { await page.waitForFunction(() => document.querySelector("[data-sv=fill]"), { timeout: 30000, polling: 300 }); }
      catch (e) { await shot("fail"); report.fail = { panel: await page.evaluate(() => document.getElementById("panel-body").innerText.slice(0, 600)), bar: await page.evaluate(() => document.getElementById("storm").innerText), hash: await page.evaluate(() => location.hash) }; throw e; }
    }
    await sleep(1500);
    await shot(`${view}_end`);
    // play, sampling frame gaps and screenshots mid-way
    const t = await page.evaluate(() => new Promise((res) => {
      const gaps = [], steps = [];
      let last = performance.now();
      document.querySelector('[data-st="play"]').click();
      const loop = (now) => { gaps.push(now - last); last = now; const b = document.querySelector(".storm-now"); steps.push(b ? b.textContent : "");
        if (gaps.length < 400 && document.querySelector('[data-st="play"]').textContent === "Pause") requestAnimationFrame(loop); else res({ gaps, steps }); };
      requestAnimationFrame(loop);
    }));
    const raw = await page.evaluate(() => { const a = (window.__stormTicks || []).slice(); window.__stormTicks = []; return a; });
    const ticks = raw.slice().sort((x, y) => x - y);
    const slow = raw.map((x, i) => [i, +x.toFixed(1)]).filter(([, x]) => x > 4);
    const g = t.gaps.slice(2).sort((a, b) => a - b);
    report[view] = { frames: g.length, medianMs: Math.round(g[g.length >> 1] * 10) / 10, p95Ms: Math.round(g[Math.floor(g.length * 0.95)] * 10) / 10,
      slowFrames: slow,
      ourCodePerFrameMs: { median: +(ticks[ticks.length >> 1] || 0).toFixed(2), max: +(ticks[ticks.length - 1] || 0).toFixed(2), n: ticks.length },
      labels: [t.steps[5], t.steps[Math.floor(t.steps.length / 2)], t.steps[t.steps.length - 1]] };
    await page.evaluate(() => window.__lowpoints && document.querySelector('[data-f="step"]') && (document.querySelector('[data-f="step"]').value = "6"));
    await page.evaluate(() => { const r = document.querySelector('[data-f="step"]'); r.value = "6.5"; r.dispatchEvent(new Event("input", { bubbles: true })); });
    await sleep(800);
    await shot(`${view}_mid`);
  }
  // more states: a depression's gauge, city scale, an amount storm, a phone, Seattle (no Atlas 14)
  await page.evaluate(() => window.__lowpoints.views.set("flat")); await sleep(1500);
  await page.evaluate(() => { const r = document.querySelector('[data-f="step"]'); r.value = "9"; r.dispatchEvent(new Event("change", { bubbles: true })); });
  await sleep(800);
  const first = await page.$('[data-act="open-dep"]');
  if (first) { await first.click(); await page.waitForFunction(() => document.querySelector("[data-gauge-body]"), { timeout: 30000 }).catch(() => {}); await sleep(1500); }
  await shot("gauge");
  report.gauge = await page.evaluate(() => document.querySelector(".gauge")?.innerText || null);
  await page.evaluate(() => document.querySelector('[data-act="close"]')?.click());
  await page.evaluate(() => window.__lowpoints.map.jumpTo({ center: [-95.37, 29.76], zoom: 12.6 })); await sleep(2500); await ready(); await sleep(2500);
  await shot("city_level9");
  await page.click('[data-kind="amount"]'); await sleep(2500);
  report.amount = await page.evaluate(() => document.querySelector(".storm-readout").innerText);
  await shot("amount");
  await page.goto("about:blank"); await page.goto(`${base}#map=14.5/47.6062/-122.3321&s=r.100.24`, { waitUntil: "domcontentloaded" }); await ready(); await sleep(4000);
  report.seattle = await page.evaluate(() => ({ bar: document.querySelector(".storm-readout")?.innerText, panel: document.getElementById("panel-body").innerText.slice(0, 400) }));
  await shot("seattle");
  await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.goto("about:blank"); await page.goto(`${base}#map=15/29.7604/-95.3698&s=r.10.24&t=8`, { waitUntil: "domcontentloaded" }); await ready(); await sleep(4000);
  await shot("phone");
  report.phoneHash = await page.evaluate(() => location.hash);
  report.panel = await page.evaluate(() => document.getElementById("panel-body").innerText.slice(0, 900));
  report.bar = await page.evaluate(() => document.getElementById("storm").innerText);
  report.hash = await page.evaluate(() => location.hash);
} catch (e) { report.errors.push("test: " + e.message); } finally {
  await browser.close();
  app.kill(); data.kill();
}
writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 1));
console.log(JSON.stringify(report, null, 1));
