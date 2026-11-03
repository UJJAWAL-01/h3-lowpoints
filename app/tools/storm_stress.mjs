// 6.3f: stress tests of the rain simulator in headless Chrome, data served locally.
//   node app/tools/storm_stress.mjs DATA_DIR OUT_DIR [--only=name]
// Each scenario abuses the storm bar while it plays, then checks the app is healthy: no page
// errors, play not stuck, the drawn water belongs to the current storm, no NaN or "undefined"
// on screen, memory not growing round after round (soak).
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [dataDir, outArg] = process.argv.slice(2);
const only = (process.argv.find((a) => a.startsWith("--only=")) || "").slice(7);
const out = resolve(outArg);
mkdirSync(out, { recursive: true });
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe"].find(existsSync);
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8772"], { stdio: "ignore" });
const data = spawn(process.execPath, [join(here, "serve.mjs"), resolve(dataDir), "8773"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--enable-precise-memory-info"] });
const base = `http://127.0.0.1:8772/explorer/?packs=${encodeURIComponent("http://127.0.0.1:8773/{file}")}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let seed = 11;
const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
const pick = (a) => a[Math.floor(rand() * a.length)];
const report = {};

async function open(page, hash, viewport = { width: 1360, height: 860 }) {
  await page.setViewport({ deviceScaleFactor: 1, ...viewport });
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank */ } });
  await page.goto(`${base}#${hash}`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.loading === 0 && window.__lowpoints.state.res != null, { timeout: 120000, polling: 250 });
  await sleep(800);
}

// let everything finish: no loading, play ended, figures for the current storm
async function settle(page) {
  await page.evaluate(() => window.__lowpoints.stormBar.stop());
  await page.waitForFunction(() => { const L = window.__lowpoints, b = L.stormBar;
    // and the map library has finished every pack source (new data is processed in its worker after we hand it over)
    const loaded = [...L.state.regions.keys()].every((r) => !L.map.getSource(`pk-${r}`) || L.map.isSourceLoaded(`pk-${r}`));
    return loaded && L.state.loading === 0 && !L.map.isMoving() && !b.playing && (!b.active || b.summary || L.state.res < 8); }, { timeout: 90000, polling: 250 });
  await sleep(1500);
}

const health = (page) => page.evaluate(() => {
  const L = window.__lowpoints, b = L.stormBar, v = L.views, w = v.water, res = L.state.res;
  const panel = document.getElementById("panel-body").innerText, bar = document.getElementById("storm").innerText;
  const out = { active: b.active, playing: b.playing, res, view: v.effective, problems: [] };
  if (/NaN|undefined|Infinity/.test(panel)) out.problems.push("panel shows NaN/undefined");
  if (/NaN|undefined|-\d+ min/.test(bar)) out.problems.push("bar shows NaN/undefined/negative");
  if (b.active && res >= 8) {
    if (!b.summary) out.problems.push("no summary");
    if (!v.storm || JSON.stringify(v.storm.storm) !== JSON.stringify(b.storm)) out.problems.push("map water is for another storm");
    if (v.storm && v.effective !== "wells") for (const r of w.meshes.keys()) if (w.meshOf(r).level === res && !w.hasWater(r, res, v.storm.gen)) out.problems.push(`no water for ${r}`);
  }
  if (!b.active && v.storm) out.problems.push("storm closed but water still set");
  // every visible pack layer is tracked and drawn at the current level
  const m = L.map, visible = m.getStyle().layers.filter((l) => /^pk-.*-fill$/.test(l.id) && m.getLayoutProperty(l.id, "visibility") !== "none");
  for (const l of visible) {
    const r = l.id.slice(3, -5);
    if (!L.state.regions.has(r)) out.problems.push(`untracked pack layer ${r}`);
    else if (res >= 8 && L.state.regions.get(r) !== res) out.problems.push(`pack ${r} drawn at level ${L.state.regions.get(r)}, view at ${res}`);
    if (res === 10 && m.querySourceFeatures(`pk-${r}`).some((ft) => ft.properties.s != null)) out.problems.push(`pack ${r} holds city-scale hexagons at street level`);
  }
  if (res >= 8 && m.getLayoutProperty("overview-fill", "visibility") !== "none" && visible.length) out.problems.push("overview still shown at detail");
  out.heapMB = performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e6) : null;
  out.textures = w.waters.size + v.wells.waters.size;
  return out;
});

async function scenario(name, fn, opts = {}) {
  if (only && !name.startsWith(only)) return;
  const page = await browser.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message.slice(0, 300)}`));
  const t0 = Date.now();
  let result;
  try { result = await fn(page); await settle(page); result.health = await health(page);
    if (result.health.problems.length) result.packLog = await page.evaluate(() => (window.__packLog || []).slice(-40)); }
  catch (e) { result = { crashed: String(e.message || e).slice(0, 400) }; }
  try { await page.screenshot({ path: join(out, `${name}.png`) }); } catch { /* closed */ }
  const ok = !result.crashed && !errors.length && result.health && !result.health.problems.length && (opts.extra ? opts.extra(result) : true);
  report[name] = { ok, seconds: Math.round((Date.now() - t0) / 100) / 10, errors: [...new Set(errors)].slice(0, 8), ...result };
  console.log(name, ok ? "ok" : "FAIL", JSON.stringify(report[name]).slice(0, 400));
  await page.close();
}

const HOU = "map=14.8/29.7604/-95.3698&s=r.10.24";
const play = (page) => page.evaluate(() => { const b = window.__lowpoints.stormBar; if (!b.playing) b.play(); });

try {
  await scenario("s1_play_pause_slider_spam", async (page) => {
    await open(page, HOU);
    for (let k = 0; k < 120; k++) {
      const a = rand();
      if (a < 0.35) await page.evaluate(() => document.querySelector('[data-st="play"]')?.click());
      else if (a < 0.8) await page.evaluate((v) => { const r = document.querySelector('[data-f="time"], [data-f="step"]'); if (!r) return;
        r.value = String(v * Number(r.max)); r.dispatchEvent(new Event("input", { bubbles: true })); if (v > 0.5) r.dispatchEvent(new Event("change", { bubbles: true })); }, rand());
      else await page.keyboard.press(pick(["ArrowLeft", "ArrowRight", "Home", "End", " "]));
      await sleep(15 + rand() * 60);
    }
    return { actions: 120 };
  });

  await scenario("s2_storm_changes_while_playing", async (page) => {
    await open(page, HOU);
    for (let k = 0; k < 24; k++) {
      await play(page);
      await sleep(100 + rand() * 300);
      const a = Math.floor(rand() * 5);
      await page.evaluate((a, r) => {
        const q = (s) => document.querySelector(s);
        if (a === 0) q(`[data-kind="${r < 0.5 ? "amount" : "rarity"}"]`)?.click();
        else if (a === 1) q(`[data-ground="${["dry", "average", "wet"][Math.floor(r * 3)]}"]`)?.click();
        else if (a === 2) q(`[data-pick="${[2, 10, 100][Math.floor(r * 3)]}"]`)?.click();
        else {
          const sel = q(a === 3 ? '[data-f="hours"]' : '[data-f="years"]') || q('[data-f="amount"]');
          if (!sel) return;
          if (sel.tagName === "SELECT") sel.selectedIndex = Math.floor(r * sel.options.length);
          else sel.value = String(Math.round(10 + r * 300));
          sel.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }, a, rand());
    }
    return { changes: 24 };
  });

  await scenario("s3_map_moves_while_playing", async (page) => {
    await open(page, HOU);
    for (let k = 0; k < 30; k++) {
      await play(page);
      const a = Math.floor(rand() * 4);
      await page.evaluate((a, r) => { const m = window.__lowpoints.map;
        if (a === 0) m.panBy([(r - 0.5) * 600, (r - 0.3) * 400], { duration: 200 });
        else if (a === 1) m.zoomTo(12.5 + r * 4, { duration: 250 });
        else if (a === 2) m.easeTo({ pitch: r * 60, duration: 200 });
        else m.easeTo({ bearing: (r - 0.5) * 120, duration: 200 }); }, a, rand());
      await sleep(120 + rand() * 250);
    }
    return { moves: 30 };
  });

  await scenario("s4_views_and_levels_while_playing", async (page) => {
    await open(page, HOU);
    for (let k = 0; k < 24; k++) {
      await play(page);
      if (rand() < 0.5) await page.evaluate((v) => window.__lowpoints.views.set(v), pick(["flat", "columns", "wells"]));
      else await page.evaluate((l) => document.querySelector(`[data-level="${l}"]`)?.click(), pick(["auto", "8", "9", "10"]));
      await sleep(300 + rand() * 500);
    }
    await page.evaluate(() => window.__lowpoints.views.set("flat"));
    return { switches: 24 };
  });

  await scenario("s5_basemap_units_filter_while_playing", async (page) => {
    await open(page, HOU);
    for (let k = 0; k < 18; k++) {
      await play(page);
      const a = k % 3;
      if (a === 0) await page.click("#basemap");
      else if (a === 1) await page.click("#units");
      else await page.evaluate((on) => document.querySelector(`[data-act="${on ? "show-undrained" : "show-all"}"]`)?.click(), k % 2 === 0);
      await sleep(400 + rand() * 600);
    }
    return { toggles: 18 };
  });

  await scenario("s6_open_close_spam", async (page) => {
    await open(page, "map=14.8/29.7604/-95.3698");
    for (let k = 0; k < 30; k++) { await page.click("#rain"); await sleep(rand() * 250); }
    if (await page.evaluate(() => !window.__lowpoints.stormBar.active)) await page.click("#rain");
    await sleep(500);
    await play(page);
    return { toggles: 30 };
  });

  await scenario("s7_phone", async (page) => {
    await open(page, HOU, { width: 390, height: 844, isMobile: true, hasTouch: true });
    for (let k = 0; k < 16; k++) {
      await play(page);
      await page.evaluate(() => document.getElementById("handle").click());
      await sleep(250 + rand() * 400);
      if (k % 4 === 0) await page.evaluate(() => document.querySelector('[data-act="open-dep"]')?.click());
      if (k % 4 === 2) await page.evaluate(() => document.querySelector('[data-act="close"]')?.click());
    }
    return { rounds: 16 };
  });

  await scenario("s8_soak", async (page) => {
    await open(page, HOU);
    const heap = [], textures = [];
    const places = [[-95.3698, 29.7604], [-95.40, 29.74], [-95.34, 29.78], [-95.37, 29.72]];
    for (let round = 0; round < 6; round++) {
      for (const c of places) {
        await page.evaluate((c) => window.__lowpoints.map.jumpTo({ center: c }), c);
        await sleep(600);
        await play(page);
        await sleep(1500);
        await page.evaluate((s) => document.querySelector(`[data-pick="${s}"]`)?.click(), [2, 10, 100][round % 3]);
        await sleep(800);
      }
      await settle(page);
      const h = await health(page);
      heap.push(h.heapMB); textures.push(h.textures);
      await page.evaluate(() => { if (window.gc) window.gc(); });
    }
    return { heapMBPerRound: heap, texturesPerRound: textures };
  }, { extra: (r) => r.heapMBPerRound && Math.max(...r.heapMBPerRound.slice(2)) <= r.heapMBPerRound[1] * 1.6 + 40 && Math.max(...r.texturesPerRound) <= 40 });
} finally {
  await browser.close();
  app.kill(); data.kill();
}
const pass = Object.values(report).every((r) => r.ok);
writeFileSync(join(out, "report.json"), JSON.stringify({ pass, ...report }, null, 1));
console.log(JSON.stringify({ pass, scenarios: Object.fromEntries(Object.entries(report).map(([k, v]) => [k, v.ok])) }));
