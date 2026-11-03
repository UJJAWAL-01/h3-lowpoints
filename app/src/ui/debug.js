// Diagnostic panel (?debug=1): how smooth the map runs on this device and what takes the time,
// so a visitor (or the owner) can report real numbers. Stays on the device: nothing is sent; Copy
// puts the numbers on the clipboard. Updated twice a second over the last 3 seconds.

export function setupDebug({ map, state, views, stormBar }) {
  if (new URLSearchParams(location.search).get("debug") !== "1") return null;
  const box = document.createElement("div");
  box.className = "debug card";
  box.setAttribute("role", "status");
  document.body.appendChild(box);

  const frames = [], renders = [], ticks = [], longs = [];
  const keep = (arr, now) => { while (arr.length && now - arr[0][0] > 3000) arr.shift(); };
  // frame gaps (how smooth it looks)
  let last = performance.now();
  const loop = (now) => { frames.push([now, now - last]); last = now; requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
  // time the map spends drawing a frame (MapLibre's own render, main thread)
  const orig = map._render?.bind(map);
  if (orig) map._render = (t) => { const a = performance.now(); const r = orig(t); renders.push([a, performance.now() - a]); return r; };
  // our storm code per frame (storm-bar records it)
  const tickPoll = () => { const a = window.__stormTicks; if (a && a.length) { const now = performance.now(); for (const x of a.splice(0)) ticks.push([now, x]); } };
  window.__lowpointsDebug = true;
  // long main-thread pauses (over 50 ms)
  try {
    new PerformanceObserver((list) => { for (const e of list.getEntries()) longs.push([e.startTime, e.duration]); }).observe({ type: "longtask", buffered: false });
  } catch { /* not supported */ }

  const gl = map.painter?.context?.gl;
  let gpu = "unknown";
  try { const ext = gl.getExtension("WEBGL_debug_renderer_info"); gpu = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); } catch { /* hidden */ }

  const stats = (arr) => {
    const v = arr.map((x) => x[1]).sort((a, b) => a - b);
    if (!v.length) return null;
    return { n: v.length, median: v[v.length >> 1], p95: v[Math.min(v.length - 1, Math.floor(v.length * 0.95))], max: v[v.length - 1] };
  };
  const ms = (x) => (x == null ? "–" : `${x.toFixed(x < 10 ? 1 : 0)} ms`);
  let lastText = "";

  function update() {
    const now = performance.now();
    tickPoll();
    for (const a of [frames, renders, ticks, longs]) keep(a, now);
    const f = stats(frames), r = stats(renders), t = stats(ticks);
    const fps = f ? 1000 / f.median : 0, slow = frames.filter((x) => x[1] > 33).length;
    const c = map.getCanvas(), dpr = window.devicePixelRatio || 1;
    const water = views.water, wells = views.wells;
    const tri = (m) => [...m.values()].reduce((a, x) => a + (x.count || x.counts?.pit || 0), 0);
    const lines = [
      ["Frames", f ? `${fps.toFixed(0)} per second (gap: median ${ms(f.median)}, 95% ${ms(f.p95)}, worst ${ms(f.max)})` : "–"],
      ["Choppy frames", f ? `${slow} of ${f.n} over 33 ms` : "–"],
      ["Map drawing", r ? `median ${ms(r.median)}, 95% ${ms(r.p95)}, worst ${ms(r.max)}` : "–"],
      ["Storm code", t ? `median ${ms(t.median)}, worst ${ms(t.max)}` : "not playing"],
      ["Long pauses", longs.length ? `${longs.length} (worst ${ms(Math.max(...longs.map((x) => x[1])))})` : "none"],
      ["View", `${views.effective}, level ${state.res}, zoom ${map.getZoom().toFixed(1)}, tilt ${map.getPitch().toFixed(0)}°`],
      ["Drawn", `${state.regions.size} packs; water ${tri(water.meshes)} corners; wells ${tri(wells.meshes)} corners`],
      ["Storm", stormBar.active ? `${stormBar.playing ? "playing" : "paused"}, step ${stormBar.step + 1} of 16` : "off"],
      ["Screen", `${c.clientWidth}×${c.clientHeight} at ${dpr}× (${c.width}×${c.height} pixels drawn)`],
      ["Graphics", gpu],
    ];
    const text = lines.map(([k, v]) => `${k}: ${v}`).join("\n");
    if (text === lastText) return;
    lastText = text;
    box.innerHTML = `<div class="debug-head"><span class="label">Diagnostics (this device only)</span><button class="btn link" data-copy-debug>Copy</button></div>
      <dl>${lines.map(([k, v]) => `<dt>${k}</dt><dd>${v.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</dd>`).join("")}</dl>`;
  }
  setInterval(update, 500);
  box.addEventListener("click", async (e) => {
    if (!e.target.closest("[data-copy-debug]")) return;
    const text = `${lastText}\nBrowser: ${navigator.userAgent}\nPage: ${location.href}`;
    try { await navigator.clipboard.writeText(text); e.target.textContent = "Copied"; } catch { e.target.textContent = "Copy failed"; }
    setTimeout(() => { e.target.textContent = "Copy"; }, 1500);
  });
  return { stats: () => lastText };
}
