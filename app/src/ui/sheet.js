// On phones the panel is a bottom sheet: a peek, half or fully open; drag the handle or tap it.

const PHONE = window.matchMedia("(max-width: 719px)");

export function setupSheet(panel, handle) {
  let state = "peek", startY = 0, startT = 0, dragging = false;

  function set(s) {
    state = s;
    panel.classList.toggle("open", s === "open");
    panel.classList.toggle("half", s === "half");
    panel.style.transform = "";
    handle.setAttribute("aria-expanded", String(s !== "peek"));
    handle.setAttribute("aria-label", s === "open" ? "Show less" : "Show more");
    if (s !== "open") panel.scrollTop = 0;
    document.body.classList.toggle("sheet-up", s !== "peek");
  }
  handle.addEventListener("click", () => { if (!dragging) set(state === "open" ? "peek" : "open"); });
  handle.addEventListener("pointerdown", (e) => {
    if (!PHONE.matches) return;
    startY = e.clientY; startT = performance.now(); dragging = false;
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener("pointermove", (e) => {
    if (!PHONE.matches || !handle.hasPointerCapture(e.pointerId)) return;
    const dy = e.clientY - startY;
    if (Math.abs(dy) > 6) dragging = true;
    if (!dragging) return;
    const h = panel.getBoundingClientRect().height;
    const base = state === "open" ? 0 : state === "half" ? 0.45 * h : h - peek();
    panel.style.transition = "none";
    panel.style.transform = `translateY(${Math.min(Math.max(base + dy, 0), h - peek())}px)`;
  });
  handle.addEventListener("pointerup", (e) => {
    if (!PHONE.matches || !handle.hasPointerCapture(e.pointerId)) return;
    handle.releasePointerCapture(e.pointerId);
    panel.style.transition = "";
    if (!dragging) return;
    const dy = e.clientY - startY, v = dy / Math.max(performance.now() - startT, 1);
    const order = ["open", "half", "peek"];
    let k = order.indexOf(state);
    if (v > 0.5 || dy > 80) k = Math.min(k + 1, 2);
    else if (v < -0.5 || dy < -80) k = Math.max(k - 1, 0);
    set(order[k]);
    setTimeout(() => { dragging = false; }, 0);
  });
  return { set, get state() { return state; }, get phone() { return PHONE.matches; } };
}

function peek() {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--sheet-peek")) || 140;
}
