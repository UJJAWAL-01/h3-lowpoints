// Falling rain over the map while a storm plays. Decoration only: it shows nothing measured (the
// water on the map is the model). Heavier for a more intense storm (rain per hour), it fades in
// when play starts and out when it stops; never shown when the visitor asks for reduced motion.
// A 2D canvas under the cards, pointer-events off, drawn only while it is visible.

const MAX_DROPS = 420;

export function createRain(reduced) {
  const canvas = document.createElement("canvas");
  canvas.className = "rain";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  let drops = [], level = 0, target = 0, want = 0, frame = 0, last = 0, w = 0, h = 0, dpr = 1;

  function size() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth; h = window.innerHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
  window.addEventListener("resize", () => { if (frame) size(); });

  const drop = (anyY) => ({ x: Math.random() * (w + 200) - 100, y: anyY ? Math.random() * h : -30 - Math.random() * 120,
    len: 10 + Math.random() * 16, v: 700 + Math.random() * 500, a: 0.26 + Math.random() * 0.26 });

  function tick(now) {
    const dt = Math.max(0, Math.min((now - last) / 1000, 0.12));   // (a first frame can be stamped before start)
    last = now;
    // ease the amount of rain towards the target (fade in 0.4 s, out 0.8 s)
    level = Math.max(0, Math.min(1, level + (target - level) * Math.min(1, dt * (target > level ? 6 : 3.5))));
    const n = Math.max(0, Math.round(level * want));
    while (drops.length < n) drops.push(drop(drops.length === 0 || level < 0.2));
    if (drops.length > n) drops.length = n;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.lineCap = "round"; ctx.lineWidth = 1.3;
    const sx = -0.22;                                      // a little wind
    for (const d of drops) {
      d.y += d.v * dt; d.x += d.v * dt * sx;
      if (d.y > h + 20 || d.x < -40) Object.assign(d, drop(false));
      ctx.strokeStyle = `rgba(29, 63, 158, ${d.a * Math.min(1, level * 1.4)})`;
      ctx.beginPath(); ctx.moveTo(d.x, d.y); ctx.lineTo(d.x - d.len * sx, d.y - d.len); ctx.stroke();
    }
    if (target === 0 && level < 0.01) { drops = []; ctx.clearRect(0, 0, w, h); frame = 0; canvas.style.opacity = "0"; return; }
    frame = requestAnimationFrame(tick);
  }

  return {
    // intensity 0..1
    start(intensity) {
      if (reduced()) return;
      want = Math.round(60 + (MAX_DROPS - 60) * Math.max(0, Math.min(1, intensity)));
      target = 1;
      if (!frame) { size(); canvas.style.opacity = "1"; last = performance.now(); frame = requestAnimationFrame(tick); }
    },
    stop() { target = 0; },
  };
}

// Rain per hour -> 0..1, on a log scale: 2 mm/h light, about 25 mm/h heavy, 100 mm/h and more a cloudburst
export function intensityOf(mm, hours) {
  const rate = mm / hours;
  if (!(rate > 0)) return 0;
  return Math.max(0, Math.min(1, Math.log(rate / 2) / Math.log(100 / 2)));
}
