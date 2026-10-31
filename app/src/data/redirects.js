// Hugging Face answers every file request with a redirect to its CDN. Remember where a URL
// redirects (until the signed link expires) and send later range requests straight there:
// one round trip per request instead of two.

const resolved = new Map();           // url -> { final, until }

function expiry(final) {
  const e = Number(new URL(final).searchParams.get("Expires"));
  return e > 0 ? e * 1000 - 120000 : Date.now() + 10 * 60000;
}

export async function rangeFetch(url, start, length, signal) {
  const init = { headers: { Range: `bytes=${start}-${start + length - 1}` }, signal, mode: "cors", credentials: "omit" };
  const hit = resolved.get(url);
  let r;
  if (hit && hit.until > Date.now()) {
    r = await fetch(hit.final, init);
    if (r.status === 206 || r.status === 200) return r;
    resolved.delete(url);              // expired or refused: ask the original again
  }
  r = await fetch(url, init);
  if (r.redirected && r.url && r.url !== url) resolved.set(url, { final: r.url, until: expiry(r.url) });
  return r;
}

// A PMTiles source that reads through rangeFetch.
export class RedirectSource {
  constructor(url) { this.url = url; }
  getKey() { return this.url; }
  async getBytes(offset, length, signal) {
    const r = await rangeFetch(this.url, offset, length, signal);
    if (r.status !== 206 && r.status !== 200) throw new Error(`basemap: HTTP ${r.status}`);
    let data = await r.arrayBuffer();
    if (r.status === 200 && data.byteLength > length) data = data.slice(offset, offset + length);
    return { data, etag: r.headers.get("ETag") || undefined, cacheControl: r.headers.get("Cache-Control") || undefined,
      expires: r.headers.get("Expires") || undefined };
  }
}
