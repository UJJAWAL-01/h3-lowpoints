// The three views (Flat, Columns, Wells): switching, tilting and rotating in 3D, the wells layer,
// the stated exaggeration, and the storm's water in 3D (values from the worker's stormLevel).

import { DEPTH_EXAGGERATION, SHARE_CAP, SHARE_M_PER_PCT, VIEWS, waterHeight } from "../map/heights.js";
import { applyView } from "../map/layers.js";
import { WellsLayer } from "../map/wells.js";

const PITCH = 55;
const LABEL = { flat: "Flat", columns: "Columns", wells: "Wells" };

export function createViews({ map, client, state, toast, reduced, param, setParam, onChange }) {
  const wells = new WellsLayer();
  let view = VIEWS.includes(param("v")) ? param("v") : "flat";
  let storm = null;                 // { storm, step, values: Map "region:level" -> Float32Array }
  const deps = new Map();           // region -> Int32Array: depression with most area, per drawn hexagon
  let selected = null;              // { region, index }

  const is3D = () => view !== "flat";
  const effective = () => (view === "wells" && state.res !== 10 ? "flat" : view);
  const drawn = () => [...state.regions.keys()].filter((r) => map.getLayer(`pk-${r}-fill`)
    && map.getLayoutProperty(`pk-${r}-fill`, "visibility") !== "none");

  function ensureLayer() {
    if (!map.getLayer("wells") && map.getLayer("selection-fill")) map.addLayer(wells, "selection-fill");
  }
  map.on("styledata", () => { if (map.getLayer("selection-fill")) ensureLayer(); });

  function interaction(on) {
    for (const h of [map.dragRotate, map.touchPitch]) on ? h.enable() : h.disable();
    on ? map.touchZoomRotate.enableRotation() : map.touchZoomRotate.disableRotation();
    on ? map.keyboard.enableRotation() : map.keyboard.disableRotation();
  }

  // what the legend says about heights, in plain words
  function note() {
    const v = effective(), res = state.res;
    if (view === "wells" && v === "flat") return "Wells show at the finest level (street scale). Zoom in to see them.";
    if (v === "columns") {
      return res === 10
        ? `Column height = water depth when full, exaggerated ×${DEPTH_EXAGGERATION}.`
        : `Column height: 1% of the hexagon in depressions = ${SHARE_M_PER_PCT[res]} m (shares above ${SHARE_CAP * 100}% drawn at ${SHARE_CAP * 100}%). Heights are not real.`;
    }
    if (v === "wells") return `Well depth = water depth when full, exaggerated ×${DEPTH_EXAGGERATION}, below the level at which each depression overflows.`;
    return "";
  }

  function render() {
    ensureLayer();
    const sources = ["overview", ...drawn().map((r) => `pk-${r}`)];
    applyView(map, effective(), sources);
    wells.setVisible(effective() === "wells");
    if (effective() === "wells") updateWells();
    if (storm) applyStep();
    const el = document.getElementById("view-note");
    if (el) { const t = note(); el.textContent = t; el.hidden = !t; }
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
    interaction(is3D());
    if (is3D() && !was3D && map.getPitch() < 20) { tilt(PITCH, animate); watchSpeed(); }
    if (!is3D() && was3D) tilt(0, animate, 0);
    render();
    onChange?.(view);
  }

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
      const b = map.getBounds(), view = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
      const dx = (view[2] - view[0]) * 0.5, dy = (view[3] - view[1]) * 0.5;
      const want = [view[0] - dx, view[1] - dy, view[2] + dx, view[3] + dy];
      const regions = drawn();
      wells.dropExcept(new Set(regions));
      for (const r of regions) {
        const have = wells.meshBox(r);
        if (have && have[0] <= view[0] && have[1] <= view[1] && have[2] >= view[2] && have[3] >= view[3]) continue;
        const mesh = await client.call("wellMesh", r, want);
        if (mesh && effective() === "wells") wells.setMesh(mesh);
      }
      if (storm) await loadWater();
    } catch (e) {
      toast("The wells could not be drawn here.");
    } finally { wellsBusy = false; }
  }

  // ---------------------------------------------------------------- the storm's water in 3D
  async function setStorm(s) {
    storm = s ? { storm: s, step: 15, values: new Map() } : null;
    wells.clearWater();
    for (const r of state.regions.keys()) clearState(r);
    if (storm) { await loadWater(); applyStep(); }
    map.triggerRepaint();
  }

  async function loadWater() {
    if (!storm || !(state.res >= 8)) return;
    for (const r of drawn()) {
      const key = `${r}:${state.res}`;
      if (!storm.values.has(key)) {
        const out = await client.call("stormLevel", r, state.res, storm.storm);
        if (!storm) return;
        storm.values.set(key, out.empty ? null : out.values);
      }
      const values = storm.values.get(key);
      if (values && state.res === 10 && !wells.waters.has(r)) wells.setWater(r, values);
    }
  }

  function setStep(s) {
    if (!storm) return;
    storm.step = Math.max(0, Math.min(15, s | 0));
    applyStep();
  }

  function applyStep() {
    if (!storm) return;
    wells.setStep(storm.step);
    if (effective() !== "columns") return;
    for (const r of drawn()) {
      const values = storm.values.get(`${r}:${state.res}`);
      if (!values) continue;
      const K = values.length / 16;
      for (let k = 0; k < K; k++) {
        map.setFeatureState({ source: `pk-${r}`, id: k }, { w: waterHeight(state.res, values[k * 16 + storm.step]) });
      }
    }
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

  interaction(is3D());
  // a link to a 3D view that does not say how tilted: tilt as the switch would
  map.once("load", () => { if (is3D() && map.getPitch() < 20) tilt(PITCH, false); });
  return {
    get view() { return view; }, get effective() { return effective(); }, is3D, set, next, render, note,
    afterRefresh, drewPack, select, updateWells, setStorm, setStep, wells,
    get storm() { return storm; },
  };
}
