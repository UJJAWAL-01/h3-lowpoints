// The three views (Flat, Columns, Wells): switching, tilting and rotating in 3D, the wells layer,
// the stated exaggeration, and the storm's water in 3D (values from the worker's stormLevel).

import { DEFAULT_EXAGGERATION, EXAGGERATIONS, SHARE_CAP, SHARE_M_PER_PCT, VIEWS } from "../map/heights.js";
import { applyView, setColumnGlass, setExaggeration } from "../map/layers.js";
import { WaterLayer } from "../map/water.js";
import { WellsLayer } from "../map/wells.js";
import { viewBox } from "../map/viewbox.js";

const PITCH = 55;
const LABEL = { flat: "Flat", columns: "Columns", wells: "Wells" };

export function createViews({ map, client, state, toast, reduced, param, setParam, onChange }) {
  const wells = new WellsLayer();
  const water = new WaterLayer();     // the storm's water on the flat map
  let view = VIEWS.includes(param("v")) ? param("v") : "flat";
  let storm = null;                 // { storm, step, values: Map "region:level" -> Float32Array }
  const deps = new Map();           // region -> Int32Array: depression with most area, per drawn hexagon
  let selected = null;              // { region, index }
  // each 3D view keeps its own depth factor, both starting at x10 (a link's x= is for the view it opens)
  const linked = EXAGGERATIONS.includes(Number(param("x"))) ? Number(param("x")) : DEFAULT_EXAGGERATION;
  const factors = { columns: DEFAULT_EXAGGERATION, wells: DEFAULT_EXAGGERATION };
  factors[view === "wells" ? "wells" : "columns"] = linked;
  const factorKey = () => (view === "wells" ? "wells" : "columns");
  let exag = factors[factorKey()];
  wells.exag = exag;

  const is3D = () => view !== "flat";
  const effective = () => (view === "wells" && state.res !== 10 ? "flat" : view);
  const drawn = () => [...state.regions.keys()].filter((r) => map.getLayer(`pk-${r}-fill`)
    && map.getLayoutProperty(`pk-${r}-fill`, "visibility") !== "none");

  function ensureLayer() {
    if (!map.getLayer("wells") && map.getLayer("selection-fill")) map.addLayer(wells, "selection-fill");
    if (!map.getLayer(water.id) && map.getLayer("selection-fill")) { map.addLayer(water, "selection-fill"); water.placed = "selection-fill"; }
  }
  map.on("styledata", () => { if (map.getLayer("selection-fill")) ensureLayer(); });

  function interaction(on) {
    for (const h of [map.dragRotate, map.touchPitch]) on ? h.enable() : h.disable();
    on ? map.touchZoomRotate.enableRotation() : map.touchZoomRotate.disableRotation();
    on ? map.keyboard.enableRotation() : map.keyboard.disableRotation();
  }

  const scale = () => (exag === 1 ? "at true scale (×1)" : `exaggerated ×${exag}`);
  // what the legend says about heights, in plain words
  function note() {
    const v = effective(), res = state.res;
    if (view === "wells" && v === "flat") return "Wells show at street scale (the finest level). Zoom in, or let the map take you there.";
    if (v === "columns") {
      return res === 10
        ? `Column height = how deep water stands when full, ${scale()}. Colour: depth (sand shallow, brown deep).${storm ? " Blue inside: the water at this point in the storm." : ""}`
        : `Column height: 1% of the hexagon in depressions = ${SHARE_M_PER_PCT[res]} m (shares above ${SHARE_CAP * 100}% drawn at ${SHARE_CAP * 100}%). Heights are not real.`;
    }
    if (v === "wells") return `Well depth = how deep water stands when full, ${scale()}, measured down from each depression's rim (the level where water spills out). Colour: depth.${storm ? " Blue: the water at this point in the storm." : ""}`;
    return "";
  }

  function render() {
    ensureLayer();
    // the overview's columns belong to the overview levels only (they are kilometres tall: at
    // street scale they would stand over everything)
    const detail = state.res >= 8;
    const sources = detail ? drawn().map((r) => `pk-${r}`) : ["overview"];
    applyView(map, effective(), sources);
    if (detail && map.getLayer("overview-col")) map.setLayoutProperty("overview-col", "visibility", "none");
    if (!detail) for (const r of state.regions.keys()) for (const id of [`pk-${r}-col`, `pk-${r}-colw`]) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
    wells.setVisible(effective() === "wells");
    if (effective() === "wells") updateWells();
    const kind = effective() === "columns" ? "columns" : "flat";
    const showWater = !!storm && effective() !== "wells" && state.res >= 8;
    water.setVisible(showWater, kind);
    // Flat: the water lies over the hexagons. Columns: drawn first, so the see-through columns
    // (glass) lie over the water rising inside them and do not hide it in the depth buffer.
    const before = kind === "columns" ? "overview-fill" : "selection-fill";
    if (map.getLayer(water.id) && map.getLayer(before) && water.placed !== before) { map.moveLayer(water.id, before); water.placed = before; }
    setColumnGlass(map, !!storm && kind === "columns", sources);
    if (showWater) updateWaterMesh(kind);
    if (storm) applyStep();
    const el = document.getElementById("view-note");
    if (el) {
      const t = note(), depthView = (effective() === "wells" || effective() === "columns") && state.res === 10;
      const far = view === "wells" && effective() === "flat";
      el.innerHTML = t ? `<span>${t}</span>${depthView ? `<span class="exag" role="group" aria-label="Depth exaggeration">${EXAGGERATIONS.map((x) =>
        `<button data-exag="${x}" aria-pressed="${x === exag}" title="${x === 1 ? "True scale" : `Depths drawn ${x} times deeper`}">×${x}</button>`).join("")}</span>` : ""}${far
        ? '<button class="btn primary" data-street="1">Take me to street level</button>' : ""}` : "";
      el.hidden = !t;
    }
    document.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
    const cyc = document.getElementById("view3d");
    if (cyc) { cyc.title = cyc.ariaLabel = `View: ${LABEL[view]}. Click for ${LABEL[next()]}`; cyc.setAttribute("aria-pressed", String(is3D())); }
    compass();
  }

  const next = () => VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length];

  function set(v, { animate = true } = {}) {
    if (!VIEWS.includes(v)) return;
    const was3D = is3D();
    view = v;
    setParam("v", v === "flat" ? null : v);
    applyFactor(factors[factorKey()]);
    interaction(is3D());
    if (v === "wells" && state.res !== 10) { goStreet(); watchSpeed(); }
    else if (is3D() && !was3D && map.getPitch() < 20) { tilt(PITCH, animate); watchSpeed(); }
    if (!is3D() && was3D) tilt(0, animate, 0);
    render();
    onChange?.(view);
  }

  // Wells chosen away from street scale: fly to the nearest depression worth seeing
  async function goStreet() {
    const c = map.getCenter();
    toast("Wells show at street scale: taking you to the nearest depression.");
    try {
      const at = await client.call("nearestDepression", c.lat, c.lng);
      if (!at) { toast("No depression near here to show as wells. Find a city first, then choose Wells."); return; }
      map.flyTo({ center: at.lngLat, zoom: 15.6, pitch: PITCH, duration: reduced() ? 0 : 2200 });
    } catch { toast("The nearest depression could not be found (no connection?)."); }
  }

  function setFactor(x) {
    if (!EXAGGERATIONS.includes(x) || x === exag) return;
    factors[factorKey()] = x;
    applyFactor(x);
    render();
    onChange?.(view);
  }

  function applyFactor(x) {
    setParam("x", x === DEFAULT_EXAGGERATION || view === "flat" ? null : String(x));
    if (x === exag) return;
    exag = x;
    wells.exag = x; water.exag = x;
    setExaggeration(map, x, drawn().map((r) => `pk-${r}`));
    map.triggerRepaint();
  }

  document.getElementById("view-note")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-exag], [data-street]");
    if (!b) return;
    if (b.dataset.street) goStreet(); else setFactor(Number(b.dataset.exag));
  });

  function tilt(pitch, animate, bearing) {
    const opts = { pitch, duration: animate && !reduced() ? 900 : 0 };
    if (bearing != null) opts.bearing = bearing;
    map.easeTo(opts);
  }

  // A slow device: suggest Flat (frame times while the map tilts into 3D)
  let warned = false;
  function watchSpeed() {
    if (warned) return;
    const times = [];
    const onRender = () => times.push(performance.now());
    map.on("render", onRender);
    setTimeout(() => {
      map.off("render", onRender);
      const gaps = times.slice(1).map((t, i) => t - times[i]).sort((a, b) => a - b);
      const median = gaps.length ? gaps[gaps.length >> 1] : 0;
      if (gaps.length >= 5 && median > 50 && is3D()) {
        warned = true;
        toast("3D looks slow on this device. The flat view may be smoother.", () => set("flat"), "Use Flat");
      }
    }, 1200);
  }

  function compass() {
    const b = document.getElementById("compass");
    if (!b) return;
    const show = is3D() || Math.abs(map.getBearing()) > 0.5 || map.getPitch() > 0.5;
    b.hidden = !show;
    const svg = b.querySelector("svg");
    if (svg) svg.style.transform = `rotate(${-map.getBearing()}deg)`;
  }
  map.on("rotate", compass);
  map.on("pitch", compass);

  // ---------------------------------------------------------------- wells near the view
  let wellsBusy = false;
  async function updateWells() {
    if (wellsBusy || effective() !== "wells") return;
    wellsBusy = true;
    try {
      const view = viewBox(map);
      const dx = (view[2] - view[0]) * 0.5, dy = (view[3] - view[1]) * 0.5;
      const want = [view[0] - dx, view[1] - dy, view[2] + dx, view[3] + dy];
      const regions = drawn();
      wells.dropExcept(new Set(regions));
      for (const r of regions) {
        const have = wells.meshBox(r);
        if (have && have[0] <= view[0] && have[1] <= view[1] && have[2] >= view[2] && have[3] >= view[3]) continue;
        const mesh = await client.call("wellMesh", r, want, state.show);
        if (mesh && effective() === "wells") wells.setMesh(mesh);
      }
      if (storm) await loadWater();
    } catch (e) {
      toast("The wells could not be drawn here.");
    } finally { wellsBusy = false; }
  }

  // ---------------------------------------------------------------- flat water near the view
  let waterBusy = false, waterAgain = false;
  async function updateWaterMesh(kind) {
    if (waterBusy) { waterAgain = true; return; }
    waterBusy = true;
    try {
      do {
        waterAgain = false;
        const res = state.res;
        const view = viewBox(map);
        const dx = (view[2] - view[0]) * 0.5, dy = (view[3] - view[1]) * 0.5;
        const want = [view[0] - dx, view[1] - dy, view[2] + dx, view[3] + dy];
        const regions = drawn();
        water.dropExcept(new Set(regions));
        for (const r of regions) {
          const have = water.meshOf(r);
          if (have && have.level === res && have.mode === state.show && have.kind === kind && have.box[0] <= view[0] && have.box[1] <= view[1]
            && have.box[2] >= view[2] && have.box[3] >= view[3]) continue;
          const mesh = await client.call("waterMesh", r, res, want, state.show, kind);
          if (mesh && state.res === res && storm && water.kind === kind) water.setMesh(mesh);
        }
      } while (waterAgain);
    } catch (e) {
      toast("The storm's water could not be drawn here.");
    } finally { waterBusy = false; }
  }

  // ---------------------------------------------------------------- the storm's water in 3D
  let stormGen = 0;
  async function setStorm(s, step = 15) {
    storm = s ? { storm: s, step: Math.max(-1, Math.min(15, Number(step))), values: new Map(), gen: ++stormGen } : null;
    wells.setStep(storm ? storm.step : 15); water.setStep(storm ? storm.step : 15);
    water.exag = exag;
    wells.clearWater();
    if (!storm) { water.clearWater(); water.setVisible(false); }   // a new storm: the old water blends into the new
    if (storm) { await loadWater(); render(); }
    map.triggerRepaint();
  }

  async function loadWater() {
    const cur = storm;
    if (!cur || !(state.res >= 8)) return;
    for (const r of drawn()) {
      const key = `${r}:${state.res}:${state.show}`;
      if (!cur.values.has(key)) {
        const out = await client.call("stormLevel", r, state.res, cur.storm, state.show);
        if (storm !== cur || key !== `${r}:${state.res}:${state.show}`) return;     // a newer storm, level or filter
        storm.values.set(key, out.empty ? null : out.values);
      }
      const values = storm.values.get(key);
      if (values && state.res === 10 && !wells.waters.has(r)) wells.setWater(r, values);
      if (values && !water.hasWater(r, state.res, cur.gen)) water.setWater(r, state.res, values, cur.gen);
    }
  }

  // t: -1 (no rain yet) .. 15 (the whole storm); fractional while the storm plays (the GPU layers
  // blend two exact steps; nothing per hexagon changes on the page)
  function setStep(t) {
    if (!storm) return;
    storm.step = Math.max(-1, Math.min(15, Number(t)));
    applyStep();
  }

  function applyStep() {
    if (!storm) return;
    wells.setStep(storm.step);
    water.setStep(storm.step);
  }

  function clearState(r) {
    if (map.getSource(`pk-${r}`)) map.removeFeatureState({ source: `pk-${r}` });
  }

  // ---------------------------------------------------------------- after the map draws a pack
  function drewPack(region, fc) {
    const d = new Int32Array(fc.features.length);
    fc.features.forEach((ft, k) => { d[k] = ft.properties.dep ?? -1; });
    deps.set(region, d);
    clearState(region);
    wells.drop(region);
    water.dropMesh(region); water.clearWater(region);      // its hexagons are new
    if (exag !== DEFAULT_EXAGGERATION) setExaggeration(map, exag, [`pk-${region}`]);
    if (selected?.region === region) markSelected();
  }

  function afterRefresh() {
    render();
    if (storm) loadWater().then(applyStep).catch(() => {});
  }

  function select(region, index) {
    if (selected) markSelected(false);
    selected = region == null ? null : { region, index };
    if (selected) markSelected();
  }

  function markSelected(on = true) {
    const d = deps.get(selected.region);
    if (!d || !map.getSource(`pk-${selected.region}`)) return;
    for (let k = 0; k < d.length; k++) if (d[k] === selected.index) map.setFeatureState({ source: `pk-${selected.region}`, id: k }, { sel: on });
  }

  // the filter changed: every drawn hexagon is new
  function resetData() {
    wells.dropExcept(new Set());
    deps.clear();
    water.clear();
    if (storm) { storm.values.clear(); wells.clearWater(); }
  }

  interaction(is3D());
  // a link to a 3D view that does not say how tilted: tilt as the switch would
  map.once("load", () => { if (is3D() && map.getPitch() < 20) tilt(PITCH, false); });
  return {
    get view() { return view; }, get effective() { return effective(); }, is3D, set, next, render, note,
    afterRefresh, drewPack, select, updateWells, setStorm, setStep, setFactor, goStreet, wells, water, resetData,
    get exaggeration() { return exag; },
    get storm() { return storm; },
  };
}
