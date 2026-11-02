// The rain simulator's controls: the storm (how rare, or how much), how long, the ground before it,
// and the timeline. The engine (worker) runs each storm in 16 exact steps; this file chooses the
// storm, plays the steps, keeps the link up to date and asks the worker for the view's figures.
//
// The timeline is rain fallen, not time: storms are totals in this model (how the rain is spread
// over the hours does not change what is held), so it never says when water arrives.
//
// Smooth play: the playhead runs continuously from "no rain yet" (-1) to the whole storm (15). The
// map blends the water between the two exact steps on either side (GPU, drawing only); the figures
// in the panel and the gauge change only when the playhead reaches an exact step, and the rain
// fallen (exactly linear) is shown continuously.

import { HOURS, YEARS } from "../rarity.js";
import * as f from "./format.js";

export const STEPS = 16;
const PLAY_MS = 6400;                      // no rain to the whole storm (17 marks, 16 steps of rain)
const SNAP_MS = 220;                       // a released slider eases to the nearest exact step
const MAX_MM = 1000;
const KINDS = ["rarity", "amount"];
export const GROUNDS = ["dry", "average", "wet"];
const GROUND_LABEL = { dry: "Dry", average: "Average", wet: "Wet" };
export const DEFAULT_STORM = { kind: "rarity", years: 10, hours: 24, condition: "average" };

// ---------------------------------------------------------------- links: s=a.130.24 | s=r.100.24, g=dry|wet, t=1..16
export function parseLink(s, g, t) {
  if (!s) return { storm: null };
  const m = /^([ar])\.(\d+(?:\.\d+)?)\.(\d+)$/.exec(s);
  if (!m) return { storm: null, bad: true };
  const hours = Number(m[3]);
  if (!HOURS.includes(hours)) return { storm: null, bad: true };
  const condition = g == null ? "average" : g;
  if (!GROUNDS.includes(condition)) return { storm: null, bad: true };
  let storm;
  if (m[1] === "a") {
    const mm = Number(m[2]);
    if (!(mm > 0 && mm <= MAX_MM)) return { storm: null, bad: true };
    storm = { kind: "amount", mm, hours, condition };
  } else {
    const years = Number(m[2]);
    if (!YEARS.includes(years)) return { storm: null, bad: true };
    storm = { kind: "rarity", years, hours, condition };
  }
  let step = STEPS - 1;
  if (t != null) {
    const k = Number(t);
    if (!(Number.isInteger(k) && k >= 0 && k <= STEPS)) return { storm, step, bad: true };
    step = k - 1;
  }
  return { storm, step };
}

export function linkOf(storm) {
  return storm.kind === "amount" ? `a.${Math.round(storm.mm * 10) / 10}.${storm.hours}` : `r.${storm.years}.${storm.hours}`;
}

// "about a 25-year storm": 2 significant figures
export function yearsText(y) {
  if (y === 0) return "more common than the 1-year storm";
  if (y === Infinity) return "rarer than the 1,000-year storm";
  if (!Number.isFinite(y)) return null;
  const r = Number(y.toPrecision(2));
  const s = r >= 100 ? f.int(r) : String(r);
  const an = /^8/.test(s) || /^1[18](0)?$/.test(s);
  return `about ${an ? "an" : "a"} ${s}-year storm`;
}

export const hoursText = (h) => `${h}-hour`;

// The storm in words: "the 10-year 24-hour storm" or "5.1 in (130 mm) in 6 hours"
export function stormWords(storm) {
  if (storm.kind === "rarity") return `the ${f.int(storm.years)}-year ${hoursText(storm.hours)} storm`;
  const r = f.rain(storm.mm);
  return `${r.main} (${r.alt}) in ${storm.hours} hour${storm.hours === 1 ? "" : "s"}`;
}

// Rain fallen after step s, of a total
export const fallen = (total, s) => (total * (s + 1)) / STEPS;

export function createStormBar({ map, client, state, views, toast, reduced, param, setParam, isDetail, onChange, onDetail }) {
  const el = document.getElementById("storm");
  let storm = null, step = STEPS - 1, playing = false, frame = 0;
  let pos = STEPS - 1;                       // the playhead, -1 .. 15, fractional while it moves
  let summary = null, readout = null, centre = null, lines = null, runToken = 0;
  let open = false;
  const listeners = new Set();

  // ---------------------------------------------------------------- the view: what to run on
  const box = () => { const b = map.getBounds(); return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]; };
  const detail = () => isDetail(state.res);

  function ensureLines() {
    if (map.getSource("storm-lines")) return;
    map.addSource("storm-lines", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    // each line shows once the storm reaches the step its overflow starts (feature-state "on":
    // a few changes when a step is reached, no re-filtering while the storm plays)
    map.addLayer({ id: "storm-lines", type: "line", source: "storm-lines",
      layout: { "line-cap": "round" }, paint: { "line-color": "#1d3f9e", "line-opacity": ["case", ["boolean", ["feature-state", "on"], false], 0.75, 0],
        "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.8, 15, 1.8] } }, "spill-line");
  }

  // ---------------------------------------------------------------- running a storm
  async function run({ keepStep = false } = {}) {
    if (!storm) return;
    const token = ++runToken;
    stop();
    if (!keepStep) { step = STEPS - 1; pos = STEPS - 1; }
    writeLink();
    renderBar();
    if (!detail()) { summary = null; await views.setStorm(null); emit(); return; }
    await views.setStorm(storm, pos);
    await refreshFigures(token);
  }

  // the figures for the view (on a new storm, and after the map moves)
  let figTimer = 0;
  function refreshSoon() { clearTimeout(figTimer); figTimer = setTimeout(() => refreshFigures(runToken), 200); }

  async function refreshFigures(token) {
    if (!storm) return;
    const c = map.getCenter();
    centre = { lat: c.lat, lon: c.lng };
    try {
      readout = await client.call("rarityAt", c.lat, c.lng, storm.hours, storm.kind === "amount" ? storm.mm : 0);
      if (token !== runToken) return;
      if (!detail()) { summary = null; renderBar(); emit(); return; }
      const b = box(), regions = await client.call("regionsIn", b, 0);
      const [sm, ln] = await Promise.all([client.call("stormSummary", regions, b, storm, state.show),
        client.call("stormLines", regions, b, storm, state.show)]);
      if (token !== runToken) return;
      summary = { ...sm, box: b };
      lines = ln;
      ensureLines();
      map.getSource("storm-lines").setData(ln);
      linesByStep = Array.from({ length: STEPS }, () => []);
      ln.features.forEach((ft) => linesByStep[ft.properties.s].push(ft.id));
      linesAt = -1;
      applyStep();
    } catch (e) {
      if (token === runToken) toast(`The storm could not be run here (${String(e.message || e)}).`);
    }
    renderBar();
    emit();
  }

  // ---------------------------------------------------------------- the timeline
  // exact step at a playhead position: the last one reached (-1 = before any rain)
  const exact = (p) => Math.floor(p + 1e-6);

  function moveTo(p, { fromPlay = false } = {}) {
    pos = Math.max(-1, Math.min(STEPS - 1, p));
    if (!fromPlay) stop();
    views.setStep(pos);                      // the GPU layers draw the blend
    const k = exact(pos);
    if (k !== step) {
      step = k;
      applyStep();
      for (const fn of listeners) fn("step");
    }
    renderTimeline();
  }

  // an exact step (slider keys, links, the end of play)
  function setStep(k, opts) { moveTo(Math.round(k), opts); writeLink(); }

  // overflow lines up to the current step: switch on (or off, going back) only those in between
  let linesByStep = null, linesAt = -1;
  function applyStep() {
    if (!linesByStep || !map.getSource("storm-lines")) return;
    const lo = Math.min(linesAt, step), hi = Math.max(linesAt, step), on = step > linesAt;
    for (let s = lo + 1; s <= hi; s++) for (const id of linesByStep[s]) map.setFeatureState({ source: "storm-lines", id }, { on });
    linesAt = step;
  }

  // ease the playhead to p over ms (released slider, a new storm shown at its end)
  let easeFrame = 0;
  function easeTo(p, ms = SNAP_MS) {
    cancelAnimationFrame(easeFrame);
    const from = pos, t0 = performance.now();
    if (reduced() || Math.abs(p - from) < 1e-3) { setStep(p); return; }
    const go = (now) => {
      const u = Math.min(1, (now - t0) / ms), e = 1 - (1 - u) ** 3;      // ease out
      moveTo(from + (p - from) * e, { fromPlay: true });
      if (u < 1) easeFrame = requestAnimationFrame(go); else setStep(p, { fromPlay: true });
    };
    easeFrame = requestAnimationFrame(go);
  }

  function play() {
    if (!storm || !summary) return;
    if (reduced()) { setStep(STEPS - 1); return; }
    cancelAnimationFrame(easeFrame);
    if (pos >= STEPS - 1 - 1e-6) moveTo(-1);
    playing = true;
    document.getElementById("panel-body")?.setAttribute("aria-busy", "true");
    renderTimeline();
    let last = performance.now();
    const tick = (now) => {
      if (!playing) return;
      const dt = Math.min(now - last, 250);               // on time on slow devices; a stalled tab does not jump ahead
      last = now;
      const p = pos + (dt / PLAY_MS) * STEPS;
      if (p >= STEPS - 1) { moveTo(STEPS - 1, { fromPlay: true }); stop(); writeLink(); return; }
      const t0 = performance.now();
      moveTo(p, { fromPlay: true });
      if (window.__lowpoints) (window.__stormTicks ||= []).push(performance.now() - t0);   // test builds only read it
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  }

  function stop() {
    if (!playing) return;
    playing = false;
    cancelAnimationFrame(frame);
    document.getElementById("panel-body")?.setAttribute("aria-busy", "false");
    writeLink();
    renderTimeline();
    for (const fn of listeners) fn("step");
  }

  // ---------------------------------------------------------------- opening, choosing
  function openBar(s = null, at = null) {
    open = true;
    el.hidden = false;
    document.body.classList.add("storm-open");
    document.getElementById("rain")?.setAttribute("aria-pressed", "true");
    storm = s || storm || { ...DEFAULT_STORM };
    step = at == null ? STEPS - 1 : at;
    pos = step;
    run({ keepStep: at != null });
    measure();
  }

  function close() {
    open = false;
    stop();
    runToken++;
    storm = null; summary = null; lines = null; readout = null;
    el.hidden = true;
    document.body.classList.remove("storm-open");
    document.getElementById("rain")?.setAttribute("aria-pressed", "false");
    for (const k of ["s", "g", "t"]) setParam(k, null);
    if (map.getSource("storm-lines")) map.getSource("storm-lines").setData({ type: "FeatureCollection", features: [] });
    views.setStorm(null);
    emit();
  }

  function choose(change) {
    const next = { ...storm, ...change };
    if (next.kind === "amount") { delete next.years; if (!(next.mm > 0)) next.mm = defaultAmount(next.hours); }
    else { delete next.mm; if (!YEARS.includes(next.years)) next.years = 10; }
    storm = next;
    run();
  }

  // an amount to start from: this place's 10-year storm of that duration, or 2 in
  function defaultAmount(hours) {
    const d = readout?.covered && storm?.hours === hours ? readout.depths[YEARS.indexOf(10)] : null;
    return d ? Math.round(d) : 50.8;
  }

  function writeLink() {
    if (!storm) return;
    setParam("s", linkOf(storm));
    setParam("g", storm.condition === "average" ? null : storm.condition);
    setParam("t", playing || step === STEPS - 1 ? null : String(step + 1));
  }

  // ---------------------------------------------------------------- the bar
  const us = () => f.units.us;
  const amountIn = (mm) => (us() ? Math.round((mm / 25.4) * 100) / 100 : Math.round(mm * 10) / 10);
  const centreDepth = () => {
    if (!storm || !readout?.covered) return NaN;
    if (storm.kind === "amount") return storm.mm;
    return readout.depths[YEARS.indexOf(storm.years)];
  };

  function renderBar() {
    if (!open) return;
    if (!detail()) {
      el.innerHTML = `<div class="storm-head"><span class="label">Rain simulator</span>${closeBtn()}</div>
        <p class="storm-far">Storms run on single depressions. Zoom in to a city to run one.</p>
        <div class="row"><button class="btn primary" data-st="zoom">Zoom in here</button></div>`;
      measure();
      return;
    }
    const s = storm, rare = s.kind === "rarity";
    const yearOpts = YEARS.map((y) => `<option value="${y}"${y === s.years ? " selected" : ""}>${f.int(y)}-year</option>`).join("");
    const hourOpts = HOURS.map((h) => `<option value="${h}"${h === s.hours ? " selected" : ""}>${h} hour${h === 1 ? "" : "s"}</option>`).join("");
    const unit = us() ? "in" : "mm";
    const picks = readout?.covered
      ? [2, 10, 100].map((y) => {
        const d = readout.depths[YEARS.indexOf(y)];
        const on = rare && s.years === y && s.hours === 24;
        return `<button data-pick="${y}" aria-pressed="${on}" title="NOAA Atlas 14 at the centre of the view">${y}-yr 24 h <small>${f.esc(f.rain(d).main)}</small></button>`;
      }).join("") : "";
    el.innerHTML = `<div class="storm-head"><span class="label" id="storm-label">Rain simulator</span>${closeBtn()}</div>
      <div class="storm-row">
        <div class="seg" role="group" aria-label="Choose the storm by">
          <button data-kind="rarity" aria-pressed="${rare}">How rare</button><button data-kind="amount" aria-pressed="${!rare}">How much</button></div>
        ${rare ? `<label class="field"><span class="visually-hidden">Return period</span><select data-f="years">${yearOpts}</select></label>`
    : `<label class="field amount"><span class="visually-hidden">Rain (${unit})</span><input data-f="amount" type="number" inputmode="decimal" min="0" step="${us() ? 0.1 : 1}"
          value="${amountIn(s.mm)}" aria-describedby="storm-amount-note"><span class="unit">${unit}</span></label>`}
        <label class="field"><span class="visually-hidden">Duration</span><select data-f="hours">${hourOpts}</select></label>
        <div class="seg" role="group" aria-label="Ground before the storm">${GROUNDS.map((g) =>
    `<button data-ground="${g}" aria-pressed="${g === s.condition}" title="${groundTitle(g)}">${GROUND_LABEL[g]}</button>`).join("")}</div>
      </div>
      ${picks ? `<div class="storm-picks"><span class="label">Here</span>${picks}</div>` : ""}
      <p class="storm-readout" id="storm-amount-note">${readoutText()}</p>
      <div class="storm-time">
        <button class="btn primary play" data-st="play">Play</button>
        <input type="range" min="0" max="${STEPS}" step="0.01" value="${pos + 1}" data-f="step" aria-label="Rain fallen so far, in sixteenths of the storm">
        <span class="storm-now" aria-live="off"></span>
      </div>`;
    renderTimeline();
    measure();
  }

  const closeBtn = () => `<button class="close" data-st="close" aria-label="Close the rain simulator"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.6"/></svg></button>`;

  function groundTitle(g) {
    return g === "dry" ? "Dry ground soaks up more rain (curve numbers for dry conditions, NRCS)"
      : g === "wet" ? "Wet ground after earlier rain lets more run off (curve numbers for wet conditions, NRCS)"
      : "Usual conditions (the curve numbers as mapped)";
  }

  function readoutText() {
    if (!readout) return "Reading NOAA Atlas 14 for this place…";
    if (!readout.covered) {
      return storm.kind === "rarity"
        ? "NOAA Atlas 14 does not cover this place (Washington and Oregon), so storms by rarity are not known here. Choose an amount instead."
        : "How rare this is cannot be said here: NOAA Atlas 14 does not cover Washington and Oregon.";
    }
    if (storm.kind === "amount") {
      const y = yearsText(readout.returnPeriod);
      return `${f.esc(stormWords(storm))} is ${y} at the centre of the view (NOAA Atlas 14).${storm.mm > MAX_MM - 1e-9 ? "" : ""}`;
    }
    const d = centreDepth();
    const range = summary && Number.isFinite(summary.rainMin) && summary.rainMax - summary.rainMin > 0.02 * summary.rainMax   // only a spread worth saying
      ? ` Each depression gets the depth at its own location: ${f.esc(f.rain(summary.rainMin).main)} to ${f.esc(f.rain(summary.rainMax).main)} across this view.` : "";
    return `The ${f.int(storm.years)}-year ${storm.hours}-hour storm here: ${f.esc(f.rain(d).main)} (${f.esc(f.rain(d).alt)}) at the centre of the view (NOAA Atlas 14).${range}`;
  }

  function renderTimeline() {
    if (!open || !detail()) return;
    const range = el.querySelector('[data-f="step"]'), now = el.querySelector(".storm-now"), btn = el.querySelector('[data-st="play"]');
    if (range && document.activeElement !== range) range.value = String(pos + 1);
    const text = nowText();                          // written only when it changes (every frame while playing)
    if (range && !playing && range.getAttribute("aria-valuetext") !== text) range.setAttribute("aria-valuetext", text);
    if (now && now.textContent !== text) now.textContent = text;
    if (btn) { btn.textContent = playing ? "Pause" : step >= STEPS - 1 ? "Replay" : "Play"; }
  }

  function nowText() {
    if (!storm) return "";
    const d = storm.kind === "amount" ? storm.mm : centreDepth();
    if (Number.isFinite(d)) {
      const so = f.rain(fallen(d, pos)), all = f.rain(d);
      return `Rain so far: ${so.main} of ${all.main}${storm.kind === "rarity" ? " (centre of the view)" : ""}`;
    }
    return `Rain so far: ${Math.round(((pos + 1) / STEPS) * 100)}% of the storm`;
  }

  // ---------------------------------------------------------------- events
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.st === "close") close();
    else if (b.dataset.st === "zoom") onDetail();
    else if (b.dataset.st === "play") playing ? stop() : play();
    else if (b.dataset.kind && b.dataset.kind !== storm.kind) choose({ kind: b.dataset.kind });
    else if (b.dataset.ground && b.dataset.ground !== storm.condition) choose({ condition: b.dataset.ground });
    else if (b.dataset.pick) choose({ kind: "rarity", years: Number(b.dataset.pick), hours: 24 });
  });
  el.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.f === "years") choose({ years: Number(t.value) });
    else if (t.dataset.f === "hours") choose({ hours: Number(t.value) });
    else if (t.dataset.f === "amount") {
      const v = Number(t.value), mm = us() ? v * 25.4 : v;
      if (!(mm > 0)) { toast("Enter a rain amount above 0."); t.value = String(amountIn(storm.mm)); return; }
      if (mm > MAX_MM) { toast(`At most ${us() ? "39.4 in" : "1,000 mm"}: the water from neighbouring areas is known only up to there.`); t.value = String(amountIn(Math.min(storm.mm, MAX_MM))); return; }
      choose({ mm: Math.round(mm * 10) / 10 });
    }
  });
  el.addEventListener("input", (e) => { if (e.target.dataset.f === "step") { cancelAnimationFrame(easeFrame); moveTo(Number(e.target.value) - 1); } });
  el.addEventListener("change", (e) => { if (e.target.dataset.f === "step") easeTo(Math.round(Number(e.target.value) - 1)); });
  el.addEventListener("keydown", (e) => {
    const r = e.target.dataset?.f === "step";
    if (!r || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(e.key)) return;
    e.preventDefault();                                       // keys move by whole exact steps
    const k = { Home: -1, End: STEPS - 1 }[e.key] ?? step + (["ArrowLeft", "ArrowDown", "PageDown"].includes(e.key) ? -1 : 1) * (e.key.startsWith("Page") ? 4 : 1);
    easeTo(Math.max(-1, Math.min(STEPS - 1, k)), 160);
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === " " && e.target === el.querySelector('[data-f="step"]')) { e.preventDefault(); playing ? stop() : play(); }
  });

  function emit() { for (const fn of listeners) fn("storm"); onChange?.(); }

  function measure() {
    requestAnimationFrame(() => document.documentElement.style.setProperty("--storm-h", `${open ? Math.round(el.getBoundingClientRect().height) : 0}px`));
  }

  // ---------------------------------------------------------------- from the page
  function afterRefresh() {
    if (!open || !storm) return;
    if (!detail()) { if (summary || views.storm) { summary = null; views.setStorm(null); } renderBar(); emit(); return; }
    if (!views.storm) { run({ keepStep: true }); return; }
    refreshSoon();
  }

  // the link, once the map has loaded
  function fromLink() {
    const p = parseLink(param("s"), param("g"), param("t"));
    if (p.bad) toast("Part of this link's storm could not be read, so the storm shown may differ.");
    if (p.storm) openBar(p.storm, p.step);
    else if (p.bad) for (const k of ["s", "g", "t"]) setParam(k, null);
  }

  return {
    open: openBar, close, play, stop, setStep, afterRefresh, fromLink, renderBar,
    on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    get active() { return open && !!storm; }, get storm() { return storm; }, get step() { return step; }, get pos() { return pos; },
    get summary() { return summary; }, get readout() { return readout; }, get playing() { return playing; },
    get lines() { return lines; }, centreDepth, nowText, readoutText,
  };
}
