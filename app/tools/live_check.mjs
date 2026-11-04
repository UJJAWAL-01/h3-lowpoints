// The public site, as a visitor gets it (no test hooks there): opens, draws, tour, flood reports, storm,
// Python dialog, on real hosts; desktop and phone.
//   node app/tools/live_check.mjs OUT_DIR [URL]
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import puppeteer from "puppeteer-core";

const out = resolve(process.argv[2]);
const url = process.argv[3] || "https://ujjawal-01.github.io/h3-lowpoints/explorer/";
mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"], protocolTimeout: 300000 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rep = {};
async function run(tag, viewport) {
  const r = { errors: [], http: [] };
  const page = await browser.newPage();
  page.on("pageerror", (e) => r.errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") r.errors.push(m.text().slice(0, 200)); });
  page.on("response", (x) => { if (x.status() >= 400) r.http.push(`${x.status()} ${x.url().slice(0, 120)}`); });
  await page.setViewport(viewport);
  await page.evaluateOnNewDocument(() => { try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* blank */ } });
  const t0 = Date.now();
  await page.goto(`${url}#map=14.6/40.54236/-74.17958`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-act="tour"]', { timeout: 60000 });
  r.tourButtons = await page.$$eval('[data-act="tour"]', (b) => b.map((x) => x.textContent));
  r.panelMs = Date.now() - t0;
  r.credit = await page.$eval(".credit", (e) => e.textContent);
  // storm and Python
  await page.evaluate(() => document.getElementById("rain").click());
  await page.waitForSelector("[data-sv=fill]", { timeout: 90000 });
  r.stormFull = await page.$eval("[data-sv=fill]", (e) => e.textContent);
  r.stormMs = Date.now() - t0;
  await page.evaluate(() => document.querySelector('[data-act="python-storm"]')?.click());
  await sleep(500);
  r.python = await page.evaluate(() => ({ open: document.getElementById("python").open, pin: /==0\.5\.1/.test(document.getElementById("python-code").textContent) }));
  await page.evaluate(() => document.getElementById("python-close").click());
  // flood reports
  await page.evaluate(() => document.getElementById("reports").click());
  await page.waitForFunction(() => !document.getElementById("reports-key").hidden, { timeout: 20000 });
  r.reportsKey = /913 reports/.test(await page.$eval("#reports-key", (e) => e.innerText));
  await sleep(1500);
  await page.screenshot({ path: join(out, `live_${tag}.png`) });
  await page.evaluate(() => document.getElementById("reports").click());
  // a tour from its link
  await page.goto("about:blank");
  await page.goto(`${url}#tour=austin`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => /10,570,791/.test(document.getElementById("tour-text")?.textContent || ""), { timeout: 90000 });
  r.tourCaption = await page.$eval("#tour-chapter", (e) => e.textContent);
  await page.waitForFunction(() => /2 of 12/.test(document.getElementById("tour-chapter")?.textContent || ""), { timeout: 90000 });
  r.tourAdvanced = true;
  await page.keyboard.press("Escape"); await sleep(400);
  r.tourExit = !(await page.$(".tour"));
  await page.close();
  r.pass = !r.errors.length && !r.http.length && r.tourButtons.length === 2 && r.python.open && r.python.pin && r.reportsKey && r.tourAdvanced && r.tourExit;
  return r;
}
try {
  rep.desktop = await run("desktop", { width: 1440, height: 900 });
  rep.phone = await run("phone", { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
} finally { await browser.close(); }
writeFileSync(join(out, "live_check.json"), JSON.stringify(rep, null, 1));
console.log(JSON.stringify(rep));
process.exit(rep.desktop.pass && rep.phone.pass ? 0 : 1);
