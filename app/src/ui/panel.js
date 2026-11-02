// What the side panel (or bottom sheet) says. Plain sentences first, the numbers below them.

import { EARTH } from "../map/heights.js";
import { legendHtml } from "../map/layers.js";
import * as f from "./format.js";
import { WATER } from "../map/water.js";
import { STEPS, fallen } from "./storm-bar.js";
import { duration, timeAt } from "../temporal.js";

const both = (x) => `${f.esc(x.main)}${x.alt ? ` <small>${f.esc(x.alt)}</small>` : ""}`;
const inline = (x) => `${f.esc(x.main)}${x.alt ? ` (${f.esc(x.alt)})` : ""}`;
const CLOSE = `<button class="close" data-act="close" aria-label="Close details"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.6"/></svg></button>`;

export function intro(nat, mode, res, heightNote = "", earth = false, show = "all") {
  const n = nat
    ? `<div class="stats" role="list">
        <div role="listitem"><span class="label">Depressions</span><span class="v">${f.int(nat.depressions)}</span></div>
        <div role="listitem"><span class="label">Fill in a 2-yr storm</span><span class="v">${f.pct(nat.fills2Share, 0)}</span></div>
        <div role="listitem"><span class="label">Landlocked</span><span class="v">${f.pct(nat.landlockedShare, 0)}</span></div>
      </div>
      <p class="muted">Together they cover ${inline(f.area(nat.depressedM2))} of the lower 48 states and hold ${inline(f.volume(nat.volumeM3))} when full.</p>`
    : `<div class="stats"><div><span class="label">Loading</span><span class="v">…</span></div></div>`;
  const hint = res === 10
    ? "Each hexagon is part of a depression. Click one to see how much it holds, the rain that fills it and where it overflows."
    : mode === "detail"
    ? "Zoom in further to see how deep each depression fills, or click a hexagon to open the depression with most area in it."
    : "Zoom in, or find a place, to see single depressions. Click a hexagon for its numbers.";
  return `<h1>Where rain <em>water collects</em></h1>
    <p class="lede">Every closed depression on land in the lower 48 US states: low ground that water flows into but not out of.</p>
    <p class="notice">For exploration and learning only. Not a flood map, forecast or advice. <button class="btn link" data-act="disclaimer">Disclaimer</button></p>
    ${n}
    ${legendHtml(res === 10 ? "depth" : "share", f.units.us, res === 10 && earth ? EARTH : undefined)}
    ${heightNote ? `<p class="note"><strong>3D:</strong> ${f.esc(heightNote)}</p>` : ""}
    <p class="note">${hint}</p>
    ${mode === "detail" ? '<div class="row"><button class="btn primary" data-act="storm-open">Run a storm here</button></div>' : ''}
    ${showControl(show)}
    ${source("cell")}
    ${foot()}`;
}

function foot() {
  return `<div class="foot"><button class="btn link" data-act="about">About this map</button>
    <a href="https://ujjawal-01.github.io/h3-lowpoints/evidence.html" target="_blank" rel="noopener">Evidence</a>
    <a href="https://github.com/UJJAWAL-01/h3-lowpoints" target="_blank" rel="noopener">The library</a></div>`;
}

export function overviewCell(p, res) {
  return `<div class="kicker"><span class="label">H3 cell · resolution ${res}</span>${CLOSE}</div>
    <h2>${f.pct(p.s, p.s < 0.01 ? 2 : 1)} <em>of this hexagon</em> is in depressions</h2>
    <div class="stats">
      <div><span class="label">Depressions</span><span class="v">${f.int(p.c)}</span></div>
      <div><span class="label">Fill in a 2-yr storm</span><span class="v">${f.pct(p.f, 0)}</span></div>
      <div><span class="label">Landlocked</span><span class="v">${f.pct(p.l, 0)}</span></div>
    </div>
    <dl class="facts">
      <dt>Area in depressions</dt><dd>${both(f.area(p.a))}</dd>
      <dt>Water held when all are full</dt><dd>${both(f.volume(p.v))}</dd>
      <dt>Cell</dt><dd><button class="copy" data-copy="${f.esc(p.h)}" title="Copy">${f.esc(p.h)}</button></dd>
    </dl>
    <p class="note">Counts are of depressions whose lowest point lies in this hexagon. Hexagons at coasts and borders are partly outside the data.</p>
    <div class="row"><button class="btn primary" data-act="zoom-cell">Zoom in here</button></div>
    ${source("cell")}`;
}

export function depression(d, extra = "") {
  const deep = f.depth(d.depth), wide = f.area(d.area), holds = f.volume(d.volume);
  const rain = f.rain(d.rainToFill);
  const storm = f.fillsIn(d.fillsInYears);
  const fills = f.esc(storm.text);
  let where;
  if (d.target >= 0 && d.targetLowLngLat) {
    const dist = f.distance(f.metres([d.lon, d.lat], d.targetLowLngLat));
    where = `When full, it overflows into another depression ${inline(dist)} to the ${f.compass([d.lon, d.lat], d.targetLowLngLat)}${d.chainSteps > 1 ? `, and on through ${d.chainSteps - 1} more` : ""}${d.chainCut ? ", then on further than is drawn" : d.chainLeaves ? ", then off the mapped land (into open water or past the edge of the data)" : ""}.`;
  } else {
    where = "When full, its water leaves the mapped land: into open water (a river, lake or the sea) or past the edge of the data.";
  }
  const road = d.road === 2
    ? `<span class="badge solid" title="Its lowest point is ${f.int(d.roadM)} m from a major road (US Census TIGER 2024) and it lies along the road: a road cut, underpass or ramp. Storm drains usually empty these, but drains are not in the data, and underpasses can still flood when drains are overwhelmed.">Road cut or underpass</span>`
    : d.road === 1 ? `<span class="badge" title="Its lowest point is ${f.int(d.roadM)} m from a major road (US Census TIGER 2024): storm drains may empty it; drains are not in the data.">On a major road</span>` : "";
  const status = d.landlocked && !d.road
    ? `<span class="badge solid" title="No sign of drainage in mapped lines: no stream, ditch or canal within 30 m of its lowest point, and no highway or ramp within 30 m of where it overflows. Not proof that none exists.">Landlocked</span>`
    : d.drained ? `<span class="badge" title="A mapped stream, ditch or canal within 30 m of its lowest point, or a highway or ramp within 30 m of where it overflows">Likely drained</span>` : "";
  const shallow = d.depth < 1
    ? `<span class="badge" title="The 30 m elevation model's vertical error is about 0.5 to 1 m: a depression this shallow may not exist on the ground">Shallow</span>` : "";
  const up = d.upstream || d.receivesOutside
    ? `<dt>Depressions overflowing into it</dt><dd>${f.int(d.upstream)}${d.receivesOutside ? " <small>and some from neighbouring areas</small>" : ""}</dd>`
    : "";
  return `<div class="kicker"><span class="label">Depression</span>${CLOSE}</div>
    <h2>Holds <em>${f.esc(holds.main)}</em> of water</h2>
    <div class="row">${road}${status}${shallow}</div>
    <p class="say">Water can stand up to <strong>${inline(deep)}</strong> deep over <strong>${inline(wide)}</strong>. ${fills}</p>
    <p class="say">${where}</p>
    ${extra}
    ${mapKey(d)}
    <dl class="facts">
      <dt>Depth when full <small>(below its rim)</small></dt><dd>${both(deep)}</dd>
      ${d.extras ? `<dt>Rim, where water spills out <small>(above sea level, NAVD 88)</small></dt><dd>${both(f.elevation(d.rimM))}</dd>
      <dt>Lowest ground <small>(above sea level)</small></dt><dd>${both(f.elevation(d.groundM))}</dd>
      <dt>Nearest major road <small>(from the lowest point; Census TIGER 2024)</small></dt><dd>${Number.isFinite(d.roadM) ? both(f.distance(d.roadM)) : "over 100 m"}</dd>` : ""}
      <dt>Area when full</dt><dd>${both(wide)}</dd>
      <dt>Volume when full</dt><dd>${both(holds)}</dd>
      <dt>Land draining into it</dt><dd>${both(f.area(d.catchment))}</dd>
      <dt>Curve number <small>(how much rain runs off, 0-100)</small></dt><dd>${Number.isFinite(d.curveNumber) ? d.curveNumber.toFixed(0) : "–"}</dd>
      <dt>Storm that fills it <small>(24 h, NOAA Atlas 14, with upstream water)</small></dt><dd>${f.esc(storm.short)}</dd>
      <dt>Rain that fills it from its own land alone</dt><dd>${both(rain)}</dd>
      ${up}
      <dt>Lowest point <small>(centre of its H3 res-10 cell)</small></dt><dd>≈ ${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}<small><button class="copy" data-copy="${f.esc(d.low)}" title="Copy the cell id">${f.esc(d.low)}</button></small></dd>
      <dt>Dataset id</dt><dd><button class="copy" data-copy="${f.esc(d.id)}" title="Copy">${f.esc(d.id)}</button></dd>
    </dl>
    <p class="note">Depths are measured down from the depression's rim, the level at which water spills out (USGS 3DEP 30 m bare-earth elevation; vertical error about 0.5 to 1 m). Storm drains, sewers, culverts and pumps are not in the data: in cities, many deep depressions are road cuts, underpasses or ramps that drains keep dry.</p>
    <div class="row"><button class="btn" data-act="zoom-dep">Zoom to it</button><button class="btn" data-act="share">Copy link</button></div>
    ${source("depression")}`;
}

function mapKey(d) {
  const rows = [`<li><i class="k k-sel" aria-hidden="true"></i><span><strong>Outlined hexagons:</strong> this depression.
    ${d.cellCount > 1 ? `It is bigger than one hexagon, so it covers ${f.int(d.cellCount)} of them.` : "It fits in one hexagon."}</span></li>`];
  if (d.chainSteps) rows.push(`<li><i class="k k-spill" aria-hidden="true"></i><span><strong>Blue arrows:</strong> where its water goes
    once it is full, through ${d.chainSteps === 1 ? "the next depression" : `${d.chainSteps} depressions in turn`}${d.chainCut ? ", then on further than is drawn" : d.chainLeaves ? ", then off the mapped land (into open water or past the edge of the data)" : ""}. Click a dot to open that depression.</span></li>`);
  if (d.upstream) rows.push(`<li><i class="k k-up" aria-hidden="true"></i><span><strong>Grey dotted lines:</strong> ${d.upstream === 1 ? "a depression" : `${f.int(d.upstream)} depressions`}
    that overflow into this one. Click a line to open it.</span></li>`);
  const how = d.chainSteps || d.upstream ? `<details class="how"><summary>How are these lines worked out?</summary>
    <p>They come from the elevation model, not from observed floods. The h3-lowpoints library
    analysed the USGS 3DEP 30 m elevation model of the whole country: when a depression is full,
    its water leaves at the lowest point of its rim and runs downhill, each step to the steepest
    neighbouring cell (the standard D8 method), until it reaches another depression. That one is
    next on the path.</p>
    <p>For clarity the lines are drawn <strong>straight</strong> between the depressions' lowest
    points; the real route follows the ground. Storm drains, pipes and culverts are not in the
    elevation model, so in towns the water may really go elsewhere.</p>
    <p><a href="https://ujjawal-01.github.io/h3-lowpoints/definition.html#catchments-and-spill" target="_blank" rel="noopener">The exact rules</a></p>
  </details>` : "";
  return `<div class="mapkey"><span class="label">On the map</span><ul>${rows.join("")}</ul>${how}</div>`;
}

// Where every number comes from, under every view
export function source(kind) {
  if (kind === "storm") return `<div class="source"><span class="label">Source</span>
    <p>Run in your browser with the same equations as the open-source h3-lowpoints library (checked to agree within 1e-6), on the national dataset version 0.3, <a href="https://doi.org/10.5281/zenodo.23731345" target="_blank" rel="noopener">doi:10.5281/zenodo.23731345</a>. Storm depths: NOAA Atlas 14.</p>
    <p><a href="https://ujjawal-01.github.io/h3-lowpoints/limitations.html" target="_blank" rel="noopener">Limits</a> ·
      <a href="https://ujjawal-01.github.io/h3-lowpoints/evidence.html" target="_blank" rel="noopener">Tested against floods</a> ·
      <button class="btn link" data-act="disclaimer">Not advice: disclaimer</button></p></div>`;
  const what = kind === "depression"
    ? "Computed by the open-source h3-lowpoints library from the USGS 3DEP 30 m elevation model, with land cover (Annual NLCD 2024), soils (USDA gNATSGO) and storms (NOAA Atlas 14)."
    : "Summed from every depression in the national dataset, computed from the USGS 3DEP 30 m elevation model.";
  return `<div class="source"><span class="label">Source</span>
    <p>${what} Dataset version 0.3, <a href="https://doi.org/10.5281/zenodo.23731345" target="_blank" rel="noopener">doi:10.5281/zenodo.23731345</a>.</p>
    <p><a href="https://ujjawal-01.github.io/h3-lowpoints/definition.html" target="_blank" rel="noopener">Method</a> ·
      <a href="https://ujjawal-01.github.io/h3-lowpoints/limitations.html" target="_blank" rel="noopener">Limits</a> ·
      <a href="https://ujjawal-01.github.io/h3-lowpoints/evidence.html" target="_blank" rel="noopener">Tested against floods</a> ·
      <button class="btn link" data-act="disclaimer">Not advice: disclaimer</button></p></div>`;
}

export function loading(what) {
  return `<div class="kicker"><span class="label">${f.esc(what)}</span>${CLOSE}</div><p class="muted">Loading…</p>`;
}

export function failed(msg) {
  return `<div class="kicker"><span class="label">Could not load</span>${CLOSE}</div><p>${f.esc(msg)}</p>
    <div class="row"><button class="btn" data-act="retry">Try again</button></div>`;
}

// Show all depressions, or hide those likely drained (applies at street and city scale)
function showControl(show) {
  return `<div class="show"><span class="label">Show</span>
    <div class="seg" role="group" aria-label="Which depressions to show">
      <button data-act="show-all" aria-pressed="${show === "all"}">All depressions</button>
      <button data-act="show-undrained" aria-pressed="${show === "undrained"}">Hide likely drained</button></div>
    <p class="note">Likely drained: a mapped stream, ditch or canal within 30 m of the lowest point, a major road within 30 m of
      where it overflows, or a road cut or underpass (lowest point on a major road). Applies at city and street scale; the
      country summaries count every depression.</p></div>`;
}

// ------------------------------------------------------------------ the rain simulator
// The view's result (figures for the step the timeline is on; range and likely-drained at the end)
// and one depression's gauge. Numbers that follow the timeline carry data-sv and are updated in
// place while the storm plays (stormNumbers), so the panel does not flicker or lose its scroll.

export function stormResult(c) {
  const sm = c.summary;
  if (!sm) return `<div class="kicker"><span class="label">Rain simulator</span>${STORM_CLOSE}</div><p class="muted">Running the storm…</p>`;
  if (!sm.depressions) {
    return `<div class="kicker"><span class="label">Rain simulator · this view</span>${STORM_CLOSE}</div>
      <p class="say">No depressions have their lowest point in this view${c.show === "undrained" ? " (likely drained ones are hidden)" : ""}. Move the map or zoom out a little.</p>
      ${assumptions(c)}`;
  }
  if (sm.unknown === sm.depressions) {
    const why = c.storm.kind === "rarity" && !Number.isFinite(sm.rainMax)
      ? "NOAA Atlas 14, which gives the storm's depth, does not cover this place (Washington and Oregon). Choose the storm by how much rain instead."
      : "land cover or soil is missing upstream of them, so their runoff cannot be worked out.";
    return `<div class="kicker"><span class="label">Rain simulator · this view</span>${STORM_CLOSE}</div>
      <h2>${f.esc(cap(stormWordsShort(c)))}</h2>
      <p class="say">Not known for the ${f.int(sm.depressions)} depressions in this view: ${why}</p>
      ${assumptions(c)}${showControl(c.show)}${source("storm")}`;
  }
  const end = sm.byStep[STEPS - 1];
  const rng = ["dry", "average", "wet"].filter((g) => sm.range[g]).map((g) => `<dt>${GROUND[g]} ground before${g === c.storm.condition ? " <small>(chosen)</small>" : ""}</dt><dd>${f.int(sm.range[g].fill)} full · ${f.int(sm.range[g].overflow)} overflow</dd>`).join("");
  const firsts = sm.first.length ? `<div class="firsts"><span class="label">Fill earliest <small>(largest first)</small></span><ol>${sm.first.map((x) =>
    `<li><button class="btn link" data-act="open-dep" data-r="${f.esc(x.region)}" data-i="${x.i}">Holds ${f.esc(f.volume(x.volume).main)}</button>
      <small>full by ${x.step + 1} of 16 parts of the storm</small></li>`).join("")}</ol></div>` : "";
  const t = at(sm, c.step);
  return `<div class="kicker"><span class="label">Rain simulator · this view</span>${STORM_CLOSE}</div>
    <h2>${f.esc(cap(stormWordsShort(c)))}</h2>
    <p class="say" data-sv="sentence">${sentence(c, c.step)}</p>
    ${c.readout ? `<p class="note phone-only">${c.readout}</p>` : ""}
    <div class="stats" role="list">
      <div role="listitem"><span class="label">Full</span><span class="v" data-sv="fill">${f.int(t.fill)}</span></div>
      <div role="listitem"><span class="label">Overflowing</span><span class="v" data-sv="overflow">${f.int(t.overflow)}</span></div>
      <div role="listitem"><span class="label">Hold water</span><span class="v" data-sv="hold">${f.int(t.holdWater)}</span></div>
    </div>
    <dl class="facts">
      <dt>Depressions in this view <small>(lowest point inside the map)</small></dt><dd>${f.int(sm.depressions)}</dd>
      <dt>Ground under water</dt><dd data-sv="area">${both(f.area(t.wetAreaM2))}</dd>
      <dt>Water held</dt><dd data-sv="held">${both(f.volume(t.heldM3))}</dd>
      ${sm.unknown ? `<dt>Not known <small>(no NOAA Atlas 14, or no land cover or soil upstream)</small></dt><dd>${f.int(sm.unknown)}</dd>` : ""}
      ${end.fill ? `<dt>Of those full at the end, likely drained <small>(stream, ditch or major road: drains may keep them dry)</small></dt><dd>${f.int(sm.fillLikelyDrained)} of ${f.int(end.fill)}</dd>` : ""}
    </dl>
    ${rng ? `<div class="range"><span class="label">At the end, by the ground before the storm</span><dl class="facts">${rng}</dl></div>` : ""}
    ${firsts}
    ${waterLegend(c.res, f.units.us)}
    ${assumptions(c)}
    ${showControl(c.show)}
    ${source("storm")}`;
}

const GROUND = { dry: "Dry", average: "Average", wet: "Wet" };
const STORM_CLOSE = `<button class="close" data-act="storm-close" aria-label="Close the rain simulator"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.6"/></svg></button>`;
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const NONE = { fill: 0, overflow: 0, holdWater: 0, wetAreaM2: 0, heldM3: 0 };
const at = (sm, step) => (step < 0 ? NONE : sm.byStep[step]);

function stormWordsShort(c) {
  const s = c.storm;
  if (s.kind === "rarity") return `the ${f.int(s.years)}-year ${s.hours}-hour storm`;
  const r = f.rain(s.mm);
  return `${r.main} of rain in ${s.hours} hour${s.hours === 1 ? "" : "s"}`;
}

function sentence(c, step) {
  const sm = c.summary, t = at(sm, step), n = sm.depressions - sm.unknown;
  if (step < 0) return "Before the rain: every depression is empty. Press Play to watch them fill.";
  const so = step === STEPS - 1 ? "By the end of the storm" : `After ${step + 1} of its 16 parts`;
  return `${so}, ${f.int(t.fill)} of the ${f.int(n)} depressions in this view are full${t.overflow ? `, and ${f.int(t.overflow)} overflow into the next` : ""}; ${f.int(t.holdWater)} hold some water.`;
}

// the figures for one step, written in place
export function stormNumbers(root, c) {
  if (!root || !c.summary || !c.summary.depressions) return;
  const t = at(c.summary, c.step), set = (k, html) => { const n = root.querySelector(`[data-sv="${k}"]`); if (n) n.innerHTML = html; };
  set("sentence", sentence(c, c.step));
  set("fill", f.int(t.fill)); set("overflow", f.int(t.overflow)); set("hold", f.int(t.holdWater));
  set("area", both(f.area(t.wetAreaM2))); set("held", both(f.volume(t.heldM3)));
}

function waterLegend(res, us) {
  const street = res === 10;
  const L = street ? (us ? WATER.depth.labelsFt : WATER.depth.labelsM) : WATER.share.labels;
  const title = street ? `Water depth now (${us ? "ft" : "m"})` : "Share of each hexagon under water now";
  return `<div class="legend water" aria-label="${title}"><span class="label">${title}</span>
    <div class="ramp smooth" style="background:linear-gradient(90deg, ${WATER.colours.join(", ")})"></div>
    <div class="ramp-labels">${L.map((l) => `<span>${l}</span>`).join("")}</div>
    <p class="note">Grey beneath: how deep each fills when full. Hatched: not known.</p></div>`;
}

// What every simulator figure assumes (spec check 9)
function assumptions(c) {
  const g = c.storm.condition;
  return `<details class="how"><summary>What this assumes</summary>
    <ul class="assume">
      <li>What a depression holds depends only on the rain fallen so far. The hours follow NOAA Atlas 14's median pattern of how rain builds up in this region's storms (1- to 3-hour storms use its 6-hour pattern and 48-hour storms its 24-hour one, as the library does); where NOAA has no pattern, the timeline is rain fallen, not time.</li>
      <li>Rain reaches each depression as it falls (no travel time over the ground), so times are the earliest it can fill.</li>
      <li>Runoff by the SCS curve number method for ${g} ground before the storm (land cover Annual NLCD 2024, soils USDA gNATSGO). Water from neighbouring areas is included.</li>
      <li>No storm drains, sewers or pumps, and no water soaking away once it is in a depression.</li>
      <li>Likely drained depressions fill like the rest${c.show === "undrained" ? "; hiding them takes them off the map and out of these counts, not out of the flow" : ""}.</li>
      <li>Ground from the USGS 3DEP 30 m elevation model (vertical error about 0.5 to 1 m). While the storm plays, the map blends between its 16 exact steps; every number shown is an exact step.</li>
    </ul></details>`;
}

// One depression in the storm: how full, how deep, when it fills (to within 1/16 of the storm)
export function gauge(g, c) {
  if (!g) return `<div class="gauge"><span class="label">In this storm</span><p class="muted">Running…</p></div>`;
  return `<div class="gauge" data-gauge><span class="label">In ${f.esc(stormWordsShort(c))}</span><div data-gauge-body>${gaugeBody(g, c)}</div></div>`;
}

export function gaugeBody(g, c) {
  if (!Number.isFinite(g.rainMm) || Number.isNaN(g.steps[STEPS - 1].full)) {
    return `<p class="muted">Not known here: ${Number.isFinite(g.rainMm) ? "land cover or soil is missing upstream" : "NOAA Atlas 14 does not cover this place"}.</p>`;
  }
  const k = c.step, st = k < 0 ? { full: 0, depth: 0, overflowing: false } : g.steps[k];
  const pct = Math.min(1, Math.max(0, st.full));
  const so = f.rain(fallen(g.rainMm, k)), all = f.rain(g.rainMm);
  const surface = st.depth > 0 && Number.isFinite(g.groundM)
    ? `; water surface ${f.esc(f.elevation(g.groundM + st.depth).main)} above sea level${Number.isFinite(g.rimM) ? ` (rim ${f.esc(f.elevation(g.rimM).main)})` : ""}` : "";
  const fills = g.fillShare != null ? fillTime(g, c) : g.fillStep >= 0
    ? `${g.fillStep === 0 ? `Full within the first ${f.esc(f.rain(fallen(g.rainMm, 0)).main)} of rain here` : `Full once ${f.esc(f.rain(fallen(g.rainMm, g.fillStep - 1)).main)} to ${f.esc(f.rain(fallen(g.rainMm, g.fillStep)).main)} has fallen here`}${g.spillStep >= 0 ? ", and then it overflows" : ""}.`
    : `Not full by the end of the storm: it holds ${f.pct(g.steps[STEPS - 1].full, 0)} of what it can.`;
  return `<div class="meter" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct * 100)}" aria-label="How full">
      <span style="width:${(pct * 100).toFixed(1)}%"></span></div>
    <p class="say"><strong>${f.pct(pct, 0)} full</strong> after ${f.esc(so.main)} of ${f.esc(all.main)} here: ${st.depth > 0 ? `${f.esc(f.depth(st.depth).main)} deep at its lowest point${surface}` : "dry"}${st.overflowing ? "; <strong>overflowing</strong>" : ""}.</p>
    <p class="note">${fills}</p>`;
}

// When it fills: the exact rain share (worker fillShare), turned into hours on NOAA's curve for its
// location: the typical (median of all storms) and the range over storms by the quarter with most rain
function fillTime(g, c) {
  const mm = f.rain(g.rainMm * g.fillShare).main, hours = c.storm.hours, t = g.temporal;
  const over = g.spillStep >= 0 ? " Then it overflows." : "";
  if (!t || !t.covered) return `Full once ${f.esc(mm)} has fallen here. When that is cannot be said here (no NOAA Atlas 14 timing for this place).${over}`;
  const at = (k) => timeAt(t.curves[k], g.fillShare) * hours;
  const quarters = ["first", "second", "third", "fourth"].map(at);
  const lo = Math.min(...quarters), hi = Math.max(...quarters);
  // the range over NOAA's four groups of storms (by the quarter with most rain): which group gives
  // which end is not claimed
  const range = hi - lo >= 1 / 60 ? ` In real storms it varies: from ${f.esc(duration(lo))} to ${f.esc(duration(hi))}, depending on when in the storm the heaviest rain falls.` : "";
  return `Full after about <strong>${f.esc(duration(at("all")))}</strong> of a typical ${hours}-hour storm here, once ${f.esc(mm)} has fallen.${range}${over}
    <small class="why">Earliest possible: rain reaches it as it falls. Timing: NOAA Atlas 14${t.published !== hours ? ` ${t.published}-hour pattern, stretched to ${hours} hours` : ""}, region ${f.esc(t.name)}.</small>`;
}
