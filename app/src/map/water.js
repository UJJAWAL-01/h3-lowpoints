// The storm's water on the map: blue over each hexagon, by water depth (street scale) or the share
// of the hexagon under water (city scale), hatched where the water is not known. Flat view: a blue
// sheet over the hexagons. Columns view: a blue column rising inside each (see-through) column, to
// the same height rule as the columns (heights.js). A small WebGL2 layer like Wells: the 16 steps of
// every hexagon sit in one texture per pack, so playing the storm changes a few numbers per frame
// and nothing per hexagon.

import { DEFAULT_EXAGGERATION, SHARE_CAP, SHARE_M_PER_PCT } from "./heights.js";

// Played smoothly: the playhead t runs from -1 (no rain yet) to 15 (the whole storm); between two
// exact steps the drawn water is blended linearly (drawing only; every number shown is an exact step).

// Classes: street scale, water depth (m); city scale, share of the hexagon under water
export const WATER = {
  depth: { breaks: [0.05, 0.25, 0.5, 1, 2], labelsM: ["0", "0.05", "0.25", "0.5", "1", "2+"],
    labelsFt: ["0", "0.2", "0.8", "1.6", "3.3", "6.6+"] },
  share: { breaks: [0.0025, 0.01, 0.025, 0.05, 0.1], labels: ["0", "0.25", "1", "2.5", "5", "10%+"] },
  colours: ["#c9d5f1", "#a0b4e5", "#7491d6", "#4d6ec4", "#2c4fa6", "#163780"],
};

export function waterClass(w, level) {
  const b = level === 10 ? WATER.depth.breaks : WATER.share.breaks;
  let i = 0;
  while (i < b.length && w >= b[i]) i++;
  return i;
}

const VS = `#version 300 es
uniform mat4 u_matrix; uniform int u_s0; uniform int u_s1; uniform float u_frac; uniform sampler2D u_water;
uniform sampler2D u_prev; uniform float u_fade;          // the storm before, blended out over 0.4 s
uniform float u_breaks[5];
uniform int u_cols; uniform int u_level; uniform float u_exag; uniform float u_mPerPct; uniform float u_cap;
in vec3 a_pos;                // x, y (Mercator, from the mesh origin), hexagon index
in vec3 a_col;                // columns: 1 at the top / 0 at the foot, Mercator units per metre, wall light
flat out float v_light;
flat out float v_pos;         // position on the colour ramp, 0..5 (continuous); -1 unknown
flat out float v_alpha;
float at(sampler2D t, int k, int s) { return s < 0 ? 0.0 : texelFetch(t, ivec2(k % 1024, (k / 1024) * 16 + s), 0).r; }
void main() {
  int k = int(a_pos.z + 0.5);
  float w0 = at(u_water, k, u_s0), w1 = at(u_water, k, u_s1);
  if (u_fade < 1.0) {
    float p0 = at(u_prev, k, u_s0), p1 = at(u_prev, k, u_s1);
    if (!(p0 < -0.5 || p1 < -0.5 || w0 < -0.5 || w1 < -0.5)) { w0 = mix(p0, w0, u_fade); w1 = mix(p1, w1, u_fade); }
  }
  v_light = u_cols == 1 ? a_col.z : 1.0;
  if (w0 < -0.5 || w1 < -0.5) {
    if (u_cols == 1) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }                 // unknown: no column
    v_pos = -1.0; v_alpha = 1.0; gl_Position = u_matrix * vec4(a_pos.xy, 0.0, 1.0); return;
  }
  float w = mix(max(w0, 0.0), max(w1, 0.0), u_frac);
  if (!(w > 0.0)) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }                  // dry: off screen
  // continuous position on the ramp: linear inside each class, so colours change without jumps
  float p = 5.0;
  if (w < u_breaks[0]) p = 0.0;
  else for (int i = 0; i < 4; i++) if (w >= u_breaks[i] && w < u_breaks[i + 1]) { p = float(i + 1) + (w - u_breaks[i]) / (u_breaks[i + 1] - u_breaks[i]); }
  v_pos = min(p, 5.0);
  v_alpha = clamp(w / u_breaks[0], 0.25, 1.0);                                           // the thinnest water fades in
  float z = 0.0;
  if (u_cols == 1) {                                                                     // heights.js waterHeight
    float h = u_level == 10 ? w * u_exag : min(w, u_cap) * 100.0 * u_mPerPct;
    z = a_col.x * h * a_col.y;
    v_alpha = 1.0;
  }
  gl_Position = u_matrix * vec4(a_pos.xy, z, 1.0);
}`;
const FS = `#version 300 es
precision mediump float;
uniform vec3 u_ramp[6]; uniform float u_alpha;
flat in float v_pos; flat in float v_alpha; flat in float v_light; out vec4 colour;
void main() {
  if (v_pos < -0.5) {                                                                 // unknown: hatched
    if (mod(gl_FragCoord.x + gl_FragCoord.y, 7.0) > 2.0) discard;
    colour = vec4(0.27, 0.29, 0.28, 1.0) * 0.55; return;
  }
  int i = int(floor(v_pos)); int j = min(i + 1, 5);
  vec3 c = mix(u_ramp[i], u_ramp[j], v_pos - float(i)) * v_light;
  colour = vec4(c, 1.0) * (u_alpha * v_alpha);
}`;

function program(gl, vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(`water shader: ${gl.getShaderInfoLog(s)}`);
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(`water program: ${gl.getProgramInfoLog(p)}`);
  return p;
}

const RAMP = new Float32Array(WATER.colours.flatMap((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)));

export class WaterLayer {
  constructor(id = "storm-water") {   // (the street map has its own layer "water")
    this.id = id; this.type = "custom"; this.renderingMode = "3d";
    this.meshes = new Map();      // region -> {level, mode, kind, origin, box, tri, buffer, count}
    this.waters = new Map();      // region -> {level, data, rows, tex}
    this.t = 15; this.on = false; this.kind = "flat"; this.exag = DEFAULT_EXAGGERATION;
    this.shown = 0;               // when the current water arrived (it fades in)
  }

  onAdd(map, gl) {
    this.map = map; this.gl = gl;
    this.prog = program(gl, VS, FS);
    const p = this.prog;
    this.loc = { pos: gl.getAttribLocation(p, "a_pos"), col: gl.getAttribLocation(p, "a_col"), matrix: gl.getUniformLocation(p, "u_matrix"),
      cols: gl.getUniformLocation(p, "u_cols"), level: gl.getUniformLocation(p, "u_level"), exag: gl.getUniformLocation(p, "u_exag"),
      mPerPct: gl.getUniformLocation(p, "u_mPerPct"), cap: gl.getUniformLocation(p, "u_cap"),
      s0: gl.getUniformLocation(p, "u_s0"), s1: gl.getUniformLocation(p, "u_s1"), frac: gl.getUniformLocation(p, "u_frac"),
      tex: gl.getUniformLocation(p, "u_water"), prev: gl.getUniformLocation(p, "u_prev"), fade: gl.getUniformLocation(p, "u_fade"),
      breaks: gl.getUniformLocation(p, "u_breaks"), ramp: gl.getUniformLocation(p, "u_ramp"),
      alpha: gl.getUniformLocation(p, "u_alpha") };
  }

  // (a street-map switch removes and re-adds the layer: keep the data, free only the GPU copies)
  onRemove() {
    const gl = this.gl;
    for (const m of this.meshes.values()) { if (m.buffer) gl.deleteBuffer(m.buffer); m.buffer = null; }
    for (const t of this.waters.values()) { if (t.tex) gl.deleteTexture(t.tex); t.tex = null; if (t.prev?.tex) gl.deleteTexture(t.prev.tex); if (t.prev) t.prev.tex = null; }
    gl.deleteProgram(this.prog);
    this.gl = null;
  }

  // kind "flat" or "columns": which meshes to draw (the worker's waterMesh(..., kind))
  setVisible(on, kind = this.kind) { this.on = on; this.kind = kind; this.map?.triggerRepaint(); }

  setMesh(mesh) {
    this.dropMesh(mesh.region);
    this.meshes.set(mesh.region, { ...mesh, buffer: null, count: mesh.tri.length / 6 });
    this.map?.triggerRepaint();
  }

  meshOf(region) { return this.meshes.get(region) || null; }

  dropMesh(region) {
    const m = this.meshes.get(region);
    if (m?.buffer && this.gl) this.gl.deleteBuffer(m.buffer);
    this.meshes.delete(region);
  }

  // values[k * 16 + s] from the worker's stormLevel (NaN where unknown)
  setWater(region, level, values, gen = 0) {
    const K = values.length / 16, rows = Math.max(1, Math.ceil(K / 1024)) * 16, data = new Float32Array(1024 * rows);
    for (let k = 0; k < K; k++) {
      const at = (Math.floor(k / 1024) * 16) * 1024 + (k % 1024);
      for (let s = 0; s < 16; s++) { const v = values[k * 16 + s]; data[at + s * 1024] = Number.isNaN(v) ? -1 : v > 0 ? v : 0; }
    }
    // the water already drawn here, for the same hexagons, blends into the new over 0.4 s
    const old = this.waters.get(region);
    let prev = null;
    if (old && old.level === level && old.rows === rows) { prev = { data: old.data, rows, tex: old.tex }; if (old.prev?.tex) this.gl?.deleteTexture(old.prev.tex); }
    else if (old) this.clearWater(region);
    this.waters.set(region, { level, data, rows, tex: null, gen, prev, at: performance.now() });
    if (!prev) this.shown = performance.now();
    this.map?.triggerRepaint();
  }

  hasWater(region, level, gen = 0) { const t = this.waters.get(region); return !!t && t.level === level && t.gen === gen; }

  clearWater(region) {
    const list = region ? [region] : [...this.waters.keys()];
    for (const r of list) {
      const t = this.waters.get(r);
      if (t?.tex) this.gl?.deleteTexture(t.tex);
      if (t?.prev?.tex) this.gl?.deleteTexture(t.prev.tex);
      this.waters.delete(r);
    }
    this.map?.triggerRepaint();
  }

  dropExcept(regions) {
    for (const r of [...this.meshes.keys()]) if (!regions.has(r)) this.dropMesh(r);
    for (const r of [...this.waters.keys()]) if (!regions.has(r)) this.clearWater(r);
  }

  clear() { this.dropExcept(new Set()); }

  // t: -1 (no rain) .. 15 (whole storm), fractional while playing
  setStep(t) { this.t = Math.max(-1, Math.min(15, Number(t))); this.map?.triggerRepaint(); }

  _texture(t) {
    if (t.tex) return t.tex;
    const gl = this.gl;
    t.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t.tex);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST],
      [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, 1024, t.rows, 0, gl.RED, gl.FLOAT, t.data);
    return t.tex;
  }

  render(gl, args) {
    if (!this.on || !this.meshes.size || !this.waters.size) return;
    const mvp = args.defaultProjectionData?.mainMatrix || args.modelViewProjectionMatrix;
    const L = this.loc, cols = this.kind === "columns";
    gl.useProgram(this.prog);
    gl.disable(gl.CULL_FACE); gl.disable(gl.STENCIL_TEST);
    if (cols) { gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(true); gl.disable(gl.BLEND); }
    else { gl.disable(gl.DEPTH_TEST); gl.depthMask(false); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); }
    // new water fades in over 0.3 s (a new storm, or new hexagons after a move)
    const fade = Math.min(1, (performance.now() - this.shown) / 300);
    if (fade < 1) this.map.triggerRepaint();
    const s0 = Math.floor(this.t), s1 = Math.min(s0 + 1, 15);
    gl.uniform1i(L.s0, s0); gl.uniform1i(L.s1, s1); gl.uniform1f(L.frac, this.t - s0); gl.uniform1i(L.tex, 0); gl.uniform1i(L.prev, 1);
    gl.uniform3fv(L.ramp, RAMP); gl.uniform1f(L.alpha, cols ? 1 : 0.88 * fade);
    gl.uniform1i(L.cols, cols ? 1 : 0); gl.uniform1f(L.exag, this.exag); gl.uniform1f(L.cap, SHARE_CAP);
    gl.activeTexture(gl.TEXTURE0);
    gl.enableVertexAttribArray(L.pos); gl.enableVertexAttribArray(L.col);
    for (const m of this.meshes.values()) {
      const t = this.waters.get(m.region);
      if (!t || t.level !== m.level || m.kind !== this.kind) continue;
      gl.uniform1i(L.level, m.level); gl.uniform1f(L.mPerPct, SHARE_M_PER_PCT[m.level] || 0);
      if (!m.buffer) {
        m.buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, m.buffer);
        gl.bufferData(gl.ARRAY_BUFFER, m.tri, gl.STATIC_DRAW);
      }
      gl.uniform1fv(L.breaks, m.level === 10 ? WATER.depth.breaks : WATER.share.breaks);
      const now = performance.now(), mix = t.prev ? Math.min(1, (now - t.at) / 400) : 1;
      if (t.prev && mix >= 1) { if (t.prev.tex) gl.deleteTexture(t.prev.tex); t.prev = null; }
      if (t.prev) this.map.triggerRepaint();
      gl.uniform1f(L.fade, t.prev ? mix : 1);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, this._texture(t.prev || t));
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this._texture(t));
      gl.uniformMatrix4fv(L.matrix, false, shifted(mvp, m.origin));
      gl.bindBuffer(gl.ARRAY_BUFFER, m.buffer);
      gl.vertexAttribPointer(L.pos, 3, gl.FLOAT, false, 24, 0);
      gl.vertexAttribPointer(L.col, 3, gl.FLOAT, false, 24, 12);
      gl.drawArrays(gl.TRIANGLES, 0, m.count);
    }
    gl.disableVertexAttribArray(L.pos); gl.disableVertexAttribArray(L.col);
    gl.depthMask(true);
  }
}

// mvp x translate(origin), in double precision, handed to the GPU as float32
function shifted(m, [ox, oy]) {
  const out = new Float32Array(16);
  for (let i = 0; i < 12; i++) out[i] = m[i];
  for (let i = 0; i < 4; i++) out[12 + i] = m[i] * ox + m[4 + i] * oy + m[12 + i];
  return out;
}
