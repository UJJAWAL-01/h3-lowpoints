// Load test (spec 6.4g) against the real data hosts.
//   node app/tools/load_test.mjs OUT_DIR [VISITS=20] [REPLAY=70] [reuse] [SPREAD_S]   (reuse: part A's saved traces; SPREAD_S: visits arrive over this many seconds, 0 = all at once)
// A  VISITS scripted visits in headless Chrome (4 at a time), app from a local server standing in
//    for GitHub Pages, data and basemap from the pinned hosts: every request recorded (host, range,
//    status, bytes, time) and the time to each stage (city view, street view, storm, depression).
// B  The recorded requests of REPLAY visits replayed at once with fetch (no browser, so the
//    laptop's graphics do not limit it): status, 429s, time per request and per visit.
// C  Zenodo alone: 3 visits reading the data from Zenodo only (the app's local test setting ?packs=),
//    as when Hugging Face is down; requests to Zenodo per visit (its limit is about 133 per window per client, from its headers).
// Software graphics: stage times in A include slow rendering; B and C measure the hosts.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(process.argv[2]);
const VISITS = Number(process.argv[3] || 20), REPLAY = Number(process.argv[4] || 70), REUSE = process.argv[5] === "reuse", SPREAD = Number(process.argv[6] || 0);
mkdirSync(out, { recursive: true });
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8791"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const base = "http://127.0.0.1:8791/explorer/";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PLACES = [["new_york", 40.7128, -74.006], ["austin", 30.2672, -97.7431], ["chicago", 41.8781, -87.6298],
  ["miami", 25.7617, -80.1918], ["denver", 39.7392, -104.9903]];
const kind = (u) => /explorer-basemap/.test(u) ? "basemap (HF)" : /huggingface\.co\/datasets/.test(u) ? "data (HF)"
  : /hf\.co|huggingface|cdn-lfs|xethub/.test(u) ? "HF file servers (data and map tiles, after a redirect)"
  : /zenodo\.org/.test(u) ? "data (Zenodo)" : /127\.0\.0\.1/.test(u) ? "app (Pages)" : "other";
const stats = (xs) => { const s = xs.slice().sort((a, b) => a - b); return s.length ? { n: s.length, median: Math.round(s[s.length >> 1]), p95: Math.round(s[Math.floor(s.length * 0.95)]), max: Math.round(s[s.length - 1]) } : { n: 0 }; };

async function visit(browser, i, { blockHF = false } = {}) {
  const [name, lat, lon] = PLACES[i % PLACES.length];
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  const reqs = new Map(), errors = [];
  page.on("request", (r) => { if (!r.url().startsWith("data:")) reqs.set(r, { url0: (r.redirectChain()[0] || r).url(), url: r.url(), range: r.headers().range || null, t0: Date.now() }); });
  page.on("requestfinished", (r) => { const x = reqs.get(r); if (!x) return; const res = r.response(); x.status = res?.status(); x.bytes = Number(res?.headers()["content-length"] || 0); x.ms = Date.now() - x.t0; x.limit = res?.headers()["x-ratelimit-remaining"] ?? null; });
  page.on("requestfailed", (r) => { const x = reqs.get(r); if (x) { x.failed = r.failure()?.errorText; x.ms = Date.now() - x.t0; } });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewport({ width: 1280, height: 800 });
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank */ } });
  const t = {}, start = Date.now();
  const ready = () => page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.regions.size > 0 && window.__lowpoints.state.loading === 0, { timeout: 180000, polling: 250 });
  let ok = true;
  try {
    const q = blockHF ? `?packs=${encodeURIComponent("https://zenodo.org/api/records/23896025/files/{file}/content")}` : "";
    await page.goto(`${base}${q}#map=12/${lat}/${lon}`, { waitUntil: "domcontentloaded" });
    await ready(); t.city = Date.now() - start;
    await page.evaluate(([la, lo]) => window.__lowpoints.map.jumpTo({ center: [lo, la], zoom: 15.2 }), [lat, lon]);
    await sleep(500); await ready(); t.street = Date.now() - start;
    await page.click("#rain");
    await page.waitForFunction(() => document.querySelector("[data-sv=fill]") || /Not known|No depressions/.test(document.getElementById("panel-body").innerText), { timeout: 180000, polling: 250 });
    t.storm = Date.now() - start;
    const dep = await page.$('[data-act="open-dep"]');
    if (dep) { await dep.click(); await page.waitForFunction(() => document.querySelector("[data-gauge-body]"), { timeout: 120000, polling: 250 }); t.depression = Date.now() - start; }
  } catch (e) { ok = false; errors.push(String(e.message || e).slice(0, 200)); }
  await sleep(300);
  const hops = new Set([...reqs.keys()].flatMap((r) => r.redirectChain().slice(0, -1)));   // earlier hops of a redirect
  const list = [...reqs.entries()].filter(([r]) => !hops.has(r)).map(([, r]) => ({ ...r, kind: kind(r.url0) }));
  await ctx.close();
  return { i, place: name, ok, t, errors, requests: list };
}

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const report = { date: new Date().toISOString(), visits: VISITS, replay: REPLAY };
try {
  // A: scripted visits, 4 at a time
  const visits = [];
  if (REUSE && existsSync(join(out, "traces.json"))) visits.push(...JSON.parse(readFileSync(join(out, "traces.json"), "utf8")));
  for (let i = visits.length; i < VISITS; i += 4) {
    visits.push(...await Promise.all(Array.from({ length: Math.min(4, VISITS - i) }, (_, k) => visit(browser, i + k))));
    console.log("A visits done", visits.length, visits.slice(-4).map((v) => v.ok));
  }
  writeFileSync(join(out, "traces.json"), JSON.stringify(visits));
  const byKind = {};
  for (const v of visits) {
    const per = {};
    for (const r of v.requests) { const k = (per[r.kind] ||= { requests: 0, bytes: 0 }); k.requests++; k.bytes += r.bytes || 0; }
    for (const [k, x] of Object.entries(per)) { (byKind[k] ||= { requests: [], mb: [] }); byKind[k].requests.push(x.requests); byKind[k].mb.push(x.bytes / 1e6); }
  }
  report.A = {
    ok: visits.filter((v) => v.ok).length, errors: visits.flatMap((v) => v.errors),
    stageMs: Object.fromEntries(["city", "street", "storm", "depression"].map((s) => [s, stats(visits.map((v) => v.t[s]).filter(Number.isFinite))])),
    perVisit: Object.fromEntries(Object.entries(byKind).map(([k, x]) => [k, { requests: stats(x.requests), mb: { median: +x.mb.sort((a, b) => a - b)[x.mb.length >> 1].toFixed(2), max: +Math.max(...x.mb).toFixed(2) } }])),
    httpErrors: visits.flatMap((v) => v.requests.filter((r) => r.failed || r.status >= 400).map((r) => `${r.kind} ${r.status || r.failed}`)),
  };
  console.log("A", JSON.stringify(report.A));

  // B: replay REPLAY visits at once (host requests only; the app's own files are served by Pages)
  // a redirected request is recorded twice: the hop with its Range (status 3xx) and the final hop without one,
  // which must not be replayed (it would fetch the whole 1 GB data or 7.7 GB map file)
  const firstHop = (r) => !(/^https:\/\/huggingface\.co\//.test(r.url0) && (r.status === 200 || r.status === 206));
  const traces = visits.filter((v) => v.ok).map((v) => v.requests.filter((r) => r.kind !== "app (Pages)" && r.kind !== "other" && firstHop(r)));
  const replayOne = async (trace) => {
    const res = [], t0 = Date.now();
    // a browser fetches several at once: 6 at a time, in recorded order
    let next = 0;
    const worker = async () => {
      while (next < trace.length) {
        const r = trace[next++];
        if (r.kind === "app (Pages)" || r.kind === "other") continue;
        const s = Date.now();
        try {
          const resp = await fetch(r.url0, { headers: r.range ? { Range: r.range } : {}, signal: AbortSignal.timeout(30000) });
          const buf = await resp.arrayBuffer();
          res.push({ kind: r.kind, status: resp.status, ms: Date.now() - s, bytes: buf.byteLength });
        } catch (e) { res.push({ kind: r.kind, status: 0, ms: Date.now() - s, error: String(e.message || e).slice(0, 80) }); }
      }
    };
    await Promise.all(Array.from({ length: 6 }, worker));
    return { ms: Date.now() - t0, res };
  };
  const t0 = Date.now();
  const replays = await Promise.all(Array.from({ length: REPLAY }, async (_, k) => { await sleep((k / REPLAY) * SPREAD * 1000); return replayOne(traces[k % traces.length]); }));
  const all = replays.flatMap((r) => r.res);
  report.B = {
    seconds: (Date.now() - t0) / 1000, requests: all.length,
    spreadSeconds: SPREAD, errors: [...new Set(all.filter((r) => r.error).map((r) => r.error))].slice(0, 5),
    status: all.reduce((m, r) => ((m[`${r.kind} ${r.status}`] = (m[`${r.kind} ${r.status}`] || 0) + 1), m), {}),
    requestMs: Object.fromEntries([...new Set(all.map((r) => r.kind))].map((k) => [k, stats(all.filter((r) => r.kind === k).map((r) => r.ms))])),
    visitMs: stats(replays.map((r) => r.ms)), gb: +(all.reduce((s, r) => s + (r.bytes || 0), 0) / 1e9).toFixed(3),
  };
  console.log("B", JSON.stringify(report.B));

  // C: Zenodo fallback in the browser
  const fall = [];
  for (let i = 0; i < 3; i++) fall.push(await visit(browser, i, { blockHF: true }));
  writeFileSync(join(out, "traces_fallback.json"), JSON.stringify(fall));
  report.C = fall.map((v) => ({ place: v.place, ok: v.ok, t: v.t, errors: v.errors,
    zenodoRequests: v.requests.filter((r) => r.kind === "data (Zenodo)").length,
    zenodoStatus: v.requests.filter((r) => r.kind === "data (Zenodo)").reduce((m, r) => ((m[r.status || r.failed] = (m[r.status || r.failed] || 0) + 1), m), {}),
    lowestRateLimitRemaining: Math.min(...v.requests.filter((r) => r.limit != null).map((r) => Number(r.limit))) }));
  console.log("C", JSON.stringify(report.C));
} finally {
  await browser.close(); app.kill();
}
writeFileSync(join(out, "load_test.json"), JSON.stringify(report, null, 1));
