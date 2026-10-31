// A small static server with HTTP range requests and CORS, for local testing of the explorer.
//   node app/tools/serve.mjs ROOT [PORT]          (ROOT can hold several folders, e.g. app/ and data/)
// Only for this machine: it listens on 127.0.0.1.
import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const root = resolve(process.argv[2] || ".");
const port = Number(process.argv[3] || 8765);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".bin": "application/octet-stream",
  ".svg": "image/svg+xml", ".woff2": "font/woff2", ".pmtiles": "application/octet-stream" };
export let requests = 0;

createServer((req, res) => {
  requests++;
  let path = normalize(join(root, decodeURIComponent(new URL(req.url, "http://x").pathname)));
  if (!path.startsWith(root)) { res.writeHead(403).end(); return; }
  let size;
  try {
    let st = statSync(path);
    if (st.isDirectory()) { path = join(path, "index.html"); st = statSync(path); }
    size = st.size;
  } catch { res.writeHead(404).end(); return; }
  const headers = { "Content-Type": TYPES[extname(path)] || "application/octet-stream",
    "Accept-Ranges": "bytes", "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Range", "Access-Control-Expose-Headers": "Content-Length, Content-Range" };
  if (req.method === "OPTIONS") { res.writeHead(204, headers).end(); return; }
  const m = /bytes=(\d+)-(\d*)/.exec(req.headers.range || "");
  if (m) {
    const start = Number(m[1]), end = m[2] ? Math.min(Number(m[2]), size - 1) : size - 1;
    res.writeHead(206, { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": end - start + 1 });
    createReadStream(path, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { ...headers, "Content-Length": size });
    createReadStream(path).pipe(res);
  }
}).listen(port, "127.0.0.1", () => console.log(`serving ${root} on http://127.0.0.1:${port}`));
