"""Catchments and spill links tile by tile, with exactly the answer of `routing.route`.

A tile is processed with a one-pixel rim of its neighbours (its "window"). The rim's ground
heights are read with the tile; its exact filled levels are known, because the rim pixels are
border cells of the neighbouring tiles, whose levels the fill's border solve gives. Every rule
of `routing` that looks at a pixel's neighbours can therefore be applied to every pixel of the
tile exactly. What cannot be settled inside one tile is where water goes after it leaves:

- a path that steps onto a pixel of another tile ("halo" end: that pixel's key);
- a flat that continues into another tile (its nearest way off may lie there: "flat" end);
- the full surface of a sink that continues into another tile (its first exit may lie there:
  "fp" end, for full plateau);
- a sink that continues into another tile ("sink" end: the piece, joined like depressions).

Each tile reports where every path ends inside it, and the pieces needed to settle these loose
ends; `resolve` settles them once every tile is done. Sinks that lie wholly inside a tile and
are too shallow to be depressions pass their water on inside the tile already (`collapse`), so
the loose ends stay few.
"""
import numpy as np
from numba import njit
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .routing import _DR, _DC

E_NONE, E_OUT, E_SINK, E_HALO, E_FLAT, E_FP, E_KEPT = -1, 0, 1, 2, 3, 4, 5
_TYPE_SHIFT = 58
OUT_CODE = 0


def encode(kind, ident):
    """One int64 code per end: the type in the top bits, the id below."""
    return (np.asarray(kind, dtype=np.int64) << _TYPE_SHIFT) | np.asarray(ident, dtype=np.int64)


def decode(code):
    code = np.asarray(code, dtype=np.int64)
    return code >> _TYPE_SHIFT, code & ((1 << _TYPE_SHIFT) - 1)


@njit(cache=True)
def _own(yy, xx, oy, ox, h, w):
    return oy <= yy < oy + h and ox <= xx < ox + w


@njit(cache=True)
def _halo_mate(z, valid, sinkf, y, x, oy, ox, h, w):
    """Does own pixel (y, x) touch a rim pixel of the same flat (valid, no sink, same height)?"""
    eh, ew = z.shape
    for k in range(8):
        yy, xx = y + _DR[k], x + _DC[k]
        if yy < 0 or yy >= eh or xx < 0 or xx >= ew or _own(yy, xx, oy, ox, h, w):
            continue
        if valid[yy, xx] and not sinkf[yy, xx] and z[yy, xx] == z[y, x]:
            return True
    return False


@njit(cache=True)
def tile_route(z, valid, filled, sinkf, lab, oy, ox, h, w, dist, width, er0, ec0, n_lab):
    """The routing rules inside one tile, on its window.

    z, valid, filled, sinkf (positive depth) and lab (the tile's sink labels 1..n_lab, 0 on the
    rim) are window arrays. The tile is rows oy..oy+h, columns ox..ox+w of the window; (er0, ec0)
    is the window's top-left pixel in the raster and `width` the raster's width (a pixel's key is
    row * width + col). dist: distances to the 8 neighbours, per window row.

    Returns per own pixel (row-major): end type and id, the local number of the border-crossing
    flat it belongs to (-1 if none), whether it has a lower neighbour, whether it is an outlet;
    the number of unresolvable flat pixels (0 on a consistent fill); per sink label its full
    surface; per own pixel its full surface and whether it meets the rim at the same level; and
    per full surface: does it cross the border, its first exit's key (-1 if none), and where
    the overflow goes from there (end type and id).
    """
    eh, ew = z.shape
    n_own = h * w
    outlet = np.zeros(n_own, np.bool_)
    rcv = np.full(n_own, -1, np.int64)            # receiver, as a window index
    for i in range(h):
        for j in range(w):
            y, x = i + oy, j + ox
            if not valid[y, x]:
                continue
            o = i * w + j
            for k in range(8):
                yy, xx = y + _DR[k], x + _DC[k]
                if yy < 0 or yy >= eh or xx < 0 or xx >= ew or not valid[yy, xx]:
                    outlet[o] = True
                    break
            if outlet[o] or sinkf[y, x]:
                continue
            best, pick = 0.0, -1
            for k in range(8):
                yy, xx = y + _DR[k], x + _DC[k]
                drop = (z[y, x] - z[yy, xx]) / dist[y, k]
                if drop > best:
                    best, pick = drop, yy * ew + xx
            rcv[o] = pick
    has_down = rcv >= 0

    # rule 4: flats wholly inside the tile are settled here, the others are deferred
    pcomp = np.full(n_own, -1, np.int64)
    steps = np.full(n_own, -1, np.int64)
    seen = np.zeros(n_own, np.bool_)
    queue = np.empty(n_own, np.int64)
    queue2 = np.empty(n_own, np.int64)
    n_pc = 0
    unresolved = 0
    for o0 in range(n_own):
        y0, x0 = o0 // w + oy, o0 % w + ox
        if seen[o0] or not valid[y0, x0] or sinkf[y0, x0]:
            continue
        flat0 = not outlet[o0] and not has_down[o0]
        if not flat0 and not _halo_mate(z, valid, sinkf, y0, x0, oy, ox, h, w):
            continue
        seen[o0] = True
        queue[0] = o0
        head, tail, touches = 0, 1, False
        while head < tail:
            o = queue[head]
            head += 1
            y, x = o // w + oy, o % w + ox
            if _halo_mate(z, valid, sinkf, y, x, oy, ox, h, w):
                touches = True
            for k in range(8):
                i2, j2 = o // w + _DR[k], o % w + _DC[k]
                if i2 < 0 or i2 >= h or j2 < 0 or j2 >= w:
                    continue
                o2 = i2 * w + j2
                yy, xx = i2 + oy, j2 + ox
                if not seen[o2] and valid[yy, xx] and not sinkf[yy, xx] and z[yy, xx] == z[y, x]:
                    seen[o2] = True
                    queue[tail] = o2
                    tail += 1
        if touches:
            for q in range(tail):
                o = queue[q]
                pcomp[o] = n_pc
                if not outlet[o] and not has_down[o]:
                    rcv[o] = -2                     # settled once every tile is done
            n_pc += 1
            continue
        h2, t2 = 0, 0
        for q in range(tail):
            o = queue[q]
            if outlet[o] or has_down[o]:
                steps[o] = 0
                queue2[t2] = o
                t2 += 1
        while h2 < t2:
            o = queue2[h2]
            h2 += 1
            y, x = o // w + oy, o % w + ox
            for k in range(8):
                i2, j2 = o // w + _DR[k], o % w + _DC[k]
                if i2 < 0 or i2 >= h or j2 < 0 or j2 >= w:
                    continue
                o2 = i2 * w + j2
                yy, xx = i2 + oy, j2 + ox
                if (steps[o2] < 0 and valid[yy, xx] and not sinkf[yy, xx] and not outlet[o2]
                        and z[yy, xx] == z[y, x]):
                    steps[o2] = steps[o] + 1
                    queue2[t2] = o2
                    t2 += 1
        for q in range(tail):
            o = queue[q]
            if outlet[o] or has_down[o]:
                continue
            if steps[o] < 0:
                unresolved += 1
                continue
            y, x = o // w + oy, o % w + ox
            for k in range(8):
                i2, j2 = o // w + _DR[k], o % w + _DC[k]
                if i2 < 0 or i2 >= h or j2 < 0 or j2 >= w:
                    continue
                o2 = i2 * w + j2
                yy, xx = i2 + oy, j2 + ox
                if (valid[yy, xx] and not sinkf[yy, xx] and z[yy, xx] == z[y, x]
                        and steps[o2] == steps[o] - 1):
                    rcv[o] = yy * ew + xx
                    break

    # where each own pixel's water ends inside the tile
    end_t = np.full(n_own, E_NONE, np.int64)
    end_i = np.zeros(n_own, np.int64)
    for o in range(n_own):
        y, x = o // w + oy, o % w + ox
        if not valid[y, x]:
            continue
        if sinkf[y, x]:
            end_t[o], end_i[o] = E_SINK, lab[y, x]
        elif outlet[o]:
            end_t[o], end_i[o] = E_OUT, 0
        elif rcv[o] == -2:
            end_t[o], end_i[o] = E_FLAT, (er0 + y) * width + ec0 + x
    path = np.empty(n_own, np.int64)
    for o in range(n_own):
        if end_t[o] != E_NONE or not valid[o // w + oy, o % w + ox]:
            continue
        m, j = 0, o
        t, ident = E_OUT, 0
        while True:
            if end_t[j] != E_NONE:
                t, ident = end_t[j], end_i[j]
                break
            path[m] = j
            m += 1
            r = rcv[j]
            if r < 0:                               # an unresolvable flat, counted above
                break
            ry, rx = r // ew, r % ew
            if _own(ry, rx, oy, ox, h, w):
                j = (ry - oy) * w + (rx - ox)
            else:
                t, ident = E_HALO, (er0 + ry) * width + ec0 + rx
                break
        for q in range(m):
            end_t[path[q]], end_i[path[q]] = t, ident

    # rule 5: the full surface around each sink, its first exit, and where the overflow goes
    fp = np.full(n_own, -1, np.int64)
    fp_border = np.zeros(n_own, np.bool_)
    sink_fp = np.full(n_lab + 1, -1, np.int64)
    n_max = n_lab + 2 * (h + w) + 1             # one per sink, or per tile-border pixel
    fp_touch = np.zeros(n_max, np.bool_)
    fp_exit = np.full(n_max, -1, np.int64)
    fp_dt = np.full(n_max, E_NONE, np.int64)
    fp_di = np.zeros(n_max, np.int64)
    n_fp = 0
    for o0 in range(n_own):
        y0, x0 = o0 // w + oy, o0 % w + ox
        if fp[o0] >= 0 or not valid[y0, x0]:
            continue
        level = filled[y0, x0]
        # a surface around a sink here, or one that continues from another tile (where its sink
        # may lie): its exit may be in this tile
        if not sinkf[y0, x0]:
            crosses = False
            for k in range(8):
                yy, xx = y0 + _DR[k], x0 + _DC[k]
                if (0 <= yy < eh and 0 <= xx < ew and not _own(yy, xx, oy, ox, h, w)
                        and valid[yy, xx] and filled[yy, xx] == level):
                    crosses = True
                    break
            if not crosses:
                continue
        fp[o0] = n_fp
        queue[0] = o0
        head, tail, first, first_o = 0, 1, -1, -1
        while head < tail:
            o = queue[head]
            head += 1
            y, x = o // w + oy, o % w + ox
            is_exit = outlet[o]
            for k in range(8):
                yy, xx = y + _DR[k], x + _DC[k]
                if yy < 0 or yy >= eh or xx < 0 or xx >= ew or not valid[yy, xx]:
                    continue
                if filled[yy, xx] < level:
                    is_exit = True
                elif filled[yy, xx] == level:
                    if _own(yy, xx, oy, ox, h, w):
                        o2 = (yy - oy) * w + (xx - ox)
                        if fp[o2] < 0:
                            fp[o2] = n_fp
                            queue[tail] = o2
                            tail += 1
                    else:
                        fp_border[o] = True
                        fp_touch[n_fp] = True
            if is_exit:
                key = (er0 + y) * width + ec0 + x
                if first < 0 or key < first:
                    first, first_o = key, o
        fp_exit[n_fp] = first
        if first_o >= 0:
            if outlet[first_o]:
                fp_dt[n_fp], fp_di[n_fp] = E_OUT, 0
            else:
                y, x = first_o // w + oy, first_o % w + ox
                best, pick = 0.0, -1
                for k in range(8):
                    yy, xx = y + _DR[k], x + _DC[k]
                    drop = (level - filled[yy, xx]) / dist[y, k]
                    if drop > best:
                        best, pick = drop, yy * ew + xx
                py, px = pick // ew, pick % ew
                if _own(py, px, oy, ox, h, w):
                    o2 = (py - oy) * w + (px - ox)
                    fp_dt[n_fp], fp_di[n_fp] = end_t[o2], end_i[o2]
                else:
                    fp_dt[n_fp], fp_di[n_fp] = E_HALO, (er0 + py) * width + ec0 + px
        for q in range(tail):
            o = queue[q]
            y, x = o // w + oy, o % w + ox
            if sinkf[y, x]:
                sink_fp[lab[y, x]] = n_fp
        n_fp += 1
    return (end_t, end_i, pcomp, has_down, outlet, unresolved, sink_fp, fp, fp_border,
            fp_touch[:n_fp], fp_exit[:n_fp], fp_dt[:n_fp], fp_di[:n_fp])


@njit(cache=True)
def collapse(order, cand, spill_t, spill_i):
    """Pass the water of sinks that cannot become depressions on along their spill paths.

    order: sink labels by rising level (a spill always reaches a lower sink, so it is already
    settled). A spill that reaches a non-candidate sink takes over that sink's spill.
    """
    for s in order:
        if spill_t[s] == E_SINK and not cand[spill_i[s]]:
            t = spill_i[s]
            spill_t[s], spill_i[s] = spill_t[t], spill_i[t]
    return spill_t, spill_i


@njit(cache=True)
def _bfs_steps(n, indptr, indices, is_exit):
    """Breadth-first steps from the exits over an undirected graph (CSR); -1 if unreachable."""
    steps = np.full(n, -1, np.int64)
    queue = np.empty(n, np.int64)
    head = tail = 0
    for i in range(n):
        if is_exit[i]:
            steps[i] = 0
            queue[tail] = i
            tail += 1
    while head < tail:
        i = queue[head]
        head += 1
        for k in range(indptr[i], indptr[i + 1]):
            j = indices[k]
            if steps[j] < 0 and not is_exit[j]:
                steps[j] = steps[i] + 1
                queue[tail] = j
                tail += 1
    return steps


def _pairs(keys, width, height):
    """Index pairs of 8-neighbouring pixels among `keys` (unique), each pair once."""
    from .tiled import neighbour_pairs

    return neighbour_pairs(keys, width, height)


def settle_flats(keys, z, is_exit, codes, width, height):
    """Rule 4 over flats that cross tile borders.

    keys, z, is_exit, codes: every pixel of those flats (from all tiles; codes are the ends of
    the exits). Returns, for each flat pixel (not an exit), the code its water passes to: the
    end of the exit it steps onto, or the flat end of the next flat pixel.
    """
    order = np.argsort(keys)
    keys, z, is_exit, codes = keys[order], z[order], is_exit[order], codes[order]
    n = len(keys)
    i, j = _pairs(keys, width, height)
    same = z[i] == z[j]
    i, j = i[same], j[same]
    u, v = np.r_[i, j], np.r_[j, i]
    srt = np.argsort(u, kind="stable")
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(u, minlength=n), out=indptr[1:])
    steps = _bfs_steps(n, indptr, v[srt].astype(np.int64), is_exit)
    flat = ~is_exit
    if (steps[flat] < 0).any():
        raise RuntimeError("a flat crossing tile borders has no way off: the fill is inconsistent")
    nxt = np.full(n, -1, dtype=np.int64)
    rows, cols = keys // width, keys % width
    for k in range(8):                              # the first neighbour, in the fixed order
        rr, cc = rows + _DR[k], cols + _DC[k]
        ok = flat & (nxt < 0) & (rr >= 0) & (rr < height) & (cc >= 0) & (cc < width)
        target = rr * width + cc
        pos = np.minimum(np.searchsorted(keys, target), n - 1)
        hit = ok & (keys[pos] == target) & (z[pos] == z) & (steps[pos] == steps - 1)
        nxt[hit] = pos[hit]
    out_keys = keys[flat]
    to = nxt[flat]
    to_code = np.where(is_exit[to], codes[to], encode(E_FLAT, keys[to]))
    return out_keys, to_code


def join_surfaces(keys, fp_id, level, n_fp, width, height):
    """Full surfaces that cross tile borders, joined: the component of each piece.

    keys, fp_id, level: the pixels of each piece that meet the rim at the same level.
    """
    if len(keys) == 0:
        return np.arange(n_fp)
    order = np.argsort(keys)
    keys, fp_id, level = keys[order], fp_id[order], level[order]
    i, j = _pairs(keys, width, height)
    same = level[i] == level[j]
    g = coo_matrix((np.ones(int(same.sum()), dtype=np.int8), (fp_id[i[same]], fp_id[j[same]])),
                   shape=(n_fp, n_fp))
    return connected_components(g, directed=False)[1]


@njit(cache=True)
def _follow(n, nxt):
    """Final index of each node by following `nxt` (-k-1 marks a terminal with value k)."""
    out = np.full(n, -1, np.int64)
    path = np.empty(n, np.int64)
    for i in range(n):
        if out[i] >= 0 or nxt[i] < 0:
            continue
        m, j = 0, i
        while True:
            if nxt[j] < 0:
                break
            if out[j] >= 0:
                break
            path[m] = j
            m += 1
            j = nxt[j]
            if m > n:
                raise RuntimeError("water routing loops")
        final = j if out[j] < 0 else out[j]
        for q in range(m):
            out[path[q]] = final
    return out


class Ends:
    """Every loose end of a tiled routing, settled: `final(codes)` gives each code's depression
    comp (KEPT ends) or OUT.

    node codes and their next codes come from: sinks (piece -> its comp if kept, else its comp's
    spill), halo ends (border pixel -> its end in its own tile), flat ends (settled flats) and fp
    ends (surface piece -> the overflow of its joined surface).
    """

    def __init__(self, node_codes, next_codes):
        order = np.argsort(node_codes)
        self.codes = node_codes[order]
        nxt = next_codes[order]
        n = len(self.codes)
        kind, _ = decode(nxt)
        terminal = (kind == E_OUT) | (kind == E_KEPT)
        pos = np.minimum(np.searchsorted(self.codes, nxt), max(n - 1, 0))
        if n and not (terminal | (self.codes[pos] == nxt)).all():
            missing = nxt[~terminal & (self.codes[pos] != nxt)][:5]
            raise RuntimeError(f"routing ends without a node: {decode(missing)}")
        self.terminal_code = np.where(terminal, nxt, -1)
        idx = np.where(terminal, -1, pos)
        last = _follow(n, idx)
        last = np.where(terminal, np.arange(n), last)
        self.final_code = self.terminal_code[last]

    def final(self, codes):
        codes = np.asarray(codes, dtype=np.int64)
        kind, _ = decode(codes)
        terminal = (kind == E_OUT) | (kind == E_KEPT)
        n = len(self.codes)
        pos = np.minimum(np.searchsorted(self.codes, codes), max(n - 1, 0))
        if n:
            found = self.codes[pos] == codes
        else:
            found = np.zeros(len(codes), dtype=bool)
        if not (terminal | found).all():
            raise RuntimeError("a routing end was never settled")
        return np.where(terminal, codes, self.final_code[pos] if n else codes)
