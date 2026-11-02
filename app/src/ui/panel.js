// What the side panel (or bottom sheet) says. Plain sentences first, the numbers below them.

import { EARTH } from "../map/heights.js";
import { legendHtml } from "../map/layers.js";
import * as f from "./format.js";

const both = (x) => `${f.esc(x.main)}${x.alt ? ` <small>${f.esc(x.alt)}</small>` : ""}`;
const inline = (x) => `${f.esc(x.main)}${x.alt ? ` (${f.esc(x.alt)})` : ""}`;
const CLOSE = `<button class="close" data-act="close" aria-label="Close details"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.6"/></svg></button>`;

export function intro(nat, mode, res, heightNote = "", earth = false) {
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

export function depression(d) {
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
  const status = d.landlocked
    ? `<span class="badge solid" title="No sign of drainage in mapped lines: no stream, ditch or canal within 30 m of its lowest point, and no highway or ramp within 30 m of where it overflows. Not proof that none exists.">Landlocked</span>`
    : d.drained ? `<span class="badge" title="A mapped stream, ditch or canal within 30 m of its lowest point, or a highway or ramp within 30 m of where it overflows">Likely drained</span>` : "";
  const shallow = d.depth < 1
    ? `<span class="badge" title="The 30 m elevation model's vertical error is about 0.5 to 1 m: a depression this shallow may not exist on the ground">Shallow</span>` : "";
  const up = d.upstream || d.receivesOutside
    ? `<dt>Depressions overflowing into it</dt><dd>${f.int(d.upstream)}${d.receivesOutside ? " <small>and some from neighbouring areas</small>" : ""}</dd>`
    : "";
  return `<div class="kicker"><span class="label">Depression</span>${CLOSE}</div>
    <h2>Holds <em>${f.esc(holds.main)}</em> of water</h2>
    <div class="row">${status}${shallow}</div>
    <p class="say">Water can stand up to <strong>${inline(deep)}</strong> deep over <strong>${inline(wide)}</strong>. ${fills}</p>
    <p class="say">${where}</p>
    ${mapKey(d)}
    <dl class="facts">
      <dt>Depth when full</dt><dd>${both(deep)}</dd>
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
