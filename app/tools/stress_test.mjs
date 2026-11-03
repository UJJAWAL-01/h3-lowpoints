// Stress and edge-case tests for the explorer in headless Chrome.
//   node app/tools/stress_test.mjs OUT_DIR [--only=name]
// Each scenario opens a fresh page, records console errors and page errors, and returns findings.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(process.argv[2] || "stress_out");
const only = (process.argv.find((a) => a.startsWith("--only=")) || "").slice(7);
mkdirSync(outDir, { recursive: true });
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const port = 8768, base = `http://127.0.0.1:${port}/explorer/`;
const server = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), String(port)], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 700));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--enable-precise-memory-info"] });
const report = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const US = { w: -124, e: -67.5, s: 25.5, n: 48.8 };
let seed = 7;
const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

async function settle(page, ms = 90000) {
  await page.waitForFunction(() => {
    const L = window.__lowpoints;
    return L && L.map.loaded() && L.state.loading === 0 && !L.map.isMoving();
  }, { timeout: ms, polling: 250 });
  await sleep(500);
}

async function open(page, url, viewport = { width: 1440, height: 900 }) {
  await page.setViewport({ deviceScaleFactor: 1, ...viewport });
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch {} });
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await settle(page);
}

async function scenario(name, fn, { bypassCSP = false } = {}) {
  if (only && !name.startsWith(only)) return;
  const page = await browser.newPage();
  if (bypassCSP) await page.setBypassCSP(true);
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message.slice(0, 300)}`));
  page.on("response", (r) => { if (r.status() >= 400) errors.push(`HTTP ${r.status()}: ${r.url().slice(0, 200)}`); });
  const t0 = Date.now();
  let result;
  try { result = await fn(page, errors); } catch (e) { result = { crashed: String(e.message || e).slice(0, 400) }; }
  try { await page.screenshot({ path: join(outDir, `${name}.png`) }); } catch {}
  report[name] = { seconds: Math.round((Date.now() - t0) / 100) / 10, errors: [...new Set(errors)].slice(0, 12), ...result };
  console.log(name, JSON.stringify(report[name]).slice(0, 600));
  await page.close();
}

const state = (page) => page.evaluate(() => {
  const L = window.__lowpoints, m = L.map;
  const style = m.getStyle();
  return { zoom: Math.round(m.getZoom() * 10) / 10, res: L.state.res, loading: L.state.loading,
    regionsTracked: L.state.regions.size,
    packSources: Object.keys(style.sources).filter((k) => k.startsWith("pk-")).length,
    layers: style.layers.length, view: L.state.view,
    heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e6) : null };
});

try {
  // 1. rapid random navigation across the country, overview and detail mixed
  await scenario("01_rapid_navigation", async (page) => {
    await open(page, base);
    const before = await state(page);
    for (let k = 0; k < 40; k++) {
      const lon = US.w + rand() * (US.e - US.w), lat = US.s + rand() * (US.n - US.s);
      const zoom = k % 3 === 0 ? 3 + rand() * 6 : 10.6 + rand() * 4;
      await page.evaluate((c, z) => window.__lowpoints.map.jumpTo({ center: c, zoom: z }), [lon, lat], zoom);
      await sleep(150);
    }
    await settle(page, 180000);
    const after = await state(page);
    return { before, after, ok: after.loading === 0 && after.packSources <= 18 };
  });

  // 2. a long pan in detail view (Houston to Dallas): sources must stay bounded
  await scenario("02_long_pan", async (page) => {
    await open(page, `${base}#map=11/29.76/-95.37`);
    const from = [-95.37, 29.76], to = [-96.8, 32.78];
    for (let k = 1; k <= 24; k++) {
      const t = k / 24;
      await page.evaluate((c) => window.__lowpoints.map.jumpTo({ center: c }), [from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t]);
      await sleep(400);
    }
    await settle(page, 180000);
    const s = await state(page);
    return { ...s, ok: s.packSources <= 18 && s.loading === 0 };
  });

  // 3. rapid clicks on different hexagons: the panel must show the last one clicked
  await scenario("03_rapid_clicks", async (page) => {
    await open(page, `${base}#map=14.2/29.7604/-95.3698`);
    const targets = await page.evaluate(() => {
      const L = window.__lowpoints, m = L.map;
      const layers = [...L.state.regions.keys()].map((r) => `pk-${r}-fill`).filter((id) => m.getLayer(id));
      const out = [], seen = new Set(), c = m.getCanvas();
      for (let y = 100; y < c.clientHeight - 100; y += 40) for (let x = 450; x < c.clientWidth - 100; x += 40) {
        const f = m.queryRenderedFeatures([x, y], { layers })[0];
        if (f && !seen.has(`${f.source}:${f.properties.dep}`)) { seen.add(`${f.source}:${f.properties.dep}`); out.push({ x, y, src: f.source.slice(3), dep: f.properties.dep }); }
      }
      return out.slice(0, 15);
    });
    for (const t of targets) { await page.mouse.click(t.x, t.y); await sleep(25); }
    await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 30000 });
    await sleep(1500);
    const last = targets[targets.length - 1];
    const shown = await page.evaluate(() => { const d = window.__lowpoints.state.selected.data; return { region: d.region, index: d.index }; });
    return { clicks: targets.length, last, shown, ok: shown.region === last.src && shown.index === last.dep };
  });

  // 4. broken, tampered or extreme links
  await scenario("04_bad_links", async (page) => {
    const hashes = ["#d=garbage", "#d=84446cbffffffff.999999999999", "#d=8426cf1ffffffff.1", "#d=..", "#map=abc/def",
      "#map=10/0/0", "#map=30/40/-100", "#map=-5/95/400", "#map=12/29.76/-95.37&d=zzz", "#d=<script>alert(1)</script>",
      "#map=12/29.76/-95.37&d=84446cbffffffff.11104772&d=84446cbffffffff.1"];
    const out = {};
    for (const h of hashes) {
      await page.goto("about:blank");
      await page.setViewport({ width: 1440, height: 900 });
      await page.goto(`${base}${h}`, { waitUntil: "domcontentloaded" });
      try { await settle(page, 60000); out[h] = { ...(await state(page)), xss: await page.evaluate(() => !!window.__xss) }; }
      catch (e) { out[h] = { failed: String(e.message).slice(0, 120) }; }
    }
    return { cases: out };
  });

  // 5. odd search input
  await scenario("05_search_edge", async (page) => {
    await open(page, base);
    const inputs = ["", "   ", "<img src=x onerror=window.__xss=1>", "a".repeat(500), "Cañon City", "st louis", "TX",
      "zzzzqqq", "new york ny", "springfield", "san josé", "o'fallon", "1234"];
    const out = {};
    for (const q of inputs) {
      await page.click("#search", { clickCount: 3 });
      await page.keyboard.press("Backspace");
      if (q) await page.type("#search", q, { delay: 2 });
      await sleep(350);
      out[q.slice(0, 30)] = await page.evaluate(() => ({
        shown: !document.getElementById("results").hidden,
        items: [...document.querySelectorAll("#results li[data-i]")].slice(0, 3).map((li) => li.innerText.replace(/\s+/g, " ")),
        empty: !!document.querySelector("#results .empty") }));
    }
    // keyboard beyond the ends
    await page.click("#search", { clickCount: 3 }); await page.type("#search", "port");
    await sleep(300);
    for (let k = 0; k < 12; k++) await page.keyboard.press("ArrowDown");
    for (let k = 0; k < 20; k++) await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Escape");
    return { cases: out, xss: await page.evaluate(() => !!window.__xss), ok: !(await page.evaluate(() => !!window.__xss)) };
  });

  // 6. both data hosts unreachable (local test override): a message, no crash, recovers
  await scenario("06_dead_pack_hosts", async (page, errors) => {
    await open(page, `${base}?packs=${encodeURIComponent("http://127.0.0.1:9/{file}")}#map=13/29.76/-95.37`);
    await sleep(1500);
    const toast = await page.$eval("#toast", (t) => ({ shown: t.classList.contains("show"), text: t.innerText }));
    const s = await state(page);
    return { toast, ...s, consoleErrorsAreNetworkOnly: errors.every((e) => /Failed to load|ERR_CONNECTION|net::/i.test(e)) };
  });

  // 7. the street map blocked: hexagons still drawn, a message shown
  await scenario("07_basemap_blocked", async (page) => {
    await page.setRequestInterception(true);
    page.on("request", (r) => (/explorer-basemap|xet-bridge/.test(r.url()) ? r.abort() : r.continue()));
    await page.setViewport({ width: 1440, height: 900 });
    await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch {} });
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.overviewKey, { timeout: 60000 }).catch(() => {});
    await sleep(2000);
    return page.evaluate(() => ({ overviewDrawn: !!window.__lowpoints.state.overviewKey,
      hexagonsRendered: window.__lowpoints.map.queryRenderedFeatures({ layers: ["overview-fill"] }).length,
      toast: document.getElementById("toast").innerText }));
  });

  // 8. a missing overview file: a message, no crash
  await scenario("08_overview_missing", async (page) => {
    await page.setRequestInterception(true);
    page.on("request", (r) => (/overview_4\.bin/.test(r.url()) ? r.respond({ status: 404, body: "" }) : r.continue()));
    await page.setViewport({ width: 1440, height: 900 });
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await sleep(6000);
    return page.evaluate(() => ({ mapLoaded: window.__lowpoints?.map.loaded(), toast: document.getElementById("toast").innerText,
      panel: document.getElementById("panel-body").innerText.slice(0, 80) }));
  });

  // 9. resizing between desktop and phone with a depression open
  await scenario("09_resize", async (page) => {
    await open(page, `${base}#map=14.2/29.7604/-95.3698&d=84446cbffffffff.11104772`);
    await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 30000 });
    const sizes = [[390, 844], [1440, 900], [768, 1024], [1024, 600], [390, 844], [1920, 1080]];
    const out = [];
    for (const [w, h] of sizes) {
      await page.setViewport({ width: w, height: h });
      await sleep(600);
      out.push(await page.evaluate(() => {
        const p = document.getElementById("panel").getBoundingClientRect();
        const tools = document.querySelector(".tools").getBoundingClientRect();
        return { w: innerWidth, panelVisible: p.bottom > 0 && p.top < innerHeight && p.width > 0, panelTop: Math.round(p.top),
          overflowX: document.documentElement.scrollWidth > innerWidth, toolsInside: tools.right <= innerWidth };
      }));
    }
    return { sizes: out, ok: out.every((o) => o.panelVisible && !o.overflowX && o.toolsInside) };
  });

  // 10. keyboard only
  await scenario("10_keyboard", async (page) => {
    await open(page, base);
    const order = [];
    for (let k = 0; k < 22; k++) {
      await page.keyboard.press("Tab");
      order.push(await page.evaluate(() => { const a = document.activeElement; return a.id || a.getAttribute("aria-label") || a.textContent.trim().slice(0, 20) || a.tagName; }));
    }
    await page.keyboard.press("Escape");
    await page.evaluate(() => document.activeElement.blur());
    await page.keyboard.press("/");
    const slashFocusesSearch = await page.evaluate(() => document.activeElement.id === "search");
    const focusVisible = await page.evaluate(() => getComputedStyle(document.querySelector("#zoom-in"), ":focus-visible").outlineStyle);
    return { tabOrder: order, slashFocusesSearch };
  });

  // 11. level buttons pressed fast, then zoom across families
  await scenario("11_level_pins", async (page) => {
    await open(page, base);
    for (const v of ["10", "4", "8", "auto", "6", "9", "5", "10", "auto", "8"]) {
      await page.click(`#levels button[data-level="${v}"]`);
      await sleep(120);
    }
    await settle(page, 120000);
    const a = await state(page);
    await page.evaluate(() => window.__lowpoints.map.jumpTo({ zoom: 4 }));
    await settle(page);
    const b = await state(page);
    return { afterButtons: a, afterZoomOut: b, pinned: await page.evaluate(() => window.__lowpoints.state.pinned) };
  });

  // 12. units switched while a depression is open
  await scenario("12_units", async (page) => {
    await open(page, `${base}#map=15/29.7604/-95.3698&d=84446cbffffffff.11104772`);
    await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 30000 });
    await sleep(500);
    const metric = await page.$eval("#panel-body", (e) => e.innerText.match(/Water can stand up to ([^ ]+ [a-z]+)/)?.[1]);
    await page.click("#units"); await sleep(300);
    const us = await page.$eval("#panel-body", (e) => e.innerText.match(/Water can stand up to ([^ ]+ [a-z]+)/)?.[1]);
    await page.click("#units"); await sleep(300);
    return { metric, us, ok: /m$/.test(metric || "") && /ft$/.test(us || "") };
  });

  // 13. the guide: interrupted, restarted, resized, gone backwards
  await scenario("13_guide_edge", async (page) => {
    await open(page, base);
    await page.click("#guide-open"); await sleep(500);
    await page.keyboard.press("ArrowRight"); await page.keyboard.press("ArrowRight"); await sleep(400);
    await page.keyboard.press("Escape"); await sleep(300);
    const closedByEsc = !(await page.$(".guide-card"));
    await page.click("#guide-open"); await sleep(200);
    await page.click("#guide-open").catch(() => {});                 // second press while open
    for (let k = 0; k < 4; k++) { await page.keyboard.press("ArrowRight"); await sleep(250); }
    await page.waitForFunction(() => !document.querySelector(".guide-card.busy"), { timeout: 60000 });
    await page.setViewport({ width: 390, height: 844 }); await sleep(600);
    const cardRect = await page.evaluate(() => { const r = document.querySelector(".guide-card")?.getBoundingClientRect();
      return r && { l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), vw: innerWidth, vh: innerHeight,
        step: document.getElementById("guide-title")?.textContent }; });
    const cardOnScreen = !!cardRect && cardRect.l >= 0 && cardRect.r <= cardRect.vw && cardRect.t >= 0 && cardRect.b <= cardRect.vh;
    for (let k = 0; k < 6; k++) { await page.keyboard.press("ArrowLeft"); await sleep(200); }
    const backAtStart = await page.$eval("#guide-title", (e) => e.textContent).catch(() => null);
    const cards = await page.$$eval(".guide-card", (c) => c.length);
    await page.keyboard.press("Escape");
    return { closedByEsc, cardOnScreen, cardRect, backAtStart, cardsOpenAtOnce: cards, ok: closedByEsc && cardOnScreen && cards === 1 };
  });

  // 14. street map switched many times quickly with a selection open
  await scenario("14_basemap_spam", async (page) => {
    await open(page, `${base}#map=15/29.7604/-95.3698&d=84446cbffffffff.11104772`);
    await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 30000 });
    for (let k = 0; k < 7; k++) { await page.click("#basemap"); await sleep(80); }
    await sleep(2500);
    await settle(page);
    const s = await page.evaluate(() => {
      const m = window.__lowpoints.map, L = window.__lowpoints.state;
      return { packLayers: [...L.regions.keys()].filter((r) => m.getLayer(`pk-${r}-fill`)).length,
        hexTop: !!m.getLayer("hex-top"), selection: !!m.getLayer("selection-line"), spill: !!m.getLayer("spill-arrows"),
        kind: localStorage.getItem("lowpoints.basemap") };
    });
    await page.evaluate(() => localStorage.removeItem("lowpoints.basemap"));
    return { ...s, ok: s.packLayers > 0 && s.hexTop && s.selection && s.spill };
  });

  // 15. accessibility audit (axe-core) in three states
  const axe = readFileSync(join(here, "..", "node_modules", "axe-core", "axe.min.js"), "utf8");
  await scenario("15_accessibility", async (page) => {
    await open(page, `${base}#map=15/29.7604/-95.3698`);
    const audit = async (label) => {
      const r = await page.evaluate(async () => {
        const res = await window.axe.run(document, { resultTypes: ["violations"], rules: { region: { enabled: false } } });
        return res.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, help: v.help,
          sample: v.nodes[0]?.target?.join(" ") }));
      });
      return [label, r];
    };
    await page.addScriptTag({ content: axe });
    const out = [];
    out.push(await audit("intro"));
    await page.evaluate(() => window.__lowpoints.selectDepression("84446cbffffffff", 911));
    await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 30000 }); await sleep(600);
    out.push(await audit("depression"));
    await page.click("#about-open"); await sleep(400);
    out.push(await audit("about"));
    return { audits: Object.fromEntries(out) };
  }, { bypassCSP: true });

  // 16. smoothness while panning (indicative only: software graphics in headless Chrome)
  await scenario("16_frame_pacing", async (page) => {
    await open(page, `${base}#map=13/29.76/-95.37`);
    const r = await page.evaluate(async () => {
      const m = window.__lowpoints.map, gaps = [];
      let last = performance.now(), run = true;
      const tick = (t) => { gaps.push(t - last); last = t; if (run) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
      for (let k = 0; k < 60; k++) { m.panBy([12, 4], { duration: 0 }); await new Promise((x) => setTimeout(x, 50)); }
      run = false;
      gaps.sort((a, b) => a - b);
      return { frames: gaps.length, medianMs: Math.round(gaps[gaps.length >> 1]), p95Ms: Math.round(gaps[Math.floor(gaps.length * 0.95)]) };
    });
    return r;
  });

  // 17. memory over a long session
  await scenario("17_memory_soak", async (page) => {
    await open(page, base);
    const heap = [];
    for (let round = 0; round < 5; round++) {
      for (let k = 0; k < 20; k++) {
        const lon = -98 + rand() * 20, lat = 29 + rand() * 12;
        await page.evaluate((c, z) => window.__lowpoints.map.jumpTo({ center: c, zoom: z }), [lon, lat], 11 + rand() * 3);
        await sleep(250);
      }
      await settle(page, 180000);
      const cdp = await page.createCDPSession();
      await cdp.send("HeapProfiler.collectGarbage");
      await sleep(500);
      heap.push((await state(page)).heapMB);
    }
    return { heapMBAfterEachRound: heap, packSources: (await state(page)).packSources };
  });

  // 18. every link the app shows opens
  await scenario("18_links", async (page) => {
    await open(page, `${base}#map=15/29.7604/-95.3698&d=84446cbffffffff.11104772`);
    await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 30000 });
    const links = await page.evaluate(() => [...new Set([...document.querySelectorAll("a[href]")].map((a) => a.href))]);
    const results = {};
    for (const u of links) {
      if (!u.startsWith("http")) continue;
      const headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36" };
      try { const r = await fetch(u, { redirect: "follow", headers }); results[u] = r.status; } catch (e) { results[u] = String(e.message); }
      const hash = new URL(u).hash.slice(1);
      if (hash && results[u] === 200) {
        const html = await (await fetch(u, { headers })).text();
        if (!html.includes(`id="${hash}"`)) results[u] = `200 but no #${hash}`;
      }
    }
    return { links: results, ok: Object.values(results).every((v) => v === 200) };
  });
} finally {
  await browser.close();
  server.kill();
}
writeFileSync(join(outDir, "stress_report.json"), JSON.stringify(report, null, 1));
