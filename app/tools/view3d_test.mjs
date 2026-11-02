// Spec 6.3d checks in headless Chrome: the Flat / Columns / Wells views.
//   node app/tools/view3d_test.mjs OUT_DIR
// Software graphics (SwiftShader): frame times here are not real-device numbers.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(process.argv[2] || "view3d_out");
mkdirSync(outDir, { recursive: true });
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const port = 8769, base = `http://127.0.0.1:${port}/explorer/`;
const server = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), String(port)], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 700));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const report = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const STORM = { kind: "rarity", years: 100, hours: 24, condition: "average" };

async function settle(page, ms = 90000) {
  await page.waitForFunction(() => {
    const L = window.__lowpoints;
    return L && L.map.loaded() && L.map.areTilesLoaded() && L.state.loading === 0 && !L.map.isMoving();
  }, { timeout: ms, polling: 250 });
  await sleep(900);
}

async function scenario(name, { width = 1440, height = 900, mobile = false, hash = "" }, act) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errors.push(`${m.type()}: ${m.text()}`); });
  page.on("requestfailed", (r) => { if (r.failure()?.errorText !== "net::ERR_ABORTED") errors.push(`failed: ${r.url()}`); });
  await page.setViewport({ width, height, isMobile: mobile, hasTouch: mobile });
  await page.evaluateOnNewDocument(() => localStorage.setItem("lowpoints.guided", "1"));
  let out = {};
  try {
    await page.goto(`${base}${hash}`, { waitUntil: "domcontentloaded" });
    await settle(page);
    out = (await act(page)) || {};
  } catch (e) { errors.push(`test: ${e.message}`); }
  await page.screenshot({ path: join(outDir, `${name}.png`) }).catch(() => {});
  report[name] = { errors, ...out };
  console.log(name, JSON.stringify(report[name]).slice(0, 400));
  await page.close();
}

const shot = async (page, file) => { await page.screenshot({ path: join(outDir, file) }); return readFileSync(join(outDir, file)); };
const viewState = (page) => page.evaluate(() => {
  const L = window.__lowpoints, m = L.map;
  const vis = (id) => m.getLayer(id) ? m.getLayoutProperty(id, "visibility") !== "none" : null;
  const r = [...L.state.regions.keys()][0];
  return { view: L.views.view, effective: L.views.effective, res: L.state.res, pitch: Math.round(m.getPitch()),
    bearing: Math.round(m.getBearing()), note: document.getElementById("view-note").textContent,
    noteShown: !document.getElementById("view-note").hidden, hash: location.hash,
    overviewCol: vis("overview-col"), packCol: r ? vis(`pk-${r}-col`) : null, wellsOn: L.views.wells.on,
    wellsMeshes: L.views.wells.meshes.size, wellsLayer: !!m.getLayer("wells") };
});

try {
  // 1. columns across the country
  await scenario("columns_country", { hash: "#map=3.6/38.6/-97.6&v=columns" }, async (page) => viewState(page));

  // 2. columns at city scale (level 9), with the heights rule checked on drawn hexagons
  await scenario("columns_city", { hash: "#map=12.6/29.76/-95.37" }, async (page) => {
    await page.click('[data-view="columns"]'); await settle(page);
    const heights = await page.evaluate(() => {
      const L = window.__lowpoints, r = [...L.state.regions.keys()][0];
      const fs = L.map.querySourceFeatures(`pk-${r}`).slice(0, 400);
      let bad = 0;
      for (const f of fs) {
        const p = f.properties, want = L.state.res === 10 ? p.d * 30 : Math.min(p.s, 0.25) * 100 * { 8: 30, 9: 12 }[L.state.res];
        if (Math.abs(p.hc - want) > 0.06) bad++;
      }
      return { sampled: fs.length, wrong: bad };
    });
    return { ...(await viewState(page)), heights };
  });

  // 3. columns at street scale with a storm: water at two steps must differ, heights rule at level 10
  await scenario("columns_street_storm", { hash: "#map=15/29.7604/-95.3698" }, async (page) => {
    await page.click('[data-view="columns"]'); await settle(page);
    const t0 = Date.now();
    await page.evaluate((s) => window.__lowpoints.views.setStorm(s), STORM);
    const stormMs = Date.now() - t0;
    await page.evaluate(() => window.__lowpoints.views.setStep(3)); await sleep(1200);
    const a = await shot(page, "columns_street_step4.png");
    const t1 = Date.now();
    await page.evaluate(() => window.__lowpoints.views.setStep(15));
    const stepMs = Date.now() - t1;
    await sleep(1200);
    const b = await shot(page, "columns_street_step16.png");
    const check = await page.evaluate(() => {
      const L = window.__lowpoints, r = [...L.state.regions.keys()].find((x) => L.map.getLayer(`pk-${x}-col`));
      const fs = L.map.querySourceFeatures(`pk-${r}`).slice(0, 400);
      let wrong = 0, wet = 0;
      for (const f of fs) {
        if (Math.abs(f.properties.hc - f.properties.d * 30) > 0.06) wrong++;
        const st = L.map.getFeatureState({ source: `pk-${r}`, id: f.id });
        if (st.w > 0) wet++;
        if (st.w > f.properties.hc + 0.35) wrong++;            // water never above the column (hc is rounded: d to 0.01 m, hc to 0.1 m)
      }
      return { sampled: fs.length, wrong, wetAtLastStep: wet };
    });
    return { ...(await viewState(page)), stormMs, stepMs, stepsLookDifferent: !a.equals(b), ...check };
  });

  // 4. wells at street scale: mesh, depth rule, water rising
  await scenario("wells_street", { hash: "#map=15.4/29.7604/-95.3698" }, async (page) => {
    await page.click('[data-view="wells"]'); await settle(page); await sleep(1500);
    const mesh = await page.evaluate(async () => {
      const L = window.__lowpoints, m = L.map, b = m.getBounds();
      const r = [...L.views.wells.meshes.keys()][0];
      const mesh = await L.client.call("wellMesh", r, [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
      // the depth rule: every floor vertex of hexagon k sits at its depth when full (property d of pack k)
      const pk = await L.client.call("pack", r, 10);
      let floors = 0, wrong = 0;
      for (let i = 0; i < mesh.water.length; i += 5) {
        const k = mesh.water[i + 4], d = mesh.water[i + 2];
        floors++;
        if (Math.abs(d - pk.fc.features[k].properties.d) > 0.006) wrong++;
      }
      return { region: r, cells: mesh.cells, pitVertices: mesh.pit.length / 4, floorVertices: floors, floorDepthWrong: wrong };
    });
    const dry = await shot(page, "wells_dry.png");
    await page.evaluate((s) => window.__lowpoints.views.setStorm(s), STORM); await sleep(600);
    await page.evaluate(() => window.__lowpoints.views.setStep(2)); await sleep(1000);
    const low = await shot(page, "wells_step3.png");
    await page.evaluate(() => window.__lowpoints.views.setStep(15)); await sleep(1000);
    const high = await shot(page, "wells_step16.png");
    const water = await page.evaluate(() => ({ textures: window.__lowpoints.views.wells.waters.size }));
    // frame times while turning (software graphics)
    const frames = await page.evaluate(async () => {
      const m = window.__lowpoints.map, t = [];
      const on = () => t.push(performance.now());
      m.on("render", on);
      m.easeTo({ bearing: 60, duration: 2000 });
      await new Promise((r) => setTimeout(r, 2300));
      m.off("render", on);
      const g = t.slice(1).map((x, i) => x - t[i]).sort((a, b) => a - b);
      return { frames: t.length, medianMs: Math.round(g[g.length >> 1] || 0), p90Ms: Math.round(g[Math.floor(g.length * 0.9)] || 0) };
    });
    return { ...(await viewState(page)), mesh, ...water, waterChangesPicture: !dry.equals(high), stepsLookDifferent: !low.equals(high), frames };
  });

  // 5. wells chosen at city scale: says so, draws flat
  await scenario("wells_city_note", { hash: "#map=12.6/29.76/-95.37&v=wells" }, async (page) => viewState(page));

  // 6. selection survives view switches and a street-map swap; clicking a column selects
  await scenario("switch_keep_selection", { hash: "#map=14.6/29.7604/-95.3698" }, async (page) => {
    await page.click('[data-view="columns"]'); await settle(page);
    const c = await page.evaluate(() => {           // a point where a column is drawn
      const L = window.__lowpoints, m = L.map, ids = [...L.state.regions.keys()].map((r) => `pk-${r}-col`).filter((id) => m.getLayer(id));
      const r = m.getContainer().getBoundingClientRect();
      for (let y = 0.3; y < 0.9; y += 0.05) for (let x = 0.4; x < 0.9; x += 0.05) {
        const p = [r.width * x, r.height * y];
        if (m.queryRenderedFeatures(p, { layers: ids }).length) return [r.left + p[0], r.top + p[1]];
      }
      return [r.left + r.width * 0.6, r.top + r.height / 2];
    });
    await page.mouse.click(c[0], c[1]); await sleep(2500);
    const picked = await page.evaluate(() => window.__lowpoints.state.selected?.data?.id || null);
    await page.click('[data-view="wells"]'); await settle(page);
    await page.click("#basemap"); await sleep(2500); await settle(page);
    const afterSwap = await viewState(page);
    await page.click('[data-view="flat"]'); await settle(page);
    const end = await page.evaluate(() => ({ still: window.__lowpoints.state.selected?.data?.id || null }));
    await page.evaluate(() => localStorage.removeItem("lowpoints.basemap"));
    return { pickedInColumns: picked, wellsLayerAfterSwap: afterSwap.wellsLayer, meshesAfterSwap: afterSwap.wellsMeshes,
      selectionKept: picked != null && end.still === picked, flatPitch: (await viewState(page)).pitch };
  });

  // 7. compass
  await scenario("compass", { hash: "#map=13/29.76/-95.37/40/50&v=columns" }, async (page) => {
    const before = await viewState(page);
    const shown = await page.$eval("#compass", (b) => !b.hidden);
    await page.click("#compass"); await sleep(1200);
    const after = await viewState(page);
    return { shown, before: [before.pitch, before.bearing], after: [after.pitch, after.bearing], view: after.view };
  });

  // 8. phone: the cycle button
  await scenario("phone_cycle", { width: 390, height: 844, mobile: true, hash: "#map=14.6/29.7604/-95.3698" }, async (page) => {
    const seen = [];
    for (let k = 0; k < 3; k++) { await page.click("#view3d"); await sleep(1500); seen.push((await viewState(page)).view); }
    return { seen, desktopSwitchHidden: await page.$eval("#views-desktop", (e) => getComputedStyle(e).display === "none") };
  });

  // 9. hammering the switch while a storm plays
  await scenario("stress_switch", { hash: "#map=15/29.7604/-95.3698" }, async (page) => {
    await page.evaluate((s) => window.__lowpoints.views.setStorm(s), STORM);
    for (let k = 0; k < 24; k++) {
      await page.click(`[data-view="${["columns", "wells", "flat"][k % 3]}"]`);
      await page.evaluate((s) => window.__lowpoints.views.setStep(s), k % 16);
      await sleep(80);
    }
    await page.click('[data-view="wells"]'); await settle(page);
    await page.evaluate(() => window.__lowpoints.map.easeTo({ zoom: 12, duration: 300 })); await settle(page);
    await page.evaluate(() => window.__lowpoints.map.easeTo({ zoom: 15.5, duration: 300 })); await settle(page);
    return viewState(page);
  });
} finally {
  await browser.close();
  server.kill();
}
writeFileSync(join(outDir, "report.json"), JSON.stringify(report, null, 1));
