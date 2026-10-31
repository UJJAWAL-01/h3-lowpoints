// The guide: a short walk through the app, one part at a time, reopened any time with "Guide".
// Each step: { title, text, target?: () => Element | DOMRect | null, before?: async () => void, skip?: () => bool }

export function createGuide(steps, { onEnd } = {}) {
  let i = -1, card, spot, catcher, lastFocus = null, running = false;

  function build() {
    catcher = el("div", "guide-catcher");
    spot = el("div", "guide-spot");
    card = el("div", "guide-card");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "guide-title");
    card.tabIndex = -1;
    document.body.append(catcher, spot, card);
    card.addEventListener("click", (e) => {
      const b = e.target.closest("[data-g]");
      if (!b) return;
      if (b.dataset.g === "next") go(i + 1);
      else if (b.dataset.g === "back") go(i - 1, -1);
      else stop();
    });
    window.addEventListener("resize", onResize);
    document.addEventListener("keydown", keys);
  }

  let resizeTimer = 0;
  function onResize() {                       // jump (no slide), and again once panels have moved
    if (card) card.style.transition = "none";
    requestAnimationFrame(place);
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { place(); if (card) requestAnimationFrame(() => { if (card) card.style.transition = ""; }); }, 400);
  }

  function keys(e) {
    if (!running) return;
    if (e.key === "Escape") { e.preventDefault(); stop(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(i - 1, -1); }
  }

  const visible = () => steps.map((s, k) => k).filter((k) => !(steps[k].skip && steps[k].skip()));

  async function go(k, dir = 1) {
    while (k >= 0 && k < steps.length && steps[k].skip && steps[k].skip()) k += dir;
    if (k < 0) return;
    if (k >= steps.length) { stop(); return; }
    i = k;
    const s = steps[k];
    card.classList.add("busy");
    if (s.before) { try { await s.before(); } catch { /* the step still shows */ } }
    if (i !== k) return;
    card.classList.remove("busy");
    const order = visible(), n = order.indexOf(k) + 1;
    card.innerHTML = `<div class="guide-head"><span class="label">Guide · ${n} of ${order.length}</span>
        <button class="guide-x" data-g="skip" aria-label="Close the guide">Skip</button></div>
      <h2 id="guide-title">${s.title}</h2><p>${s.text}</p>
      <div class="guide-nav">${n > 1 ? `<button class="btn" data-g="back">Back</button>` : ""}
        <button class="btn primary" data-g="next">${n === order.length ? "Done" : "Next"}</button></div>`;
    place();
    card.focus({ preventScroll: true });
  }

  function place() {
    if (!running) return;
    const s = steps[i];
    let t = s && s.target ? s.target() : null;
    const r = t instanceof Element ? t.getBoundingClientRect() : t;
    const vw = window.innerWidth, vh = window.innerHeight, phone = vw < 720;
    if (r && r.width) {
      const pad = 6;
      Object.assign(spot.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`,
        height: `${r.height + 2 * pad}px`, opacity: 1 });
    } else {
      Object.assign(spot.style, { left: `${vw / 2}px`, top: `${vh / 2}px`, width: "0px", height: "0px", opacity: 1 });
    }
    const cw = Math.min(360, vw - 32);
    card.style.width = `${cw}px`;
    const ch = card.offsetHeight;
    let x, y;
    if (!r || !r.width) { x = (vw - cw) / 2; y = (vh - ch) / 2; }
    else if (phone) { x = (vw - cw) / 2; y = r.top + r.height / 2 > vh / 2 ? Math.max(16, r.top - ch - 16) : Math.min(vh - ch - 16, r.bottom + 16); }
    else if (r.right + 20 + cw < vw - 16) { x = r.right + 20; y = clamp(r.top, 16, vh - ch - 16); }
    else if (r.left - 20 - cw > 16) { x = r.left - 20 - cw; y = clamp(r.top, 16, vh - ch - 16); }
    else { x = clamp(r.left, 16, vw - cw - 16); y = r.bottom + 16 + ch < vh ? r.bottom + 16 : Math.max(16, r.top - ch - 16); }
    x = clamp(x, 8, Math.max(8, vw - cw - 8));
    y = clamp(y, 8, Math.max(8, vh - ch - 8));
    card.style.left = `${Math.round(x)}px`;
    card.style.top = `${Math.round(y)}px`;
  }

  function start(k = 0) {
    if (!card) build();
    lastFocus = document.activeElement;
    running = true;
    document.body.classList.add("guiding");
    go(k);
  }

  function stop() {
    if (!running) return;
    running = false;
    document.body.classList.remove("guiding");
    for (const n of [card, spot, catcher]) n.remove();
    card = spot = catcher = null;
    window.removeEventListener("resize", onResize);
    document.removeEventListener("keydown", keys);
    try { localStorage.setItem("lowpoints.guided", "1"); } catch { /* ignore */ }
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    if (onEnd) onEnd();
  }

  return { start, stop, get running() { return running; }, refresh: place };
}

export function seenGuide() {
  try { return localStorage.getItem("lowpoints.guided") === "1"; } catch { return true; }
}

function el(tag, cls) {
  const n = document.createElement(tag);
  n.className = cls;
  return n;
}
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
