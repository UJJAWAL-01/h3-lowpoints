// Tours of New York City and Austin: the app runs itself through every feature, chapter by chapter,
// pressing its own buttons. Captions take their numbers from the panels at run time. Places were
// chosen by fixed rules (app/tools/tour_places.py; plans/PHASE6_4e_SPEC.md). Film mode (#film=1)
// hides the controls and adds title and end cards, for recording.

import * as f from "./format.js";
import { dayText, reportsKey, showReports } from "../map/reports.js";

export const TOURS = {
  nyc: {
    name: "New York City", search: "New York", stormDay: "2023-09-29",
    depression: { region: "842a107ffffffff", id: 5228425, lat: 40.54236, lon: -74.17958 },
    road: { region: "842a107ffffffff", id: 5221628, lat: 40.55931, lon: -74.17208 },
    city: { center: [-73.97, 40.70], zoom: 10.4 },
    evidence: "In the first study, depressions that fill in a 2-year storm drew 1.28 times as many of New York's street-flooding reports as depressions that need a rarer storm or never fill: a modest link, not a forecast.",
  },
  austin: {
    name: "Austin", search: "Austin", stormDay: "2015-10-30",
    depression: { region: "84489e3ffffffff", id: 10966539, lat: 30.29819, lon: -97.64125 },
    road: { region: "84489e3ffffffff", id: 10959192, lat: 30.32625, lon: -97.58264 },
    city: { center: [-97.74, 30.29], zoom: 10.6 },
    evidence: "In Austin the studies found no clear link between depressions and flood reports: those filling in a 2-year storm were not clearly more often reported, and landlocked ones drew fewer reports than drained ones.",
  },
};

const CHAPTERS = ["The country", "Find a place", "Zoom in", "One depression", "Roads and drains", "Run a storm",
  "Your own storm", "3D", "Real floods", "Evidence", "Python", "The end"];

class Cancelled extends Error {}

export function createTour(ctx) {
  const { map, state, stormBar, views, openLink, clearSelection, setShow, openAbout, openPython, reduced, $ } = ctx;
  let tour = null, key = null, chapter = 0, token = 0, paused = false, film = false, ui = null, pointer = null, reportsOn = false;

  // ---------------------------------------------------------------- the bar and the pointer
  function build() {
    ui = document.createElement("section");
    ui.className = `tour${film ? " film" : ""}`;
    ui.setAttribute("aria-label", "Tour");
    ui.innerHTML = `<div class="tour-cap"><span class="label" id="tour-chapter"></span>
        <h2 id="tour-title"></h2><p id="tour-text" aria-live="polite"></p></div>
      <div class="tour-nav"><ol class="tour-dots" aria-label="Chapters">${CHAPTERS.map((c, k) =>
        `<li><button data-t="go" data-k="${k}" aria-label="Chapter ${k + 1}: ${c}"></button></li>`).join("")}</ol>
        <button class="btn" data-t="back">Back</button><button class="btn primary" data-t="pause">Pause</button>
        <button class="btn" data-t="next">Next</button><button class="btn link" data-t="exit">Exit tour</button></div>`;
    ui.addEventListener("click", (e) => {
      const b = e.target.closest("[data-t]");
      if (!b) return;
      const a = b.dataset.t;
      if (a === "pause") setPaused(!paused);
      else if (a === "next") go(chapter + 1);
      else if (a === "back") go(chapter - 1);
      else if (a === "go") go(Number(b.dataset.k));
      else stop();
    });
    pointer = document.createElement("div");
    pointer.className = "tour-pointer";
    pointer.setAttribute("aria-hidden", "true");
    document.body.append(ui, pointer);
    document.addEventListener("keydown", keys);
    map.getCanvas().addEventListener("pointerdown", touched);
  }

  function keys(e) {
    if (!tour || document.querySelector("dialog[open]")) return;
    if (e.key === "Escape") { e.preventDefault(); stop(); }
    else if (e.key === " " && e.target === document.body) { e.preventDefault(); setPaused(!paused); }
  }
  function touched(e) { if (tour && e.isTrusted && !paused && !film) setPaused(true); }

  function setPaused(p) {
    paused = p;
    const b = ui?.querySelector('[data-t="pause"]');
    if (b) { b.textContent = p ? "Play" : "Pause"; b.setAttribute("aria-pressed", String(p)); }
    if (p) { map.stop(); if (stormBar.playing) stormBar.stop(); }
  }

  function caption(title, text) {
    $("tour-chapter").textContent = `${tour.name} tour · ${chapter + 1} of ${CHAPTERS.length} · ${CHAPTERS[chapter]}`;
    $("tour-title").textContent = title;
    $("tour-text").textContent = text;
    ui.querySelectorAll(".tour-dots button").forEach((b, k) => {
      b.classList.toggle("done", k < chapter);
      b.setAttribute("aria-current", k === chapter ? "step" : "false");
    });
  }

  // ---------------------------------------------------------------- steps the chapters use
  const check = (t) => { if (t !== token) throw new Cancelled(); };
  async function wait(ms, t) {                       // pause-aware
    let left = ms;
    while (left > 0) { check(t); await sleep(100); if (!paused) left -= 100; }
    check(t);
  }
  async function waitFor(fn, t, ms = 60000) {
    const end = Date.now() + ms;
    while (!fn()) { check(t); if (Date.now() > end) return false; await sleep(150); }
    check(t);
    return true;
  }
  const loaded = (t) => waitFor(() => state.loading === 0 && map.loaded() && !map.isMoving(), t);
  async function fly(opts, t) {
    check(t);
    while (paused) await wait(100, t);
    if (reduced()) map.jumpTo(opts);
    else {
      const done = new Promise((r) => map.once("moveend", r));
      map.flyTo({ curve: 1.5, speed: 0.9, essential: true, ...opts, duration: opts.duration ?? 4200 });
      await done;
    }
    check(t);
    await loaded(t);
  }
  async function ease(opts, t) {
    check(t);
    if (reduced()) { map.jumpTo(opts); return; }
    const done = new Promise((r) => map.once("moveend", r));
    map.easeTo({ essential: true, ...opts });
    await done;
    check(t);
  }
  async function point(target, t) {                  // move the pointer to an element or a map point
    let x, y;
    if (Array.isArray(target)) { const p = map.project(target); const r = map.getCanvas().getBoundingClientRect(); x = r.left + p.x; y = r.top + p.y; }
    else { const r = target.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top + r.height / 2; }
    pointer.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
    pointer.classList.add("on");
    await wait(reduced() ? 100 : 750, t);
    pointer.classList.remove("press"); void pointer.offsetWidth; pointer.classList.add("press");
  }
  async function press(el, t) {
    if (!el) return false;
    el.scrollIntoView?.({ block: "nearest" });
    await point(el, t);
    el.click();
    await wait(350, t);
    setTimeout(() => pointer?.classList.remove("on"), 900);
    return true;
  }
  async function type(input, text, t) {
    await point(input, t);
    input.focus();
    input.value = "";
    for (const ch of text) {
      input.value += ch;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await wait(reduced() ? 20 : 110, t);
    }
  }
  const panelText = () => $("panel-body").innerText;
  const sel = (s) => document.querySelector(s);
  const dep = () => state.selected?.data;
  async function openDep(which, t) {
    const p = tour[which];
    await point([p.lon, p.lat], t);
    await openLink(`${p.region}.${p.id}`);
    await waitFor(() => dep() && dep().id === p.id, t);
    await loaded(t);
  }
  async function closeDialogs() { document.querySelectorAll("dialog[open]").forEach((d) => d.close()); }
  async function calm(t) {                           // every chapter starts from a plain state
    closeDialogs();
    if (stormBar.playing) stormBar.stop();
    if (views.view !== "flat") { views.set("flat"); await wait(300, t); }
    if (map.getPitch() > 1 || Math.abs(map.getBearing()) > 1) await ease({ pitch: 0, bearing: 0, duration: 900 }, t);
    if (state.show !== "all") setShow("all");
  }

  // ---------------------------------------------------------------- the chapters
  const chapters = [
    async (t) => {                                   // 1 the country
      await calm(t); if (stormBar.active) stormBar.close(); if (state.view !== "intro") clearSelection();
      await fly({ center: [-97.6, 38.6], zoom: 3.55, pitch: 0, bearing: 0, duration: 2500 }, t);
      await waitFor(() => state.nat, t, 20000);
      caption("Where rain water collects", `Lowpoints maps every closed depression on land in the lower 48 US states: ${f.int(state.nat.depressions)} places where rain flows in but cannot flow out. Darker hexagons have more of their land in depressions.`);
      await ease({ center: [-92, 38.4], zoom: 3.9, duration: 7000 }, t);
      await wait(1500, t);
    },
    async (t) => {                                   // 2 find a place
      await calm(t);
      caption("Find any place", `Type a US city or town. The list of places is built into the app, so nothing you type leaves your device.`);
      await type($("search"), tour.search, t);
      await waitFor(() => sel("#results li[data-i]"), t, 10000);
      await press(sel("#results li[data-i]"), t);
      await wait(300, t);
      $("search").value = ""; $("search").dispatchEvent(new Event("input", { bubbles: true })); $("search").blur();
      await loaded(t);
      await fly({ ...tour.city, duration: 3500 }, t);
      await wait(2500, t);
    },
    async (t) => {                                   // 3 zoom in: levels, basemap, units
      await calm(t);
      const d = tour.depression;
      await fly({ center: [d.lon, d.lat], zoom: 12.2, duration: 5000 }, t);
      caption("From city to street", "At city scale each hexagon adds up the depressions in it (H3 levels 8 and 9). Zoomed in to level 10, each hexagon is about 15,000 m², and its shade is how deep water stands there once its depression is full.");
      await wait(3500, t);
      await fly({ center: [d.lon, d.lat], zoom: 15.4, duration: 4500 }, t);
      await wait(2000, t);
      caption("Your map, your units", "Switch the street map between detailed and quiet, and the units between metres and feet.");
      await press($("basemap"), t); await wait(1800, t); await press($("basemap"), t);
      await press($("units"), t); await wait(1500, t); await press($("units"), t);
      await wait(1000, t);
    },
    async (t) => {                                   // 4 one depression
      await calm(t);
      await openDep("depression", t);
      const d = dep();
      const where = [...document.querySelectorAll("#panel-body p.say")].map((x) => x.textContent).find((x) => /^When full/.test(x)) || "";
      caption("One depression", `This one holds ${f.volume(d.volume).main} of water when full, up to ${f.depth(d.depth).main} deep. ${f.fillsIn(d.fillsInYears).text}`);
      await wait(6500, t);
      caption("Where the water goes", `${where} The blue arrows follow it; grey dotted lines are depressions that overflow into this one.`);
      await ease({ zoom: Math.max(13.8, map.getZoom() - 1.2), duration: 2500 }, t);
      await wait(5000, t);
    },
    async (t) => {                                   // 5 roads and drains
      await calm(t);
      await fly({ center: [tour.road.lon, tour.road.lat], zoom: 15.6, duration: 4000 }, t);
      await openDep("road", t);
      const cut = /road cut or underpass/i.test(panelText());
      caption("Roads and drains", `${cut ? "This hollow runs along a major road: a road cut or underpass" : "This hollow lies on a major road"}. Storm drains usually empty these, but drains are not in the elevation data, so the map flags them.`);
      await wait(5500, t);
      caption("Hide likely drained", "One click takes likely drained depressions off the map: those beside a stream, ditch or canal, or on a major road.");
      await press(sel('[data-act="show-undrained"]'), t); await wait(3000, t); await loaded(t);
      await press(sel('[data-act="show-all"]'), t); await wait(1500, t);
      clearSelection();
    },
    async (t) => {                                   // 6 run a storm
      await calm(t); if (state.view !== "intro") clearSelection();
      const d = tour.depression;
      await fly({ center: [d.lon, d.lat], zoom: 14.6, duration: 4000 }, t);
      if (!stormBar.active) await press($("rain"), t);
      await waitFor(() => stormBar.summary && sel('[data-st="play"]'), t);
      caption("Run a storm", `${stormBar.readoutText()} Press Play and watch the depressions fill.`);
      await wait(4500, t);
      await press(sel('[data-st="play"]'), t);
      caption("Hour by hour", "Rain falls as it does in this region's storms (NOAA Atlas 14's pattern). Depressions fill, deeper blue for deeper water, and overflowing ones light up the path to the next.");
      await waitFor(() => stormBar.playing, t, 5000);
      await waitFor(() => !stormBar.playing, t, 30000);
      await wait(800, t);
      const end = stormBar.summary.byStep[stormBar.summary.byStep.length - 1];
      caption("What the storm did", `By the end, ${f.int(end.fill)} of the ${f.int(stormBar.summary.depressions)} depressions in view are full and ${f.int(end.overflow)} overflow. The panel lists which filled first.`);
      await wait(5500, t);
      await openDep("depression", t);
      await waitFor(() => sel("[data-gauge-body]") && !/Running/.test(sel(".gauge").innerText), t, 30000);
      const lines = (sel("[data-gauge-body]")?.innerText || "").split("\n").map((x) => x.trim()).filter(Boolean);
      const g = lines.find((x) => /^Full after about/.test(x)) || lines.find((x) => /full/i.test(x)) || "";
      caption("One depression in the storm", g ? g.trim() : "Its own gauge shows how full it gets and when.");
      await wait(6000, t);
      clearSelection();
    },
    async (t) => {                                   // 7 your own storm
      await calm(t); if (state.view !== "intro") clearSelection();
      if (!stormBar.active) await press($("rain"), t);
      await waitFor(() => sel('[data-kind="amount"]'), t);
      caption("Your own storm", "Or choose how much rain falls and for how long, and how wet the ground was before.");
      await press(sel('[data-kind="amount"]'), t);
      await waitFor(() => stormBar.storm?.kind === "amount" && stormBar.summary, t);
      await press(sel('[data-ground="dry"]'), t);
      await waitFor(() => stormBar.storm?.condition === "dry" && stormBar.summary, t);
      await wait(1200, t);
      const end = stormBar.summary.byStep[stormBar.summary.byStep.length - 1];
      caption("Dry ground soaks up more", `${stormBar.readoutText()} On dry ground, ${f.int(end.fill)} of the ${f.int(stormBar.summary.depressions)} depressions in view fill.`);
      await wait(6000, t);
      await press(sel('[data-ground="average"]'), t);
      await press(sel('[data-kind="rarity"]'), t);
      await waitFor(() => stormBar.storm?.kind === "rarity" && stormBar.summary, t);
    },
    async (t) => {                                   // 8 3D
      await calm(t);
      caption("See it in 3D", "Columns raise each hexagon by how deep its water is; the height is exaggerated, and the map says by how much.");
      await press(sel('[data-view="columns"]') || $("view3d"), t);
      await wait(800, t);
      await ease({ pitch: 58, bearing: -25, duration: 3000 }, t);
      await ease({ bearing: 35, duration: 7000 }, t);
      caption("Wells", "Wells sink each depression below the street: you look down into the hollows the water fills.");
      await press(sel('[data-view="wells"]') || $("view3d"), t);
      await wait(1500, t); await loaded(t);
      await ease({ bearing: -20, pitch: 62, duration: 8000 }, t);
      await wait(1000, t);
    },
    async (t) => {                                   // 9 real floods
      await calm(t); if (stormBar.active) stormBar.close(); if (state.view !== "intro") clearSelection();
      const sets = await showReports(map, true); reportsOn = true;
      const s = sets.find((x) => x.key === key);
      await fly({ ...tour.city, zoom: tour.city.zoom + 0.4, duration: 4500 }, t);
      caption("Real floods", `Red dots: ${f.int(s.points.length)} flood reports to ${tour.name}'s 311 line on ${dayText(s.day)}. They show where people noticed flooding, not where water stood.`);
      await wait(6500, t);
      const d = tour.depression;
      await fly({ center: [d.lon, d.lat], zoom: 14.2, duration: 4000 }, t);
      caption("What the studies found", tour.evidence);
      await wait(7000, t);
      await showReports(map, false); reportsOn = false;
    },
    async (t) => {                                   // 10 evidence and limits
      await calm(t);
      caption("Evidence and limits", "The map's main results were tested against real flood reports in six cities, and the negative results are published too. Storm drains, pipes and pumps are not in the data, and this is not a flood map or advice.");
      openAbout("evidence");
      await wait(7000, t);
      document.getElementById("disclaimer")?.scrollIntoView({ block: "start", behavior: reduced() ? "auto" : "smooth" });
      await wait(4500, t);
      closeDialogs();
    },
    async (t) => {                                   // 11 python
      await calm(t);
      await openDep("depression", t);
      caption("Do this in Python", "Every depression comes with the code that recomputes it from public data, with the open-source h3-lowpoints library behind this map.");
      await press(sel('[data-act="python-dep"]'), t);
      await wait(6500, t);
      closeDialogs();
      clearSelection();
    },
    async (t) => {                                   // 12 the end
      await calm(t); if (stormBar.active) stormBar.close();
      const other = key === "nyc" ? "austin" : "nyc";
      await fly({ center: [-97.6, 38.6], zoom: 3.9, duration: 4000 }, t);
      caption("Explore your own city", `Search any place in the lower 48 states, run a storm, and follow the water. Next: the tour of ${TOURS[other].name}.`);
      await wait(film ? 6000 : 5000, t);
    },
  ];

  // ---------------------------------------------------------------- running
  async function go(k) {
    if (!tour) return;
    if (k < 0) k = 0;
    if (k >= chapters.length) { stop(); return; }
    const t = ++token;
    chapter = k;
    setPaused(false);
    caption(CHAPTERS[k], "");
    try {
      await chapters[k](t);
      if (t === token) go(k + 1);
    } catch (e) {
      if (!(e instanceof Cancelled)) { console.error(e); if (t === token) go(k + 1); }
    }
  }

  async function start(which, opts = {}) {
    if (!TOURS[which]) return;
    if (tour) stop();
    key = which; tour = TOURS[which]; film = !!opts.film;
    build();
    document.body.classList.add("touring");
    if (film) document.body.classList.add("filming");
    if (film) await titleCard();
    go(opts.chapter || 0);
  }

  async function titleCard() {
    const c = document.createElement("div");
    c.className = "tour-title-card";
    c.innerHTML = `<p class="wordmark">Lowpoints</p><p>Where rain water collects, in ${tour.name}</p>`;
    document.body.append(c);
    await sleep(3200);
    c.classList.add("out");
    await sleep(900);
    c.remove();
  }

  function stop() {
    if (!tour) return;
    token++;
    tour = null;
    if (stormBar.playing) stormBar.stop();
    if (reportsOn) { showReports(map, false); reportsOn = false; }
    ui?.remove(); pointer?.remove(); ui = pointer = null;
    document.removeEventListener("keydown", keys);
    map.getCanvas().removeEventListener("pointerdown", touched);
    document.body.classList.remove("touring", "filming");
    ctx.onEnd?.();
  }

  return { start, stop, go, get running() { return !!tour; }, get chapter() { return chapter; }, get paused() { return paused; },
    get key() { return key; }, chapters: CHAPTERS, reportsKey };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
