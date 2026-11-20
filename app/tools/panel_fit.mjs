// The panel never scrolls sideways and nothing in it is cut: at several window widths, open the
// home panel, the rain simulator, a depression and the about dialog, and list anything that is
// wider than its box or sticks out of it.
//   node app/tools/panel_fit.mjs OUT_DIR [PACKS_TEMPLATE]
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const [outArg, packs] = process.argv.slice(2);
const out = resolve(outArg);
mkdirSync(out, { recursive: true });
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const app = spawn(process.execPath, [join(here, "serve.mjs"), join(here, "..", "..", "docs"), "8774"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const base = `http://127.0.0.1:8774/explorer/${packs ? `?packs=${encodeURIComponent(packs)}` : ""}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const report = { errors: [], cases: [] };

// the box scrolls sideways, or an element inside it sticks out of it (hidden helpers excepted)
const measure = (sel) => {
  const root = document.querySelector(sel);
  if (!root) return { missing: true };
  const rr = root.getBoundingClientRect(), bad = [];
  for (const el of root.querySelectorAll("*")) {
    if (el.closest(".visually-hidden") || el.closest("svg")) continue;
    if (getComputedStyle(el).display === "none") continue;
    const r = el.getBoundingClientRect();
    if (r.width > 0 && (r.right > rr.right + 0.5 || r.left < rr.left - 0.5))
      bad.push({ tag: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 30), text: (el.innerText || "").replace(/\s+/g, " ").slice(0, 50),
        left: Math.round(r.left - rr.left), right: Math.round(r.right - rr.right) });
  }
  return { boxW: Math.round(rr.width), scrollW: root.scrollWidth, clientW: root.clientWidth, sideways: root.scrollWidth > root.clientWidth + 1, bad: bad.slice(0, 8) };
};

try {
  const page = await browser.newPage();
  page.on("pageerror", (e) => report.errors.push(`pageerror: ${e.message}`));
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank */ } });
  const ready = () => page.waitForFunction(() => window.__lowpoints && window.__lowpoints.state.regions.size > 0 && window.__lowpoints.state.loading === 0,
    { timeout: 120000, polling: 300 });
  for (const [w, h] of [[1066, 942], [1360, 860], [760, 700], [390, 844]]) {
    await page.setViewport({ width: w, height: h });
    await page.goto("about:blank");   // a fresh load each time (the same address with a hash would not reload)
    await page.goto(`${base}#map=15.2/29.7604/-95.3698`, { waitUntil: "domcontentloaded" });
    await ready();
    const open = () => page.evaluate(() => document.querySelector(".panel")?.classList.add("open"));   // the phone sheet, pulled up
    const check = async (what) => { if (w < 720) { await open(); await sleep(400); }
      report.cases.push({ w, what, ...(await page.evaluate(measure, ".panel")) });
      await page.screenshot({ path: join(out, `${what.replace(/\W+/g, "_")}_${w}.png`) }); };
    await sleep(1000);
    await check("view panel");
    await page.evaluate(() => { document.querySelector(".panel")?.classList.remove("open"); document.getElementById("rain").click(); });
    await page.waitForFunction(() => document.querySelector("[data-sv=fill]"), { timeout: 90000, polling: 300 });
    await sleep(1500);
    await check("rain simulator");
    report.cases.push({ w, what: "storm bar", ...(await page.evaluate(measure, "#storm")) });
    await page.evaluate(() => document.querySelector('[data-act="storm-close"]').click());
    await sleep(800);
    const pick = await page.evaluate(async () => {
      const L = window.__lowpoints;
      for (const r of L.state.regions.keys()) {
        const p = await L.client.call("pack", r, 10, "all");
        const ft = p && !p.empty && p.fc.features.find((f) => f.properties.eg);   // one with a grade, so the evidence block shows
        if (ft) { L.selectDepression(r, ft.properties.dep); return true; }
      }
      return false;
    });
    if (pick) {
      await page.waitForFunction(() => window.__lowpoints.state.selected?.data, { timeout: 60000, polling: 300 });
      await sleep(800);
      await check("depression");
    } else report.errors.push(`no graded depression found at ${w}`);
  }
  // the about dialog, at the evidence table
  await page.setViewport({ width: 1066, height: 942 });
  await page.evaluate(() => document.getElementById("about-open").click());
  await sleep(600);
  const t = await page.evaluate(() => { const t = document.querySelector("table.evidence");
    return t ? { display: getComputedStyle(t).display, background: getComputedStyle(t).backgroundColor } : null; });
  report.evidenceTable = t;
  if (!t || t.display !== "table") report.errors.push(`evidence table display ${t && t.display}`);
  report.cases.push({ w: 1066, what: "about dialog", ...(await page.evaluate(measure, "#about .inner")) });
} catch (e) { report.errors.push(String(e.message)); } finally {
  await browser.close();
  app.kill();
}
report.pass = report.errors.length === 0 && report.cases.length >= 13 && report.cases.every((c) => !c.missing && !c.sideways && c.bad.length === 0);
writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ pass: report.pass, errors: report.errors, evidenceTable: report.evidenceTable,
  cases: report.cases.map((c) => `${c.w} ${c.what}: box ${c.boxW} scroll ${c.scrollW}/${c.clientW}${c.bad?.length ? " BAD " + JSON.stringify(c.bad.slice(0, 2)) : ""}`) }, null, 1));
