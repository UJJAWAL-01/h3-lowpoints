// Lowpoints explorer: the page. Data work happens in the worker (src/worker.js).

import * as maplibregl from "maplibre-gl";
import { PMTiles, Protocol } from "pmtiles";
import { AUTO_LEVELS, BASEMAP_URL, DETAIL_LEVELS, DETAIL_ZOOM, HOME, LOCAL, MAX_REGION_SOURCES, OVERVIEW_LEVELS, VERSION, packHosts } from "./config.js";
import { DataClient } from "./data/client.js";
import { RedirectSource } from "./data/redirects.js";
import { addBaseLayers, addPackLayers, empty, removePackLayers, setVisible } from "./map/layers.js";
import { basemapStyle } from "./map/style.js";
import * as f from "./ui/format.js";
import * as panelView from "./ui/panel.js";
import { setupSearch } from "./ui/search.js";
import { setupSheet } from "./ui/sheet.js";

const $ = (id) => document.getElementById(id);
const isDetail = (res) => res >= 8;
const autoRes = (z) => AUTO_LEVELS.find((l) => z < l.below).res;

const state = {
  pinned: null,            // a level the visitor chose, or null for Auto
  res: null,               // level drawn now
  overviewKey: null,       // what the overview source holds
  regions: new Map(),      // region -> level drawn for it
  loading: 0,
  nat: null,
  view: "intro",           // what the panel shows: intro | cell | depression
  selected: null,          // {region, index, data}
  cell: null,              // {props, res}
};

// ------------------------------------------------------------------ map
const START_VIEW = param("map");          // read before the map writes its own position
maplibregl.setWorkerUrl(new URL("maplibre-gl-worker.js", location.href).href);
const protocol = new Protocol({ metadata: false });
protocol.add(new PMTiles(new RedirectSource(BASEMAP_URL)));
maplibregl.addProtocol("pmtiles", protocol.tile);

const client = new DataClient(new URL("worker.js", location.href).href,
  { hosts: packHosts(), local: new URL(LOCAL.data, location.href).href });
// start the first data while the map is still setting up
const warm = { overview4: client.call("overviewCells", 4, null), national: client.call("national") };
warm.overview4.catch(() => {}); warm.national.catch(() => {});

const map = new maplibregl.Map({
  container: "map", style: basemapStyle(), center: HOME.center, zoom: HOME.zoom,
  minZoom: 1.6, maxZoom: 17.5, maxBounds: [[-150, 10], [-45, 60]], hash: "map",
  attributionControl: false, dragRotate: false, pitchWithRotate: false, touchPitch: false,
  renderWorldCopies: false, fadeDuration: 150, cancelPendingTileRequestsWhileZooming: true,
  locale: { "ScrollZoomBlocker.CtrlMessage": "Use Ctrl + scroll to zoom the map" },
});
map.touchZoomRotate.disableRotation();
map.keyboard.disableRotation();
const scale = new maplibregl.ScaleControl({ maxWidth: 110, unit: f.units.us ? "imperial" : "metric" });
map.addControl(scale, "bottom-left");

let basemapWarned = false;
map.on("error", (e) => {
  const msg = String(e?.error?.message || "");
  if (!basemapWarned && (e.sourceId === "protomaps" || /pmtiles|protomaps/i.test(msg))) {
    basemapWarned = true;
    toast("The street map could not load. The depressions still work.");
  }
});

map.on("load", async () => {
  addBaseLayers(map);
  if (!START_VIEW) map.fitBounds([[-124.8, 24.4], [-66.9, 49.4]], { padding: fitPadding(true), duration: 0 });
  renderPanel();
  warm.national.then((nat) => { state.nat = nat; if (state.view === "intro") renderPanel(); })
    .catch(() => toast("The national figures could not load."));
  await refresh();
  const sel = param("d");
  if (sel) openFromLink(sel);
});

let moveTimer = 0;
map.on("moveend", () => { clearTimeout(moveTimer); moveTimer = setTimeout(refresh, 120); });
map.on("zoom", () => {
  const want = targetRes();
  if (want !== state.res && (isDetail(want) !== isDetail(state.res) || Math.abs(want - state.res) >= 1)) {
    clearTimeout(moveTimer); moveTimer = setTimeout(refresh, 60);
  }
  renderLevels();
});

function targetRes() {
  const auto = autoRes(map.getZoom());
  if (state.pinned != null && isDetail(state.pinned) === isDetail(auto)) return state.pinned;
  return auto;
}

function box() {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
}

// ------------------------------------------------------------------ drawing what the view needs
let refreshing = null, again = false;
async function refresh() {
  if (refreshing) { again = true; return refreshing; }
  refreshing = (async () => {
    do {
      again = false;
      const res = targetRes();
      const prev = state.res;
      state.res = res;
      if (isDetail(res)) await drawDetail(res, prev);
      else await drawOverview(res);
      if ((isDetail(res) !== isDetail(prev) || (res === 10) !== (prev === 10)) && state.view === "intro") renderPanel();
      renderLevels();
    } while (again);
  })();
  try { await refreshing; } finally { refreshing = null; }
}

async function drawOverview(res) {
  for (const r of state.regions.keys()) setVisible(map, [`pk-${r}-fill`, `pk-${r}-line`, `pk-${r}-hover`], false);
  setVisible(map, ["overview-fill", "overview-line", "overview-hover"], true);
  const b = box();
  const key = res === 6 ? `6:${b.map((x) => Math.round(x * 2) / 2).join(",")}` : String(res);
  if (key === state.overviewKey) return;
  busy(+1);
  try {
    let fc;
    if (res === 4 && warm.overview4) { fc = await warm.overview4; warm.overview4 = null; }
    else fc = await client.call("overviewCells", res, res === 6 ? b : null);
    if (state.res !== res) return;
    map.getSource("overview").setData(fc);
    state.overviewKey = key;
    if (!performance.getEntriesByName("lowpoints:first-hexagons").length) map.once("idle", () => performance.mark("lowpoints:first-hexagons"));
  } catch (e) {
    toast("The overview could not load.", refresh);
  } finally { busy(-1); }
}

async function drawDetail(res) {
  const regions = await client.call("regionsIn", box());
  const want = new Set(regions);
  // keep the overview under the hexagons until the first pack has arrived
  let shown = 0;
  for (const [r, lvl] of state.regions) {
    const on = want.has(r);
    setVisible(map, [`pk-${r}-fill`, `pk-${r}-line`, `pk-${r}-hover`], on);
    if (on && lvl === res) shown++;
  }
  if (shown) setVisible(map, ["overview-fill", "overview-line", "overview-hover"], false);
  const todo = regions.filter((r) => state.regions.get(r) !== res);
  if (!todo.length) return;
  busy(+todo.length);
  await Promise.all(todo.map(async (r) => {
    try {
      const out = await client.call("pack", r, res);
      if (state.res !== res) return;
      if (out.empty) { state.regions.set(r, res); return; }
      addPackLayers(map, r);
      map.getSource(`pk-${r}`).setData(out.fc);
      setVisible(map, [`pk-${r}-fill`, `pk-${r}-line`, `pk-${r}-hover`], true);
      state.regions.set(r, res);
      setVisible(map, ["overview-fill", "overview-line", "overview-hover"], false);
      if (!performance.getEntriesByName("lowpoints:first-pack").length) map.once("idle", () => performance.mark("lowpoints:first-pack"));
    } catch (e) {
      toast("Some depressions could not load (both data hosts failed).", refresh);
    } finally { busy(-1); }
  }));
  prune(want);
}

function prune(want) {
  if (state.regions.size <= MAX_REGION_SOURCES) return;
  const c = map.getCenter();
  const far = [...state.regions.keys()].filter((r) => !want.has(r));
  for (const r of far.slice(0, state.regions.size - MAX_REGION_SOURCES)) {
    removePackLayers(map, r);
    state.regions.delete(r);
  }
  void c;
}

function busy(d) {
  state.loading = Math.max(0, state.loading + d);
  const el = $("progress");
  el.style.setProperty("--o", state.loading ? 1 : 0);
  el.style.setProperty("--p", state.loading ? Math.max(0.15, 1 / (1 + state.loading)) : 1);
}

// ------------------------------------------------------------------ hover and click
const tip = document.createElement("div");
tip.className = "hover-tip";
tip.hidden = true;
document.body.appendChild(tip);
let hovered = null;

function interactive() {
  const ids = ["overview-fill"];
  for (const r of state.regions.keys()) if (map.getLayer(`pk-${r}-fill`)) ids.push(`pk-${r}-fill`);
  return ids;
}

function setHover(feat) {
  if (hovered && (!feat || hovered.source !== feat.source || hovered.id !== feat.id)) {
    map.setFeatureState({ source: hovered.source, id: hovered.id }, { hover: false });
    hovered = null;
  }
  if (feat && feat.id != null) {
    map.setFeatureState({ source: feat.source, id: feat.id }, { hover: true });
    hovered = { source: feat.source, id: feat.id };
  }
}

map.on("mousemove", (e) => {
  const feat = map.queryRenderedFeatures(e.point, { layers: interactive() })[0];
  setHover(feat);
  map.getCanvas().style.cursor = feat ? "pointer" : "";
  if (!feat || matchMedia("(hover: none)").matches) { tip.hidden = true; return; }
  const p = feat.properties;
  tip.textContent = feat.source === "overview"
    ? `${f.pct(p.s, p.s < 0.01 ? 2 : 1)} in depressions · ${f.int(p.c)} depressions`
    : p.s != null
    ? `${f.pct(p.s, p.s < 0.01 ? 2 : 1)} in depressions · ${p.n} ${p.n === 1 ? "depression" : "depressions"}`
    : `${f.depth(p.d).main} deep when full${p.n > 1 ? ` · ${p.n} depressions` : ""}`;
  tip.style.left = `${e.originalEvent.clientX}px`;
  tip.style.top = `${e.originalEvent.clientY}px`;
  tip.hidden = false;
});
map.getCanvas().addEventListener("mouseleave", () => { setHover(null); tip.hidden = true; });

map.on("click", (e) => {
  const feat = map.queryRenderedFeatures(e.point, { layers: interactive() })[0];
  if (!feat) { clearSelection(); return; }
  if (feat.source === "overview") {
    selectCell(feat);
  } else {
    selectDepression(feat.source.slice(3), Number(feat.properties.dep));
  }
  if (sheet.phone) map.easeTo({ center: e.lngLat, offset: [0, -window.innerHeight * 0.22], duration: reduced() ? 0 : 500 });
});

function selectCell(feat) {
  clearSelection(false);
  state.view = "cell";
  state.cell = { props: feat.properties, res: state.res, geometry: feat.geometry };
  map.getSource("cellsel").setData({ type: "Feature", geometry: feat.geometry, properties: {} });
  renderPanel();
  sheet.set("half");
}

let selectToken = 0;
async function selectDepression(region, index, { fly = false } = {}) {
  const token = ++selectToken;
  map.getSource("cellsel").setData(empty());
  state.view = "depression";
  state.selected = { region, index, data: null };
  $("panel-body").innerHTML = panelView.loading("Depression");
  sheet.set("half");
  try {
    const d = await client.call("depression", region, index);
    if (token !== selectToken) return;
    state.selected.data = d;
    map.getSource("selection").setData(d.cells);
    const pts = d.chain.geometry.coordinates.slice(1).map((c) => ({ type: "Feature", geometry: { type: "Point", coordinates: c }, properties: {} }));
    map.getSource("spill").setData({ type: "FeatureCollection", features: d.chainSteps ? [d.chain, ...pts] : [] });
    map.getSource("upstream").setData(d.upstreamPoints);
    setParam("d", `${region}.${d.id}`);
    renderPanel();
    if (fly) fitCells(d.cells);
  } catch (e) {
    if (token !== selectToken) return;
    $("panel-body").innerHTML = panelView.failed("This depression could not be loaded. Check your connection and try again.");
  }
}

function fitCells(fc) {
  let w = 180, s = 90, e = -180, n = -90;
  for (const ft of fc.features) for (const [x, y] of ft.geometry.coordinates[0]) {
    w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y);
  }
  if (w > e) return;
  map.fitBounds([[w, s], [e, n]], { padding: fitPadding(), maxZoom: 16, duration: reduced() ? 0 : 900 });
}

function fitPadding(peek = false) {
  if (sheet.phone) {
    const sheetH = peek ? parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--sheet-peek")) || 200 : window.innerHeight * 0.55;
    return { top: Math.round($("top").getBoundingClientRect().bottom + 12), bottom: Math.round(sheetH + 12), left: 12, right: 56 };
  }
  return { top: 60, bottom: 60, left: 400, right: 80 };
}

function clearSelection(render = true) {
  selectToken++;
  for (const s of ["selection", "spill", "upstream", "cellsel"]) map.getSource(s)?.setData(empty());
  state.selected = null; state.cell = null;
  setParam("d", null);
  if (render) { state.view = "intro"; renderPanel(); sheet.set("peek"); }
}

async function openFromLink(value) {
  const [region, id] = value.split(".");
  if (!/^[0-9a-f]{15}$/.test(region || "") || !/^\d+$/.test(id || "")) return;
  try {
    const i = await client.call("findDepression", region, id);
    if (i >= 0) selectDepression(region, i, { fly: !START_VIEW });
    else toast("That depression is not in the data.");
  } catch { toast("The linked depression could not be loaded."); }
}

// ------------------------------------------------------------------ panel
const sheet = setupSheet($("panel"), $("handle"));

function renderPanel() {
  const body = $("panel-body");
  if (state.view === "depression" && state.selected?.data) body.innerHTML = panelView.depression(state.selected.data);
  else if (state.view === "cell" && state.cell) body.innerHTML = panelView.overviewCell(state.cell.props, state.cell.res);
  else {
    const res = state.res ?? autoRes(map.getZoom());
    body.innerHTML = panelView.intro(state.nat, isDetail(res) ? "detail" : "overview", res);
  }
  measure();
}

$("panel").addEventListener("click", async (e) => {
  const el = e.target.closest("[data-act],[data-copy]");
  if (!el) return;
  if (el.dataset.copy) {
    try { await navigator.clipboard.writeText(el.dataset.copy); toast("Copied."); } catch { toast(el.dataset.copy); }
    return;
  }
  const act = el.dataset.act;
  if (act === "close") clearSelection();
  else if (act === "about") openAbout();
  else if (act === "zoom-dep" && state.selected?.data) fitCells(state.selected.data.cells);
  else if (act === "zoom-cell" && state.cell) {
    const ring = state.cell.geometry.coordinates[0];
    const xs = ring.map((p) => p[0]), ys = ring.map((p) => p[1]);
    map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]],
      { padding: fitPadding(), duration: reduced() ? 0 : 900 });
  } else if (act === "share") {
    try { await navigator.clipboard.writeText(location.href); toast("Link copied."); } catch { toast(location.href); }
  } else if (act === "retry" && state.selected) selectDepression(state.selected.region, state.selected.index);
});

// ------------------------------------------------------------------ controls
function renderLevels() {
  const res = state.res ?? autoRes(map.getZoom());
  const btn = (v, label, title) => `<button data-level="${v}" aria-pressed="${state.pinned === v || (v === "auto" && state.pinned == null)}" title="${title}">${label}</button>`;
  const html = [btn("auto", "Auto", "Choose the level from the zoom"),
    ...OVERVIEW_LEVELS.map((r) => btn(r, r, `H3 resolution ${r}: summaries`)), `<span class="sep" aria-hidden="true"></span>`,
    ...DETAIL_LEVELS.map((r) => btn(r, r, `H3 resolution ${r}: single depressions`))].join("");
  const el = $("levels");
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
  el.querySelectorAll("button[data-level]").forEach((b) => b.classList.toggle("now", Number(b.dataset.level) === res));
}

$("levels").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-level]");
  if (!b) return;
  const v = b.dataset.level === "auto" ? null : Number(b.dataset.level);
  state.pinned = v;
  if (v != null && isDetail(v) !== isDetail(autoRes(map.getZoom()))) {
    const z = isDetail(v) ? Math.max(DETAIL_ZOOM + 0.6, AUTO_LEVELS.find((l) => l.res === v).below - 1) : { 4: 4, 5: 6, 6: 8 }[v];
    map.easeTo({ zoom: z, duration: reduced() ? 0 : 700 });
  } else refresh();
  renderLevels();
});

$("zoom-in").onclick = () => map.zoomIn();
$("zoom-out").onclick = () => map.zoomOut();
$("home").onclick = () => { clearSelection(); map.flyTo({ ...HOME, duration: reduced() ? 0 : 1200 }); };
$("units").textContent = f.units.us ? "ft" : "m";
$("units").onclick = () => {
  const us = f.units.toggle();
  $("units").textContent = us ? "ft" : "m";
  scale.setUnit(us ? "imperial" : "metric");
  renderPanel();
};

let you = null;
$("locate").onclick = () => {
  if (!navigator.geolocation) { toast("This browser cannot share its location."); return; }
  $("locate").setAttribute("aria-pressed", "true");
  navigator.geolocation.getCurrentPosition((pos) => {
    const ll = [pos.coords.longitude, pos.coords.latitude];
    if (!you) { const el = document.createElement("div"); el.className = "you"; you = new maplibregl.Marker({ element: el }); }
    you.setLngLat(ll).addTo(map);
    map.flyTo({ center: ll, zoom: 13.6, duration: reduced() ? 0 : 1600 });
    $("locate").setAttribute("aria-pressed", "false");
  }, () => { toast("Location not available (permission denied or no signal)."); $("locate").setAttribute("aria-pressed", "false"); },
  { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
};

function openAbout() { $("about").showModal(); }
$("about-open").onclick = openAbout;
$("about-close").onclick = () => $("about").close();
$("about").addEventListener("click", (e) => { if (e.target === $("about")) $("about").close(); });
$("about-version").textContent = `Explorer ${VERSION} · dataset 0.3 · packs doi:10.5281/zenodo.23824290`;

setupSearch({ input: $("search"), list: $("results"), onPick: (p) => {
  clearSelection();
  map.flyTo({ center: [p.lon, p.lat], zoom: p.pop > 500000 ? 11.2 : p.pop > 50000 ? 12 : 12.8, duration: reduced() ? 0 : 1800 });
} });

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("about").open && state.view !== "intro") clearSelection();
  if (e.key === "/" && document.activeElement !== $("search")) { e.preventDefault(); $("search").focus(); }
});

// ------------------------------------------------------------------ small helpers
function reduced() { return matchMedia("(prefers-reduced-motion: reduce)").matches; }

let toastTimer = 0;
function toast(msg, retry) {
  const el = $("toast");
  el.innerHTML = `${f.esc(msg)}${retry ? `<button type="button">Retry</button>` : ""}`;
  if (retry) el.querySelector("button").onclick = () => { el.classList.remove("show"); retry(); };
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), retry ? 8000 : 3500);
}

function hashParams() { return new URLSearchParams(location.hash.slice(1)); }
function param(k) { return hashParams().get(k); }
function setParam(k, v) {
  const p = hashParams();
  if (v == null) p.delete(k); else p.set(k, v);
  const s = p.toString().replace(/%2F/g, "/");
  history.replaceState(history.state, "", `${location.pathname}${location.search}${s ? `#${s}` : ""}`);
}

function measure() {
  const top = $("top").getBoundingClientRect().height;
  document.documentElement.style.setProperty("--top-h", `${Math.round(top)}px`);
  if (sheet.phone) {
    const body = $("panel-body");
    const first = body.querySelector(".stats, .say, .row");
    const peek = first ? Math.min(first.getBoundingClientRect().bottom - $("panel").getBoundingClientRect().top + 14, 230) : 140;
    document.documentElement.style.setProperty("--sheet-peek", `${Math.max(110, Math.round(peek))}px`);
  }
}
window.addEventListener("resize", measure);
renderLevels();
measure();

if (["localhost", "127.0.0.1"].includes(location.hostname)) window.__lowpoints = { map, state, client, refresh, selectDepression };
