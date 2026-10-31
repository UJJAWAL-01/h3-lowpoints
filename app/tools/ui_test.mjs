// Spec 6.2 checks in headless Chrome: console errors, views, a click, screenshots, timings.
//   node app/tools/ui_test.mjs OUT_DIR [--only=name]
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(process.argv[2] || "ui_out");
const only = (process.argv.find((a) => a.startsWith("--only=")) || "").slice(7);
mkdirSync(outDir, { recursive: true });
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const port = 8766, base = `http://127.0.0.1:${port}/explorer/`;
const server = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), String(port)], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 700));

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const report = {};

async function settle(page, ms = 60000) {
  await page.waitForFunction(() => {
    const L = window.__lowpoints;
    return L && L.map.loaded() && L.map.areTilesLoaded() && L.state.loading === 0 && !L.map.isMoving();
  }, { timeout: ms, polling: 250 });
  await new Promise((r) => setTimeout(r, 600));
}

async function run(name, { width, height, mobile = false, hash = "", act = null }) {
  if (only && name !== only) return;
  const page = await browser.newPage();
  const errors = [], requests = [];
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errors.push(`${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("requestfinished", (r) => requests.push(r.url()));
  page.on("response", (r) => { if (r.status() >= 400) errors.push(`HTTP ${r.status()}: ${r.url()}`); });
  page.on("requestfailed", (r) => { if (r.failure()?.errorText !== "net::ERR_ABORTED") errors.push(`failed: ${r.url()} ${r.failure()?.errorText}`); });
  await page.setViewport({ width, height, deviceScaleFactor: 1, isMobile: mobile, hasTouch: mobile });
  const t0 = Date.now();
  await page.goto(`${base}${hash}`, { waitUntil: "domcontentloaded" });
  await settle(page);
  const firstView = Date.now() - t0;
  const marks = await page.evaluate(() => Object.fromEntries(performance.getEntriesByType("mark")
    .filter((m) => m.name.startsWith("lowpoints:")).map((m) => [m.name.slice(10), Math.round(m.startTime)])));
  let extra = {};
  if (act) extra = (await act(page)) || {};
  await page.screenshot({ path: join(outDir, `${name}.png`) });
  const byHost = {};
  for (const u of requests) {
    const x = new URL(u);
    const k = /explorer-data|zenodo/.test(u) ? "data packs" : /explorer-basemap|xet-bridge/.test(u) ? "basemap" : x.host;
    byHost[k] = (byHost[k] || 0) + 1;
  }
  report[name] = { firstViewMs: firstView, marks, errors, requestsByHost: byHost, ...extra };
  await page.close();
}

async function clickCentre(page) {
  const box = await page.evaluate(() => {
    const L = window.__lowpoints, m = L.map;
    const layers = [...L.state.regions.keys()].map((r) => `pk-${r}-fill`).filter((id) => m.getLayer(id));
    const c = m.getCanvas(), w = c.clientWidth, h = c.clientHeight;
    // the rendered depression hexagon nearest the centre
    let best = null;
    for (let dy = -120; dy <= 120; dy += 12) for (let dx = -120; dx <= 120; dx += 12) {
      const f = m.queryRenderedFeatures([w / 2 + dx, h / 2 + dy], { layers })[0];
      if (f && (!best || dx * dx + dy * dy < best.d)) best = { x: w / 2 + dx, y: h / 2 + dy, d: dx * dx + dy * dy, src: f.source, dep: f.properties.dep };
    }
    return best;
  });
  if (!box) return { clicked: false };
  await page.mouse.click(box.x, box.y);
  await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 900));
  const check = await page.evaluate(async () => {
    const L = window.__lowpoints, d = L.state.selected.data;
    const panel = document.getElementById("panel-body").innerText;
    return { region: d.region, index: d.index, id: d.id, volume: d.volume, depth: d.depth, panelHasId: panel.includes(d.id),
      panelHead: panel.split("\n").slice(0, 6).join(" | ") };
  });
  return { clicked: true, ...check };
}

try {
  await run("desktop_country", { width: 1440, height: 900 });
  await run("phone_country", { width: 390, height: 844, mobile: true });
  await run("desktop_houston_z11", { width: 1440, height: 900, hash: "#map=11/29.76/-95.37" });
  await run("desktop_houston_z14_click", { width: 1440, height: 900, hash: "#map=14.2/29.7604/-95.3698", act: clickCentre });
  await run("phone_houston_click", { width: 390, height: 844, mobile: true, hash: "#map=14.2/29.7604/-95.3698", act: clickCentre });
  await run("desktop_z7_overview6", { width: 1440, height: 900, hash: "#map=7.4/40.6/-74.6" });
  await run("desktop_permalink", { width: 1440, height: 900, hash: "#d=84446cbffffffff.11104772", act: async (page) => {
    await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 60000 });
    await settle(page);
    return page.evaluate(() => ({ restoredId: window.__lowpoints.state.selected.data.id, zoom: Math.round(window.__lowpoints.map.getZoom() * 10) / 10,
      hash: location.hash }));
  } });
  await run("desktop_search", { width: 1440, height: 900, act: async (page) => {
    await page.click("#search");
    await page.type("#search", "austin tx", { delay: 30 });
    await page.waitForFunction(() => document.querySelectorAll("#results li[data-i]").length > 0, { timeout: 20000 });
    const first = await page.$eval("#results li[data-i]", (li) => li.innerText.replace(/\s+/g, " "));
    await page.keyboard.press("Enter");
    await new Promise((r) => setTimeout(r, 2500));
    await settle(page);
    const c = await page.evaluate(() => { const m = window.__lowpoints.map; return [m.getCenter().lat, m.getCenter().lng, m.getZoom()]; });
    return { firstResult: first, centre: c.map((x) => Math.round(x * 100) / 100) };
  } });
  for (const [name, w, h, mobile] of [["desktop_guide", 1440, 900, false], ["phone_guide", 390, 844, true]]) {
    await run(name, { width: w, height: h, mobile, act: async (page) => {
      const steps = [];
      await page.click("#guide-open");
      for (let k = 0; k < 9; k++) {
        await page.waitForFunction(() => { const c = document.querySelector(".guide-card"); return !c || !c.classList.contains("busy"); }, { timeout: 60000 });
        await new Promise((r) => setTimeout(r, 700));
        const card = await page.$(".guide-card");
        if (!card) break;
        const title = await page.$eval("#guide-title", (e) => e.textContent);
        steps.push(title);
        await page.screenshot({ path: join(outDir, `${name}_${k + 1}.png`) });
        await page.click(".guide-card [data-g=next]");
      }
      return { guideSteps: steps, guideClosed: !(await page.$(".guide-card")) };
    } });
  }
  await run("desktop_basemap_swap", { width: 1440, height: 900, hash: "#map=14.2/29.7604/-95.3698", act: async (page) => {
    const sel = await clickCentre(page);
    await page.click("#basemap");
    await new Promise((r) => setTimeout(r, 1500));
    await settle(page);
    const after = await page.evaluate(() => {
      const m = window.__lowpoints.map, L = window.__lowpoints.state;
      return { packLayers: [...L.regions.keys()].filter((r) => m.getLayer(`pk-${r}-fill`)).length,
        selectionCells: m.getSource("selection")?._data?.features?.length ?? m.querySourceFeatures("selection").length,
        hasRoadsPier: !!m.getLayer("roads_pier"), quiet: localStorage.getItem("lowpoints.basemap") };
    });
    await page.evaluate(() => localStorage.removeItem("lowpoints.basemap"));
    return { selected: sel.id, ...after };
  } });
  await run("desktop_invite", { width: 1440, height: 900, act: async (page) => {
    await page.evaluate(() => localStorage.removeItem("lowpoints.guided"));
    await page.goto(`${base}`, { waitUntil: "domcontentloaded" }); await page.evaluate(() => { history.replaceState(null, "", location.pathname); }); await page.reload({ waitUntil: "domcontentloaded" });
    await settle(page);
    await new Promise((r) => setTimeout(r, 800));
    return { inviteShown: !!(await page.$(".invite")) };
  } });
  await run("desktop_about", { width: 1440, height: 900, act: async (page) => {
    await page.click("#about-open"); await new Promise((r) => setTimeout(r, 400));
    return { aboutOpen: await page.$eval("#about", (d) => d.open) };
  } });
} finally {
  await browser.close();
  server.kill();
}
writeFileSync(join(outDir, "report.json"), JSON.stringify(report, null, 1));
console.log(JSON.stringify(report, null, 1));
