// Place search from a list that ships with the app (US Census places): no outside service.
// The list (about 0.5 MB compressed) loads the first time the box is used.

import { LOCAL } from "../config.js";
import { esc, int } from "./format.js";

const MAX = 8;
let places = null, loading = null;

const norm = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();

async function load() {
  if (places) return places;
  if (!loading) {
    loading = fetch(`${LOCAL.data}places.json`).then((r) => {
      if (!r.ok) throw new Error(`places: HTTP ${r.status}`);
      return r.json();
    }).then((rows) => (places = rows.map(([name, st, lat, lon, pop]) => ({ name, st, lat, lon, pop, key: norm(name) }))));
  }
  return loading;
}

function find(q) {
  const qn = norm(q);
  if (!qn) return [];
  let name = qn, st = null;
  const m = /^(.*?)[ ,]+([a-z]{2})$/.exec(qn);          // "springfield il" or "springfield, il"
  if (m && m[1]) { name = m[1]; st = m[2].toUpperCase(); }
  const out = [];
  for (const p of places) {                             // largest first, so the first hits are the best
    if (st && p.st !== st) continue;
    if (p.key.startsWith(name) || (name.length > 2 && p.key.includes(` ${name}`))) {
      out.push(p);
      if (out.length >= 60) break;
    }
  }
  out.sort((a, b) => (b.key === name) - (a.key === name) || (b.key.startsWith(name)) - (a.key.startsWith(name)) || b.pop - a.pop);
  return out.slice(0, MAX);
}

export function setupSearch({ input, list, onPick }) {
  let items = [], active = -1;

  function render() {
    list.innerHTML = items.length
      ? items.map((p, i) => `<li role="option" id="opt-${i}" aria-selected="${i === active}" data-i="${i}">
          <span>${esc(p.name)}</span><span class="st">${esc(p.st)}</span>
          <span class="pop mono">${p.pop ? `${int(p.pop)} people` : ""}</span></li>`).join("")
      : `<li class="empty" role="option" aria-disabled="true">No US city or town by that name</li>`;
    const show = input.value.trim().length > 0;
    list.hidden = !show;
    input.setAttribute("aria-expanded", String(show));
    input.setAttribute("aria-activedescendant", active >= 0 ? `opt-${active}` : "");
  }

  async function update() {
    try { await load(); } catch { list.innerHTML = `<li class="empty">Place list could not be loaded</li>`; list.hidden = false; return; }
    items = find(input.value);
    active = items.length ? 0 : -1;
    render();
  }

  function pick(i) {
    const p = items[i];
    if (!p) return;
    input.value = `${p.name}, ${p.st}`;
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.blur();
    onPick(p);
  }

  input.addEventListener("focus", () => { load().catch(() => {}); });
  input.addEventListener("input", update);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!items.length) return;
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(Math.max(active, 0));
    } else if (e.key === "Escape") {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
    }
  });
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-i]");
    if (li) { e.preventDefault(); pick(Number(li.dataset.i)); }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#search-box")) { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
  });
}
