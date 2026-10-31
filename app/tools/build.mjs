// Build the explorer into docs/explorer/ (served by GitHub Pages with the documentation).
//   node app/tools/build.mjs [--watch]
import { build, context } from "esbuild";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const app = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(app, "..", "docs", "explorer");
const watch = process.argv.includes("--watch");

const js = {
  entryPoints: { app: join(app, "src", "main.js"), worker: join(app, "src", "worker.js") },
  outdir: out, bundle: true, format: "esm", minify: true, target: ["es2022"], legalComments: "eof",
  sourcemap: false, logLevel: "warning", define: { "process.env.NODE_ENV": '"production"' },
};
const css = {
  entryPoints: { app: join(app, "styles", "entry.css") }, outdir: out, bundle: true, minify: true,
  external: ["*.woff2"], logLevel: "warning",
};

function copyStatic() {
  cpSync(join(app, "public"), out, { recursive: true });
  cpSync(join(app, "node_modules", "maplibre-gl", "dist", "maplibre-gl-worker.mjs"), join(out, "maplibre-gl-worker.js"));
  const hash = createHash("sha256").update(readFileSync(join(out, "app.js"))).update(readFileSync(join(out, "app.css")))
    .digest("hex").slice(0, 10);
  writeFileSync(join(out, "index.html"), readFileSync(join(app, "index.html"), "utf8").replaceAll("__VERSION__", hash));
  return hash;
}

function sizes() {
  const rows = [];
  const walk = (d) => readdirSync(d).forEach((n) => {
    const p = join(d, n);
    if (statSync(p).isDirectory()) return walk(p);
    const b = readFileSync(p);
    rows.push([relative(out, p).replaceAll("\\", "/"), b.length, /\.(js|css|html|json|pbf)$/.test(n) ? gzipSync(b, { level: 9 }).length : b.length]);
  });
  walk(out);
  const sum = (re) => rows.filter((r) => re.test(r[0])).reduce((a, r) => [a[0] + r[1], a[1] + r[2]], [0, 0]);
  const kb = ([a, b]) => `${(a / 1024).toFixed(0)} KB (${(b / 1024).toFixed(0)} KB gzipped)`;
  console.log(`js   ${kb(sum(/\.js$/))}  [app ${kb(sum(/^app\.js$/))}, worker ${kb(sum(/^worker\.js$/))}, map worker ${kb(sum(/^maplibre-gl-worker\.js$/))}]`);
  console.log(`css  ${kb(sum(/\.css$/))}`);
  console.log(`fonts ${kb(sum(/^fonts\/.*\.woff2$/))}, glyphs ${kb(sum(/^glyphs\//))}, data ${kb(sum(/^data\//))}`);
}

if (!existsSync(join(app, "styles", "entry.css"))) throw new Error("styles/entry.css missing");
if (watch) {
  mkdirSync(out, { recursive: true });
  const a = await context(js), b = await context(css);
  await Promise.all([a.watch(), b.watch()]);
  await Promise.all([a.rebuild(), b.rebuild()]);
  copyStatic();
  console.log(`watching; output in ${out}`);
} else {
  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  await build(js);
  await build(css);
  const hash = copyStatic();
  console.log(`built ${out} (version ${hash})`);
  sizes();
}
