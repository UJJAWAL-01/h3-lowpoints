// Does the map still draw when Hugging Face is failing? Many loads, 6 at a time (busy CPU), with huggingface.co
// pointed at a local server that drops each connection after 0 to 1.5 s (a failure that arrives late is what
// used to stop the app starting; an instant refusal does not; request interception stalls workers' requests).
// The street map fails and the data falls back to Zenodo. Records the state of any load that gets stuck.
//   node app/tools/basemap_race.mjs [LOADS=36]     (fails if any load gets stuck)
import { spawn } from "node:child_process";
import { join, resolve } from "node:path";
import { createServer } from "node:net";
import puppeteer from "puppeteer-core";

const repo = resolve(import.meta.dirname, "../..");
const LOADS = Number(process.argv[2] || 36);
// stands in for a failing host: accepts a connection, waits 0 to 1.5 s, drops it
const sick = createServer((c) => { c.on("error", () => {}); setTimeout(() => c.destroy(), Math.random() * 1500); }).listen(8798, "127.0.0.1");
const app = spawn(process.execPath, [join(repo, "app/tools/serve.mjs"), join(repo, "docs"), "8797"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--host-resolver-rules=MAP huggingface.co 127.0.0.1:8798, MAP *.hf.co 127.0.0.1:8798"], protocolTimeout: 300000 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let done = 0, stuck = 0;
const stuckStates = [];
async function one(i) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  const open = new Set(), seen = [];
  page.on("request", (r) => { open.add(r.url()); seen.push(r.url().replace(/[?].*/, "").slice(-60)); });
  page.on("requestfinished", (r) => open.delete(r.url()));
  page.on("requestfailed", (r) => open.delete(r.url()));
  await page.setViewport({ width: 1280, height: 800 });
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank */ } });
  await page.goto("http://127.0.0.1:8797/explorer/", { waitUntil: "domcontentloaded" }).catch(() => {});
  const ok = await page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.overviewKey, { timeout: 90000 }).then(() => true).catch(() => false);
  done++;
  if (!ok) {
    stuck++;
    stuckStates.push({ open: [...open].map((u) => u.slice(0, 110)), overviewAsked: seen.filter((u) => /overview/.test(u)).length, n: seen.length });
    stuckStates.push(await page.evaluate(() => { const L = window.__lowpoints; return L ? { hooks: true, loaded: L.map.loaded(), styleLoaded: L.map.isStyleLoaded(), hasLayer: !!L.map.getLayer("overview-fill"), res: L.state.res, loading: L.state.loading, toast: document.getElementById("toast").innerText } : { hooks: false }; }).catch((e) => String(e)));
  }
  await ctx.close();
}
try {
  for (let i = 0; i < LOADS; i += 6) { await Promise.all(Array.from({ length: Math.min(6, LOADS - i) }, (_, k) => one(i + k))); console.log("done", done, "stuck", stuck); }
} finally { await browser.close(); app.kill(); sick.close(); }
console.log(JSON.stringify({ loads: done, stuck, stuckStates }));
process.exit(stuck ? 1 : 0);
