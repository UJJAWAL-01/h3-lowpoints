// What the studies found, in plain words, from docs/evidence.md (tests/test_docs.py checks that
// every number here appears there). One line per panel kind, and the table in About.

import { DOCS_URL } from "../config.js";

export const EVIDENCE_URL = `${DOCS_URL}/evidence.html`;

// Shown under each panel's source
export const NOTE = {
  depression: "Tested against flood reports: depressions that fill in a 2-year storm drew more reports in New York and Houston (2015), but not clearly in four other cities. Landlocked ones did not draw more reports than drained ones; in Houston and Austin it was the opposite.",
  cell: "Compared with 1 m lidar in ten 5 × 5 km windows: outside New York, the 30 m data find 14% to 56% of the depressed area lidar finds, and most of what they find is real. In New York neither 30 m nor 10 m data resemble lidar.",
  storm: "Tested in ten real storms in six cities: predicted standing water marked where flood reports came from, but only modestly (AUC 0.575, where 0.5 is no better than where people live), and hardly in Chicago. Assumed storm drains made it worse.",
};

// The table in About: question, what was found, how strong
export const ROWS = [
  ["Do flood reports gather where a depression fills in a 2-year storm?", "Yes in New York and Houston (2015); not shown in four other cities.", "Mixed"],
  ["Do landlocked depressions gather more reports than drained ones?", "No: the opposite in Houston and Austin, the expected way in New Orleans.", "Contradicted where clear"],
  ["Does predicted standing water at a storm's peak mark where reports come from?", "Yes, modestly (AUC 0.575 in four cities held out for testing); hardly in Chicago.", "Supported, weak"],
  ["Does following the storm hour by hour beat the storm total?", "No measurable difference.", "Inconclusive"],
  ["Do assumed storm drains help?", "No: every uniform drain rule made the ranking worse.", "Clear"],
  ["Does finer terrain help?", "10 m beat 30 m against lidar and, slightly, against reports; 1 m was not better than 10 m against reports.", "10 m: supported"],
  ["Does machine learning beat the physical score in unseen cities?", "Not the model tested, so it was not kept.", "Not kept"],
];

export const CAVEAT = "Reports record where people noticed and reported flooding, not where water stood. Ten storms in six cities are few, and association is not cause.";

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");

export function aboutHtml() {
  return `<table class="evidence"><thead><tr><th scope="col">Question</th><th scope="col">What was found</th><th scope="col">Strength</th></tr></thead><tbody>
    ${ROWS.map(([q, a, s]) => `<tr><td>${esc(q)}</td><td>${esc(a)}</td><td>${esc(s)}</td></tr>`).join("")}</tbody></table>
    <p class="muted">AUC is the chance that a place with a flood report scores higher than the home of a randomly chosen resident: 0.5 is no better than where people live. ${esc(CAVEAT)}
      <a href="${EVIDENCE_URL}" target="_blank" rel="noopener">Every study, with its numbers</a>.</p>`;
}

export function noteHtml(kind) {
  const t = NOTE[kind];
  return t ? `<p class="evidence-note"><span class="label">Evidence</span> ${esc(t)} <a href="${EVIDENCE_URL}" target="_blank" rel="noopener">More</a></p>` : "";
}
