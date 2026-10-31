// Numbers for people: metric first with US units in brackets, or the other way round.

let us = false;
try { us = localStorage.getItem("lowpoints.units") === "us"; } catch { /* storage blocked */ }

export const units = {
  get us() { return us; },
  toggle() {
    us = !us;
    try { localStorage.setItem("lowpoints.units", us ? "us" : "metric"); } catch { /* ignore */ }
    return us;
  },
};

const nf = (d) => new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
const sig = (x) => {
  const a = Math.abs(x);
  return a >= 100 ? nf(0).format(x) : a >= 10 ? nf(1).format(x) : nf(2).format(x);
};
export const int = (x) => nf(0).format(Math.round(x));
export const pct = (x, d = 1) => (Number.isFinite(x) ? `${nf(d).format(x * 100)}%` : "–");

function pair(metric, usText) {
  return us ? { main: usText, alt: metric } : { main: metric, alt: usText };
}

export function depth(m) {
  return pair(`${sig(m)} m`, `${sig(m * 3.28084)} ft`);
}
export function rain(mm) {
  if (!Number.isFinite(mm)) return { main: "–", alt: "" };
  return pair(`${int(mm)} mm`, `${nf(1).format(mm / 25.4)} in`);
}
export function area(m2) {
  const metric = m2 >= 1e6 ? `${sig(m2 / 1e6)} km²` : m2 >= 1e4 ? `${sig(m2 / 1e4)} ha` : `${int(m2)} m²`;
  const acres = m2 / 4046.856;
  const usText = acres >= 640 ? `${sig(acres / 640)} sq mi` : `${sig(acres)} acres`;
  return pair(metric, usText);
}
export function volume(m3) {
  const metric = m3 >= 1e9 ? `${sig(m3 / 1e9)} km³` : m3 >= 1e6 ? `${sig(m3 / 1e6)} million m³` : `${int(m3)} m³`;
  const gal = m3 * 264.172;
  const usText = gal >= 1e12 ? `${sig(gal / 1e12)} trillion gal` : gal >= 1e9 ? `${sig(gal / 1e9)} billion gal`
    : gal >= 1e6 ? `${sig(gal / 1e6)} million gal` : `${int(gal)} gal`;
  return pair(metric, usText);
}
export function distance(m) {
  const metric = m >= 1000 ? `${sig(m / 1000)} km` : `${int(m)} m`;
  const ft = m * 3.28084;
  const usText = ft >= 2640 ? `${sig(m / 1609.344)} mi` : `${int(ft)} ft`;
  return pair(metric, usText);
}

// fills_in_years: the smallest NOAA Atlas 14 return period (1, 2, 5 ... 1000 years) whose 24-hour
// storm fills the depression, counting overflow from upstream; Infinity when none does
export function fillsIn(y) {
  if (y === Infinity) return { short: "none", text: "Not even the 1,000-year 24-hour storm fills it." };
  if (!Number.isFinite(y)) return { short: "unknown", text: "What storm fills it is not known here (NOAA Atlas 14 does not cover Washington and Oregon; elsewhere, land cover or soil is missing upstream)." };
  if (y <= 1) return { short: "1-year", text: "The 24-hour storm expected about once a year here fills it, counting water from upstream." };
  return { short: `${int(y)}-year`, text: `The 24-hour storm expected once in ${int(y)} years here fills it, counting water from upstream.` };
}

export function compass(from, to) {
  const [lo1, la1] = from, [lo2, la2] = to;
  const k = Math.PI / 180;
  const y = Math.sin((lo2 - lo1) * k) * Math.cos(la2 * k);
  const x = Math.cos(la1 * k) * Math.sin(la2 * k) - Math.sin(la1 * k) * Math.cos(la2 * k) * Math.cos((lo2 - lo1) * k);
  const b = (Math.atan2(y, x) / k + 360) % 360;
  return ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"][Math.round(b / 45) % 8];
}

export function metres(from, to) {
  const k = Math.PI / 180, R = 6371008.8;
  const dla = (to[1] - from[1]) * k, dlo = (to[0] - from[0]) * k;
  const a = Math.sin(dla / 2) ** 2 + Math.cos(from[1] * k) * Math.cos(to[1] * k) * Math.sin(dlo / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
