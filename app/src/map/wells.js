// The "Wells" view: each depression sinks below the street map, a hollow you look down into, its
// hexagons stepping down to their depth when full (exaggerated, heights.DEPTH_EXAGGERATION), with
// blue water rising inside during a storm. A small WebGL2 layer of our own (MapLibre draws only
// upwards from the ground).
//
// How the hollows are cut out of the map: (1) the whole screen is marked "nearest" in the depth
// buffer, (2) the hexagon openings are marked "farthest", so (3) floors, walls and water are only
// seen through an opening, and are hidden by each other where they should be. The depth buffer is
// cleared afterwards so the layers above (labels, the selection, the overflow path) draw as before.

import { DEPTH_EXAGGERATION } from "./heights.js";

const VS_PIT = `#version 300 es
uniform mat4 u_matrix; uniform float u_exag; uniform int u_mode;
in vec4 a_pos;            // x, y, z (Mercator, unexaggerated), shade
out float v_shade;
void main() {
  if (u_mode == 0) { gl_Position = vec4(a_pos.xy, -1.0, 1.0); v_shade = 0.0; return; }   // whole screen
  vec4 p = u_matrix * vec4(a_pos.xy, u_mode == 1 ? 0.0 : a_pos.z * u_exag, 1.0);
  if (u_mode == 1) p.z = p.w * 0.9999;                                                    // openings: farthest
  gl_Position = p; v_shade = a_pos.w;
}`;
const FS_PIT = `#version 300 es
precision mediump float;
in float v_shade; out vec4 colour;
void main() { colour = vec4(mix(vec3(0.95, 0.955, 0.945), vec3(0.11, 0.13, 0.12), v_shade), 1.0); }`;

const VS_WATER = `#version 300 es
uniform mat4 u_matrix; uniform float u_exag; uniform int u_step; uniform sampler2D u_water;
in vec2 a_pos; in vec3 a_cell;   // floor depth (m), Mercator per metre, hexagon index
void main() {
  int k = int(a_cell.z + 0.5);
  float w = texelFetch(u_water, ivec2(k % 1024, (k / 1024) * 16 + u_step), 0).r;
  if (!(w > 0.0)) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }                    // dry: off screen
  gl_Position = u_matrix * vec4(a_pos, (w - a_cell.x) * a_cell.y * u_exag, 1.0);
}`;
const FS_WATER = `#version 300 es
precision mediump float;
out vec4 colour;
void main() { colour = vec4(0.114, 0.247, 0.62, 1.0) * 0.62; }     // water blue, premultiplied`;

function program(gl, vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(`wells shader: ${gl.getShaderInfoLog(s)}`);
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(`wells program: ${gl.getProgramInfoLog(p)}`);
  return p;
}

export class WellsLayer {
  constructor(id = "wells") {
    this.id = id; this.type = "custom"; this.renderingMode = "3d";
    this.meshes = new Map();      // region -> {origin, buffers, counts, box}
    this.waters = new Map();      // region -> texture
    this.step = 15; this.exag = DEPTH_EXAGGERATION; this.on = false;
  }

  onAdd(map, gl) {
    this.map = map; this.gl = gl;
    this.pit = program(gl, VS_PIT, FS_PIT);
    this.wat = program(gl, VS_WATER, FS_WATER);
    this.screen = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.screen);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 0, 0, 1, -1, 0, 0, -1, 1, 0, 0, 1, 1, 0, 0]), gl.STATIC_DRAW);
    this.loc = {
      pos: gl.getAttribLocation(this.pit, "a_pos"), matrix: gl.getUniformLocation(this.pit, "u_matrix"),
      exag: gl.getUniformLocation(this.pit, "u_exag"), mode: gl.getUniformLocation(this.pit, "u_mode"),
      wpos: gl.getAttribLocation(this.wat, "a_pos"), wcell: gl.getAttribLocation(this.wat, "a_cell"),
      wmatrix: gl.getUniformLocation(this.wat, "u_matrix"), wexag: gl.getUniformLocation(this.wat, "u_exag"),
      wstep: gl.getUniformLocation(this.wat, "u_step"), wtex: gl.getUniformLocation(this.wat, "u_water"),
    };
    this.empty = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.empty);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, 1, 1, 0, gl.RED, gl.FLOAT, new Float32Array([0]));
    for (const m of this.meshes.values()) this._upload(m);
  }

  // (a street-map switch removes and re-adds the layer: keep the data, free only the GPU copies)
  onRemove() {
    const gl = this.gl;
    for (const m of this.meshes.values()) this._free(m);
    for (const t of this.waters.values()) { if (t.tex) gl.deleteTexture(t.tex); t.tex = null; }
    gl.deleteBuffer(this.screen); gl.deleteTexture(this.empty);
    gl.deleteProgram(this.pit); gl.deleteProgram(this.wat);
    this.gl = null;
  }

  setVisible(on) { this.on = on; this.map?.triggerRepaint(); }

  // mesh from the worker's wellMesh(region, box)
  setMesh(mesh) {
    const old = this.meshes.get(mesh.region);
    if (old) this._free(old);
    const m = { ...mesh, buffers: null };
    this.meshes.set(mesh.region, m);
    if (this.gl) this._upload(m);
    this.map?.triggerRepaint();
  }

  dropExcept(regions) {
    for (const [r, m] of this.meshes) if (!regions.has(r)) { this._free(m); this.meshes.delete(r); }
    for (const [r, t] of this.waters) if (!regions.has(r)) { this.gl?.deleteTexture(t.tex); this.waters.delete(r); }
  }

  drop(region) {
    const m = this.meshes.get(region);
    if (m) { this._free(m); this.meshes.delete(region); }
    this.clearWater(region);
  }

  meshBox(region) { return this.meshes.get(region)?.box || null; }

  // water depth per hexagon and step, values[k * 16 + s] (from the worker's stormLevel at level 10)
  setWater(region, values) {
    const K = values.length / 16, rows = Math.ceil(K / 1024) * 16, data = new Float32Array(1024 * rows);
    for (let k = 0; k < K; k++) {
      const at = (Math.floor(k / 1024) * 16) * 1024 + (k % 1024);
      for (let s = 0; s < 16; s++) { const v = values[k * 16 + s]; data[at + s * 1024] = v > 0 ? v : 0; }
    }
    this.clearWater(region);
    this.waters.set(region, { data, rows, tex: null });
    this.map?.triggerRepaint();
  }

  clearWater(region) {
    const list = region ? [region] : [...this.waters.keys()];
    for (const r of list) { const t = this.waters.get(r); if (t?.tex) this.gl?.deleteTexture(t.tex); this.waters.delete(r); }
    this.map?.triggerRepaint();
  }

  setStep(s) { this.step = Math.max(0, Math.min(15, s | 0)); this.map?.triggerRepaint(); }

  _upload(m) {
    const gl = this.gl, make = (arr) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, arr, gl.STATIC_DRAW); return b; };
    const open4 = new Float32Array((m.open.length / 2) * 4);
    for (let i = 0, j = 0; i < m.open.length; i += 2, j += 4) { open4[j] = m.open[i]; open4[j + 1] = m.open[i + 1]; }
    m.buffers = { open: make(open4), pit: make(m.pit), water: make(m.water) };
    m.counts = { open: m.open.length / 2, pit: m.pit.length / 4, water: m.water.length / 5 };
  }

  _free(m) {
    if (!m.buffers || !this.gl) return;
    for (const b of Object.values(m.buffers)) this.gl.deleteBuffer(b);
    m.buffers = null;
  }

  _texture(t) {
    if (t.tex) return t.tex;
    const gl = this.gl;
    t.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t.tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, 1024, t.rows, 0, gl.RED, gl.FLOAT, t.data);
    return t.tex;
  }

  render(gl, args) {
    if (!this.on || !this.meshes.size) return;
    const mvp = args.defaultProjectionData?.mainMatrix || args.modelViewProjectionMatrix;
    gl.disable(gl.CULL_FACE); gl.disable(gl.STENCIL_TEST); gl.disable(gl.BLEND);
    gl.enable(gl.DEPTH_TEST); gl.depthRange(0, 1);
    const L = this.loc;
    // 1. the whole screen nearest
    gl.useProgram(this.pit);
    gl.uniform1i(L.mode, 0); gl.uniform1f(L.exag, this.exag);
    gl.colorMask(false, false, false, false); gl.depthMask(true); gl.depthFunc(gl.ALWAYS);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.screen);
    gl.enableVertexAttribArray(L.pos); gl.vertexAttribPointer(L.pos, 4, gl.FLOAT, false, 16, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    const ready = [...this.meshes.values()].filter((m) => m.buffers);
    // 2. the openings farthest
    gl.uniform1i(L.mode, 1);
    for (const m of ready) {
      gl.uniformMatrix4fv(L.matrix, false, shifted(mvp, m.origin));
      gl.bindBuffer(gl.ARRAY_BUFFER, m.buffers.open);
      gl.vertexAttribPointer(L.pos, 4, gl.FLOAT, false, 16, 0);
      gl.drawArrays(gl.TRIANGLES, 0, m.counts.open);
    }
    // 3. floors and walls, then water
    gl.uniform1i(L.mode, 2);
    gl.colorMask(true, true, true, true); gl.depthFunc(gl.LESS);
    for (const m of ready) {
      gl.uniformMatrix4fv(L.matrix, false, shifted(mvp, m.origin));
      gl.bindBuffer(gl.ARRAY_BUFFER, m.buffers.pit);
      gl.vertexAttribPointer(L.pos, 4, gl.FLOAT, false, 16, 0);
      gl.drawArrays(gl.TRIANGLES, 0, m.counts.pit);
    }
    gl.disableVertexAttribArray(L.pos);
    if (this.waters.size) {
      gl.useProgram(this.wat);
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
      gl.uniform1f(L.wexag, this.exag); gl.uniform1i(L.wstep, this.step); gl.uniform1i(L.wtex, 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.enableVertexAttribArray(L.wpos); gl.enableVertexAttribArray(L.wcell);
      for (const m of ready) {
        const t = this.waters.get(m.region);
        if (!t) continue;
        gl.bindTexture(gl.TEXTURE_2D, this._texture(t));
        gl.uniformMatrix4fv(L.wmatrix, false, shifted(mvp, m.origin));
        gl.bindBuffer(gl.ARRAY_BUFFER, m.buffers.water);
        gl.vertexAttribPointer(L.wpos, 2, gl.FLOAT, false, 20, 0);
        gl.vertexAttribPointer(L.wcell, 3, gl.FLOAT, false, 20, 8);
        gl.drawArrays(gl.TRIANGLES, 0, m.counts.water);
      }
      gl.disableVertexAttribArray(L.wpos); gl.disableVertexAttribArray(L.wcell);
    }
    // 4. leave the depth buffer as MapLibre expects for the layers above
    gl.depthMask(true); gl.clear(gl.DEPTH_BUFFER_BIT);
  }
}

// mvp x translate(origin), in double precision, handed to the GPU as float32
function shifted(m, [ox, oy]) {
  const out = new Float32Array(16);
  for (let i = 0; i < 12; i++) out[i] = m[i];
  for (let i = 0; i < 4; i++) out[12 + i] = m[i] * ox + m[4 + i] * oy + m[12 + i];
  return out;
}
