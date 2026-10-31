// Reading files from a list of hosts: the first that answers wins; one that fails is skipped
// for the rest of the visit (so a dead host costs one timeout, not one per request).

import { rangeFetch } from "./redirects.js";

const TIMEOUT_MS = 20000;

export class Hosts {
  constructor(hosts) {
    this.hosts = hosts.map((h) => ({ ...h, down: false }));
  }

  url(host, file) {
    return host.url.replace("{file}", file);
  }

  async _try(fn) {
    let last;
    const order = [...this.hosts.filter((h) => !h.down), ...this.hosts.filter((h) => h.down)];
    for (const host of order) {
      try {
        const out = await fn(host);
        host.down = false;
        return out;
      } catch (e) {
        if (e.name === "AbortError" && e.byCaller) throw e;
        host.down = true;
        last = e;
      }
    }
    throw last || new Error("no host");
  }

  async json(file) {
    return this._try(async (host) => {
      const r = await timed(this.url(host, file), {});
      if (!r.ok) throw new Error(`HTTP ${r.status} from ${host.name}`);
      return r.json();
    });
  }

  async range(file, start, length) {
    return this._try(async (host) => {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
      let r;
      try { r = await rangeFetch(this.url(host, file), start, length, ctrl.signal); } finally { clearTimeout(t); }
      if (r.status !== 206 && r.status !== 200) throw new Error(`HTTP ${r.status} from ${host.name}`);
      const all = new Uint8Array(await r.arrayBuffer());
      if (r.status === 200) return all.subarray(start, start + length);     // range ignored
      if (all.length !== length) throw new Error(`short read from ${host.name}`);
      return all;
    });
  }
}

async function timed(url, init) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal, mode: "cors", credentials: "omit" });
  } finally {
    clearTimeout(t);
  }
}
