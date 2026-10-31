// Spec 6.1 check 1 and 4 in a real browser (headless Chrome on this machine, via puppeteer-core).
//   node app/tools/browser_test.mjs WORK [DATA_URL] [FALLBACK_URL]
// Without URLs it serves WORK locally (with range requests) and also tests the fallback by giving
// a dead first host. With URLs it reads the packs from them (e.g. Zenodo, then Hugging Face).
import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const work = resolve(process.argv[2]);
const CHROME = ["C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);

// a throwaway site: the app's modules and the test page, next to the data
const site = join(work, "site_test");
mkdirSync(site, { recursive: true });
cpSync(join(here, "..", "src"), join(site, "src"), { recursive: true });
cpSync(join(here, "..", "test"), join(site, "test"), { recursive: true });

const port = 8765, local = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, [join(here, "serve.mjs"), work, String(port)], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));

const runs = process.argv[3]
  ? [{ name: "hosts", data: process.argv[3], fallback: process.argv[4] || "", rarity: process.argv[5] || "" }]
  : [{ name: "local", data: local, fallback: "" },
     { name: "fallback", data: "http://127.0.0.1:9", fallback: local }];   // port 9: nothing listens

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true });
const report = {};
try {
  for (const run of runs) {
    const page = await browser.newPage();
    const q = new URLSearchParams({ data: run.data, fallback: run.fallback, ref: local, rarity: run.rarity || "" });
    await page.goto(`${local}/site_test/test/browser.html?${q}`);
    await page.waitForFunction(() => document.getElementById("out").textContent.includes('"done"'),
      { timeout: 300000 });
    report[run.name] = JSON.parse(await page.$eval("#out", (e) => e.textContent));
    await page.close();
  }
} finally {
  await browser.close();
  server.kill();
}
console.log(JSON.stringify({ browser: CHROME, ...report }, null, 1));
