// 6.3e checks (plans/PHASE6_3e_SPEC.md section 4) in headless Chrome, data served locally.
//   node app/tools/storm_test.mjs CASES_JSON DATA_DIR OUT_DIR
// CASES_JSON from app/tools/storm_cases.py (the library's answers).
// Software graphics (SwiftShader): frame times here are not real-device numbers.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [casesFile, dataDir, outArg] = process.argv.slice(2);
const out = resolve(outArg);
mkdirSync(out, { recursive: true });
const cases = JSON.parse(readFileSync(casesFile, "utf8").replace(/: Infinity/g, ": 1e999").replace(/: NaN/g, ": null"));   // Python writes Infinity (rarer than 1,000 years)
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe"].find(existsSync);
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8772"], { stdio: "ignore" });
const data = spawn(process.execPath, [join(here, "serve.mjs"), resolve(dataDir), "8773"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const base = `http://127.0.0.1:8772/explorer/?packs=${encodeURIComponent("http://127.0.0.1:8773/{file}")}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rel = (a, b) => Math.abs(a - b) / Math.max(Math.abs(b), 1e-9);
const R = { errors: [] };

const page = await browser.newPage();
page.on("pageerror", (e) => R.errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") R.errors.push(`console: ${m.text()}`); });
await page.setViewport({ width: 1360, height: 860 });
await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank page */ } });
const ready = () => page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.regions.size > 0 && window.__lowpoints.state.loading === 0, { timeout: 120000, polling: 300 });
async function open(hash) { await page.goto("about:blank"); await page.goto(`${base}#${hash}`, { waitUntil: "domcontentloaded" }); await ready(); }
const call = (type, ...args) => page.evaluate((t, a) => window.__lowpoints.client.call(t, ...a), type, args);
// the storm bar settled: summary for the current storm, and every drawn pack's water loaded
const settled = () => page.waitForFunction(() => { const L = window.__lowpoints, b = L.stormBar;
  return b.summary && L.views.storm && L.state.loading === 0 && document.querySelector("[data-sv=fill], .say"); }, { timeout: 60000, polling: 250 });

try {
  await open("map=14.6/29.761/-95.370");

  // ---------------------------------------------------------------- 2. engine = library (views)
  R.check2 = [];
  for (const v of cases.views) {
    const regions = await call("regionsIn", v.box, 0);
    const sm = await call("stormSummary", regions, v.box, v.storm, "all");
    let worstCount = 0, worstRel = 0;
    for (let s = 0; s < 16; s++) {
      for (const k of ["fill", "overflow", "holdWater"]) worstCount = Math.max(worstCount, Math.abs(sm.byStep[s][k] - v.byStep[s][k]));
      for (const k of ["wetAreaM2", "heldM3"]) if (v.byStep[s][k] > 0 || sm.byStep[s][k] > 0) worstRel = Math.max(worstRel, rel(sm.byStep[s][k], v.byStep[s][k]));
    }
    const firstOk = JSON.stringify(sm.first.map((x) => x.id)) === JSON.stringify(v.first.map((x) => x.id));
    const rainOk = v.rainMin == null ? !Number.isFinite(sm.rainMin) : rel(sm.rainMin, v.rainMin) < 1e-6 && rel(sm.rainMax, v.rainMax) < 1e-6;
    const ok = sm.depressions === v.depressions && sm.unknown === v.unknown && worstCount === 0 && worstRel < 1e-6 && firstOk && rainOk;
    R.check2.push({ name: v.name, storm: v.storm.kind, depressions: sm.depressions, worstCount, worstRel: +worstRel.toExponential(2), firstOk, rainOk, ok });
  }

  // ---------------------------------------------------------------- 3. rarity readout = library rule
  R.check3 = [];
  for (const c of cases.rarity) {
    const got = Number(await page.evaluate((a) => window.__lowpoints.client.call("rarityAt", ...a).then((r) => String(r.returnPeriod)), [c.lat, c.lon, c.hours, c.mm]));
    const want = c.years;
    const ok = got === want || (Number.isFinite(want) && rel(got, want) < 1e-6);
    R.check3.push({ name: c.name, mm: c.mm, hours: c.hours, want, got, ok });
  }

  // ---------------------------------------------------------------- 1. panel = engine (3 places x 2 storms, 3 steps)
  R.check1 = [];
  const places = [["houston", "map=14.6/29.761/-95.370"], ["new_york", "map=14.6/40.709/-73.999"], ["chicago", "map=14.2/41.878/-87.63"]];
  const panelStorms = ["s=r.10.24", "s=a.75.3&g=dry"];
  for (const [name, hash] of places) {
    for (const st of panelStorms) {
      await open(`${hash}&${st}`);
      await settled(); await sleep(400);
      for (const step of [3, 9, 15]) {
        await page.evaluate((k) => window.__lowpoints.stormBar.setStep(k), step);
        await sleep(150);
        const got = await page.evaluate(() => {
          const t = (k) => document.querySelector(`[data-sv="${k}"]`)?.textContent.trim();
          const sm = window.__lowpoints.stormBar.summary, s = window.__lowpoints.stormBar.step;
          return { fill: t("fill"), overflow: t("overflow"), hold: t("hold"), area: t("area"), held: t("held"),
            want: s < 0 ? null : sm.byStep[s], n: sm.depressions, sentence: t("sentence") };
        });
        const fmt = (x) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Math.round(x));
        const ok = got.want && got.fill === fmt(got.want.fill) && got.overflow === fmt(got.want.overflow) && got.hold === fmt(got.want.holdWater)
          && got.sentence.includes(`${fmt(got.want.fill)} of the`);
        R.check1.push({ place: name, storm: st, step, ok, shown: [got.fill, got.overflow, got.hold], engine: got.want && [got.want.fill, got.want.overflow, got.want.holdWater] });
      }
    }
  }

  // ---------------------------------------------------------------- 4. map water = engine, and pixels
  await open("map=15.2/29.7604/-95.3698&s=r.10.24");
  await settled(); await sleep(1500);
  R.check4 = await page.evaluate(async () => {
    const L = window.__lowpoints, w = L.views.water, res = L.state.res, out = { compared: 0, mismatches: 0, steps: [2, 8, 15] };
    for (const [r, t] of w.waters) {
      const lv = await L.client.call("stormLevel", r, res, L.stormBar.storm, L.state.show);
      const K = lv.values.length / 16;
      for (let k = 0; k < K; k += Math.max(1, Math.floor(K / 200))) {
        for (const s of out.steps) {
          const v = lv.values[k * 16 + s], d = t.data[(Math.floor(k / 1024) * 16 + s) * 1024 + (k % 1024)];
          const want = Number.isNaN(v) ? -1 : v > 0 ? v : 0;
          out.compared++;
          if (Math.abs(d - Math.fround(want)) > 1e-6) out.mismatches++;
        }
      }
    }
    return out;
  });
  // pixels: a wet and a dry hexagon in view, read from the map's frame
  // pixels: the wettest hexagon in view, read from the map's frame before the rain and at the end
  R.check4.pixels = await page.evaluate(async () => {
    const L = window.__lowpoints, map = L.map, res = L.state.res, r = [...L.views.water.waters.keys()][0];
    const lv = await L.client.call("stormLevel", r, res, L.stormBar.storm, L.state.show);
    const pk = await L.client.call("pack", r, res, L.state.show);
    const c = map.getCanvas(), W = c.clientWidth, H = c.clientHeight;
    let best = null;
    for (const ft of pk.fc.features) {
      const v = lv.values[ft.id * 16 + 15], ring = ft.geometry.coordinates[0];
      const cx = ring.slice(0, 6).reduce((a, p) => a + p[0], 0) / 6, cy = ring.slice(0, 6).reduce((a, p) => a + p[1], 0) / 6;
      const p = map.project([cx, cy]);
      if (p.x < 420 || p.x > W - 80 || p.y < 140 || p.y > H - 280) continue;
      if (!best || v > best.v) best = { p, v };
    }
    const read = (pt) => new Promise((res2) => {
      map.once("render", () => {
        const gl = map.painter.context.gl, px = new Uint8Array(4), dpr = window.devicePixelRatio || 1;
        gl.readPixels(Math.round(pt.x * dpr), Math.round(c.height - pt.y * dpr), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
        res2([...px]);
      });
      map.triggerRepaint();
    });
    const wait = () => new Promise((x) => setTimeout(x, 300));
    L.stormBar.setStep(-1); await wait();
    const before = await read(best.p);
    L.stormBar.setStep(15); await wait();
    const after = await read(best.p);
    return { v: best.v, before, after };
  });
  const blue = (c) => c && c[2] > c[0] + 40 && c[2] > c[1] + 20;
  R.check4.ok = R.check4.mismatches === 0 && R.check4.compared > 0 && R.check4.pixels.v > 0 && blue(R.check4.pixels.after) && !blue(R.check4.pixels.before);
  await page.screenshot({ path: join(out, "flat_end.png") });

  // ---------------------------------------------------------------- 5. timeline
  R.check5 = {};
  const labels = await page.evaluate(async () => {
    const b = window.__lowpoints.stormBar, outL = [];
    for (const k of [-1, 0, 7, 15]) { b.setStep(k); await new Promise((r) => setTimeout(r, 60)); outL.push([k, b.nowText(), b.centreDepth()]); }
    return outL;
  });
  R.check5.labels = labels.map(([k, text, d]) => {
    const want = Math.round((d * (k + 1)) / 16), got = Number(text.match(/: ([\d,]+) mm/)[1].replace(/,/g, ""));
    return { step: k, text, ok: Math.abs(got - want) <= 1 };
  });
  const t0 = Date.now();
  await page.click('[data-st="play"]');
  await page.waitForFunction(() => document.querySelector('[data-st="play"]').textContent === "Replay", { timeout: 20000, polling: 50 });
  R.check5.playSeconds = (Date.now() - t0) / 1000;
  await page.click('[data-st="play"]'); await sleep(1500);
  await page.click('[data-st="play"]');                      // pause
  const p1 = await page.evaluate(() => window.__lowpoints.stormBar.pos); await sleep(800);
  const p2 = await page.evaluate(() => window.__lowpoints.stormBar.pos);
  R.check5.pauseHolds = p1 === p2 && p1 > -1 && p1 < 15;
  R.check5.ticks = await page.evaluate(() => { const a = (window.__stormTicks || []).slice().sort((x, y) => x - y); window.__stormTicks = []; return { n: a.length, medianMs: +(a[a.length >> 1] || 0).toFixed(2), p95Ms: +(a[Math.floor(a.length * 0.95)] || 0).toFixed(2) }; });
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.evaluate(() => window.__lowpoints.stormBar.setStep(3));
  await page.click('[data-st="play"]'); await sleep(200);
  R.check5.reducedJumps = await page.evaluate(() => window.__lowpoints.stormBar.pos === 15 && !window.__lowpoints.stormBar.playing);
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "no-preference" }]);
  R.check5.ok = R.check5.labels.every((x) => x.ok) && R.check5.playSeconds >= 5 && R.check5.playSeconds <= 8 && R.check5.pauseHolds && R.check5.reducedJumps;

  // ---------------------------------------------------------------- 6. gauge = engine
  R.check6 = [];
  const deps = await page.evaluate(async () => {
    const L = window.__lowpoints, r = [...L.state.regions.keys()][0], pk = await L.client.call("pack", r, 10, "all");
    return { r, idx: [...new Set(pk.fc.features.map((f) => f.properties.dep))].slice(0, 10) };
  });
  for (const i of deps.idx) {
    await page.evaluate((r, k) => window.__lowpoints.selectDepression(r, k), deps.r, i);
    await page.waitForFunction(() => document.querySelector("[data-gauge-body]"), { timeout: 30000, polling: 200 }).catch(() => {});
    for (const step of [2, 8, 15]) {
      await page.evaluate((k) => window.__lowpoints.stormBar.setStep(k), step);
      await sleep(80);
      const got = await page.evaluate(async (r, k, s) => {
        const L = window.__lowpoints, g = await L.client.call("stormDepression", r, k, L.stormBar.storm);
        return { text: document.querySelector("[data-gauge-body]")?.innerText || "", eng: g.steps[s], ground: g.groundM, rain: g.rainMm };
      }, deps.r, i, step);
      if (!Number.isFinite(got.rain) || Number.isNaN(got.eng.full)) { R.check6.push({ i, step, ok: /Not known/.test(got.text) }); continue; }
      const pct = Number((got.text.match(/(\d+)% full/) || [])[1]);
      const depth = (got.text.match(/: ([\d.]+) m deep/) || [])[1];
      const surf = (got.text.match(/water surface ([\d.,-]+) m/) || [])[1];
      const okPct = Math.abs(pct - Math.min(100, got.eng.full * 100)) <= 0.5;
      const okDepth = got.eng.depth > 0 ? Math.abs(Number(depth) - got.eng.depth) <= 0.005 + got.eng.depth * 0.005 : /: dry/.test(got.text);
      const okSurf = got.eng.depth > 0 && Number.isFinite(got.ground) ? Math.abs(Number(String(surf).replace(/,/g, "")) - (got.ground + got.eng.depth)) <= 0.051 : true;
      const okOver = /overflowing/.test(got.text) === got.eng.overflowing;
      R.check6.push({ i, step, ok: okPct && okDepth && okSurf && okOver, pct, want: +(got.eng.full * 100).toFixed(2), depth, surf });
    }
  }

  // ---------------------------------------------------------------- 7. links
  await open("map=15/29.7604/-95.3698&s=a.130.6&g=wet&t=5&v=columns");
  await settled(); await sleep(500);
  R.check7 = { restore: await page.evaluate(() => { const L = window.__lowpoints, s = L.stormBar.storm;
    return { kind: s.kind, mm: s.mm, hours: s.hours, condition: s.condition, step: L.stormBar.step, view: L.views.view }; }) };
  R.check7.restoreOk = R.check7.restore.kind === "amount" && R.check7.restore.mm === 130 && R.check7.restore.hours === 6
    && R.check7.restore.condition === "wet" && R.check7.restore.step === 4 && R.check7.restore.view === "columns";
  const broken = ["s=x.1.24", "s=r.3.24", "s=r.10.5", "s=a.0.24", "s=a.-5.24", "s=a.5000.24", "s=a.abc.24", "s=r.10.24&g=soggy",
    "s=r.10.24&t=99", "s=r.10.24&t=1.5", "s=", "s=r..24"];
  R.check7.broken = [];
  for (const b of broken) {
    const before = R.errors.length;
    await open(`map=14.5/29.7604/-95.3698&${b}`); await sleep(1500);
    const st = await page.evaluate(() => ({ storm: window.__lowpoints.stormBar.storm, toast: document.getElementById("toast").textContent }));
    R.check7.broken.push({ link: b, errors: R.errors.length - before, storm: st.storm ? `${st.storm.kind}` : null, toast: st.toast.slice(0, 70) });
  }
  R.check7.ok = R.check7.restoreOk && R.check7.broken.every((x) => x.errors === 0);

  // ---------------------------------------------------------------- 8. speed (storm change to drawn)
  await open("map=15/29.7604/-95.3698&s=r.10.24");
  await settled(); await sleep(800);
  R.check8 = await page.evaluate(async () => {
    const L = window.__lowpoints, b = L.stormBar, t0 = performance.now();
    document.querySelector('[data-pick="100"]').click();
    await new Promise((res) => { const go = () => { const st = L.views.storm; const w = L.views.water;
      if (st && st.storm.years === 100 && b.summary && [...L.state.regions.keys()].every((r) => !w.meshOf(r) || w.hasWater(r, L.state.res, st.gen))) res(); else setTimeout(go, 10); }; go(); });
    return { stormToDrawnMs: Math.round(performance.now() - t0) };
  });
  R.check8.ok = R.check8.stormToDrawnMs < 1000 && R.check5.ticks.medianMs < 16;

  // ---------------------------------------------------------------- 9. wording
  R.check9 = await page.evaluate(() => {
    const t = document.getElementById("panel-body").innerHTML;
    const need = ["storm is a total", "rain fallen, not time", "No storm drains", "soaking away", "ground before the storm",
      "30 m elevation model", "Likely drained depressions fill like the rest", "16 exact steps", "evidence.html", 'data-act="disclaimer"'];
    return { missing: need.filter((s) => !t.includes(s)) };
  });
  R.check9.ok = R.check9.missing.length === 0;

  // ---------------------------------------------------------------- 10. accessibility (axe) with the storm open
  const axe = readFileSync(join(here, "..", "node_modules", "axe-core", "axe.min.js"), "utf8");
  await page.evaluate(axe);                 // (the page's CSP blocks inline scripts; this runs it through the debugger)
  const audit = () => page.evaluate(async () => (await window.axe.run(document, { resultTypes: ["violations"], rules: { region: { enabled: false } } }))
    .violations.map((v) => `${v.id}: ${v.nodes.length}`));
  R.check10 = { result: await audit() };
  const dep = await page.evaluate(() => document.querySelector('[data-act="open-dep"]') ? 1 : 0);
  if (dep) { await page.click('[data-act="open-dep"]'); await page.waitForFunction(() => document.querySelector("[data-gauge-body]"), { timeout: 30000 }).catch(() => {}); await sleep(500); }
  R.check10.gauge = await audit();
  R.check10.ok = R.check10.result.length === 0 && R.check10.gauge.length === 0;
} catch (e) {
  R.errors.push(`test: ${e.message}`);
} finally {
  await browser.close();
  app.kill(); data.kill();
}
R.pass = {
  1: !!R.check1 && R.check1.every((x) => x.ok), 2: !!R.check2 && R.check2.every((x) => x.ok), 3: !!R.check3 && R.check3.every((x) => x.ok),
  4: !!R.check4?.ok, 5: !!R.check5?.ok, 6: !!R.check6 && R.check6.length > 0 && R.check6.every((x) => x.ok), 7: !!R.check7?.ok,
  8: !!R.check8?.ok, 9: !!R.check9?.ok, axe: !!R.check10?.ok, noErrors: R.errors.length === 0,
};
writeFileSync(join(out, "report.json"), JSON.stringify(R, null, 1));
console.log(JSON.stringify({ pass: R.pass, errors: R.errors.slice(0, 5) }, null, 1));
