"""Where water goes: the catchment of every depression and where each one spills when full.

The rules (stated in full in docs/definition.md) depend only on the terrain, never on the order
in which pixels are processed, so a tiled analysis can reproduce them exactly:

1. Sinks are the 8-connected regions of positive depth (filled level above the ground), whatever
   their depth. Water landing on a sink stays in it.
2. Outlets are the valid pixels on the raster border or next to an invalid pixel (nodata, water
   mask). Water reaching one leaves the map.
3. Any other pixel passes its water to the neighbour with the steepest drop in ground height
   (drop over the distance between pixel centres in metres; ties go to the first neighbour in
   the fixed order NW, N, NE, W, E, SW, S, SE).
4. A pixel with no lower neighbour lies on a flat: the connected set of non-sink pixels of the
   same height. It passes its water to the neighbour on that flat one step nearer to the flat's
   nearest way off (a pixel with a lower neighbour, or an outlet), counted in 8-neighbour steps.
   Ties again go to the first neighbour in the fixed order.
5. When a sink is full, its surface joins every pixel of the same filled level connected to it.
   It spills at that region's exit (a pixel with a neighbour of lower filled level, or an
   outlet) with the smallest row-major index, onto the exit's neighbour with the steepest drop
   in filled level, and from there water follows rules 1 to 4. The receiving sink is always
   lower than the spilling one, so spilling never loops.

Depressions are the sinks at least `min_depth` deep. A shallower sink fills at once and passes
its water on along its spill path, so a depression's catchment is the land draining to it
directly plus the land draining to the shallow sinks that spill into it.
"""
import math

import numpy as np
from numba import njit

from ..geometry import EARTH_RADIUS_M, crs_info, pixel_areas_m2

_DR = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
_DC = np.array([-1, 0, 1, -1, 1, -1, 0, 1])
OUTLET = 0          # the target of water that leaves the map


def pixel_spacing_m(transform, crs, height):
    """Distance between pixel centres in metres, east-west and north-south, for each row.

    Projected grids: the pixel size in metres. Geographic grids: the north-south spacing on the
    sphere of the same area as WGS 84, and the east-west spacing that gives each row's exact
    pixel area, so spacing x spacing = area.
    """
    geographic, unit = crs_info(crs)
    if not geographic:
        return (np.full(height, abs(transform.a) * unit), np.full(height, abs(transform.e) * unit))
    dy = np.full(height, EARTH_RADIUS_M * math.radians(abs(transform.e)))
    return pixel_areas_m2(transform, crs, height) / dy, dy


@njit(cache=True)
def _outlets(valid):
    h, w = valid.shape
    out = np.zeros((h, w), np.bool_)
    for r in range(h):
        for c in range(w):
            if not valid[r, c]:
                continue
            if r == 0 or c == 0 or r == h - 1 or c == w - 1:
                out[r, c] = True
                continue
            for k in range(8):
                if not valid[r + _DR[k], c + _DC[k]]:
                    out[r, c] = True
                    break
    return out


@njit(cache=True)
def _distances(dx, dy):
    """Distance to each of the 8 neighbours, for each row."""
    h = len(dx)
    out = np.empty((h, 8))
    for r in range(h):
        diag = math.sqrt(dx[r] * dx[r] + dy[r] * dy[r])
        for k in range(8):
            if _DR[k] != 0 and _DC[k] != 0:
                out[r, k] = diag
            elif _DR[k] != 0:
                out[r, k] = dy[r]
            else:
                out[r, k] = dx[r]
    return out


@njit(cache=True)
def receivers(z, valid, sink, outlet, dist):
    """Flat index of the pixel each pixel passes its water to (rules 3 and 4).

    -1 for invalid pixels, sinks and outlets, which keep their water. Returns (receiver,
    unresolved), where `unresolved` counts flat pixels with no way off (always 0 on a filled
    surface; reported so a caller can check).
    """
    h, w = z.shape
    rcv = np.full(h * w, -1, np.int64)
    for r in range(h):
        for c in range(w):
            if not valid[r, c] or sink[r, c] != 0 or outlet[r, c]:
                continue
            best, pick = 0.0, -1
            for k in range(8):
                rr, cc = r + _DR[k], c + _DC[k]
                if rr < 0 or rr >= h or cc < 0 or cc >= w or not valid[rr, cc]:
                    continue
                drop = (z[r, c] - z[rr, cc]) / dist[r, k]
                if drop > best:
                    best, pick = drop, rr * w + cc
            rcv[r * w + c] = pick
    # rule 4: breadth-first steps from the ways off each flat, then one step nearer
    steps = np.full(h * w, -1, np.int64)
    queue = np.empty(h * w, np.int64)
    head = tail = 0
    for r in range(h):
        for c in range(w):
            i = r * w + c
            if valid[r, c] and sink[r, c] == 0 and (outlet[r, c] or rcv[i] >= 0):
                steps[i] = 0
                queue[tail] = i
                tail += 1
    while head < tail:
        i = queue[head]
        head += 1
        r, c = i // w, i % w
        for k in range(8):
            rr, cc = r + _DR[k], c + _DC[k]
            if rr < 0 or rr >= h or cc < 0 or cc >= w:
                continue
            j = rr * w + cc
            if (steps[j] < 0 and valid[rr, cc] and sink[rr, cc] == 0 and not outlet[rr, cc]
                    and z[rr, cc] == z[r, c]):
                steps[j] = steps[i] + 1
                queue[tail] = j
                tail += 1
    unresolved = 0
    for r in range(h):
        for c in range(w):
            i = r * w + c
            if not valid[r, c] or sink[r, c] != 0 or outlet[r, c] or rcv[i] >= 0:
                continue
            if steps[i] < 0:
                unresolved += 1
                continue
            for k in range(8):
                rr, cc = r + _DR[k], c + _DC[k]
                if rr < 0 or rr >= h or cc < 0 or cc >= w:
                    continue
                j = rr * w + cc
                if (valid[rr, cc] and sink[rr, cc] == 0 and z[rr, cc] == z[r, c]
                        and steps[j] == steps[i] - 1):
                    rcv[i] = j
                    break
    return rcv, unresolved


@njit(cache=True)
def targets(rcv, sink, outlet, valid):
    """For every pixel, the sink its water reaches (its id) or OUTLET (0); -1 if invalid."""
    h, w = sink.shape
    n = h * w
    tgt = np.full(n, -1, np.int64)
    for i in range(n):
        r, c = i // w, i % w
        if not valid[r, c]:
            continue
        if sink[r, c] != 0:
            tgt[i] = sink[r, c]
        elif outlet[r, c]:
            tgt[i] = OUTLET
    path = np.empty(n, np.int64)
    for i in range(n):
        if tgt[i] != -1 or not valid[i // w, i % w]:
            continue
        m, j = 0, i
        while tgt[j] == -1:
            path[m] = j
            m += 1
            j = rcv[j]
        for q in range(m):
            tgt[path[q]] = tgt[j]
    return tgt


@njit(cache=True)
def spills(filled, valid, sink, outlet, dist, tgt, n_sinks):
    """For each sink id 1..n_sinks: the flat index of its spill pixel and the target its overflow
    reaches (a sink id or OUTLET). Index 0 of both arrays is unused (rule 5)."""
    h, w = filled.shape
    exit_at = np.full(n_sinks + 1, -1, np.int64)
    target = np.full(n_sinks + 1, -1, np.int64)
    comp = np.full(h * w, -1, np.int64)
    queue = np.empty(h * w, np.int64)
    n_comp = 0
    for r0 in range(h):
        for c0 in range(w):
            s = sink[r0, c0]
            if s == 0 or exit_at[s] >= 0:
                continue
            # the region of equal filled level around this sink, and its first exit
            level = filled[r0, c0]
            start = r0 * w + c0
            comp[start] = n_comp
            queue[0] = start
            head, tail, first = 0, 1, -1
            while head < tail:
                i = queue[head]
                head += 1
                r, c = i // w, i % w
                is_exit = outlet[r, c]
                for k in range(8):
                    rr, cc = r + _DR[k], c + _DC[k]
                    if rr < 0 or rr >= h or cc < 0 or cc >= w or not valid[rr, cc]:
                        continue
                    j = rr * w + cc
                    if filled[rr, cc] < level:
                        is_exit = True
                    elif filled[rr, cc] == level and comp[j] < 0:
                        comp[j] = n_comp
                        queue[tail] = j
                        tail += 1
                if is_exit and (first < 0 or i < first):
                    first = i
            # where the overflow goes from the exit
            dest = OUTLET
            if first >= 0 and not outlet[first // w, first % w]:
                r, c = first // w, first % w
                best, pick = 0.0, -1
                for k in range(8):
                    rr, cc = r + _DR[k], c + _DC[k]
                    if rr < 0 or rr >= h or cc < 0 or cc >= w or not valid[rr, cc]:
                        continue
                    drop = (level - filled[rr, cc]) / dist[r, k]
                    if drop > best:
                        best, pick = drop, rr * w + cc
                dest = tgt[pick]
            for q in range(tail):
                i = queue[q]
                t = sink[i // w, i % w]
                if t != 0:
                    exit_at[t] = first
                    target[t] = dest
            n_comp += 1
    return exit_at, target


def resolve(spill_target, sink_level, kept):
    """For each sink id, the depression (kept sink) or OUTLET that finally receives its water.

    A kept sink keeps its own; a shallow sink passes on along its spill path, which only ever
    leads to lower sinks, so resolving in order of rising level needs one pass.
    """
    out = np.full(len(spill_target), OUTLET, np.int64)
    for s in np.argsort(sink_level[1:], kind="stable") + 1:
        if kept[s]:
            out[s] = s
        else:
            t = spill_target[s]
            out[s] = OUTLET if t == OUTLET else out[t]
    return out


def route(z, valid, filled, sink, kept, transform, crs):
    """Water routing of a whole raster.

    z: ground heights; valid: usable pixels; filled: the priority-flood fill; sink: labels of the
    8-connected regions of positive depth (1..n); kept: boolean array over sink ids (index 0
    unused), True for depressions. Returns a dict with `first` (per pixel: the sink or OUTLET
    its water reaches first, -1 if invalid), `final` (per pixel: the depression or OUTLET it
    finally reaches), `spill_pixel` and `spill_to` (per sink id: the flat index of its spill
    pixel and the depression or OUTLET its overflow finally reaches).
    """
    h, w = z.shape
    dist = _distances(*pixel_spacing_m(transform, crs, h))
    outlet = _outlets(valid)
    rcv, unresolved = receivers(z, valid, sink, outlet, dist)
    if unresolved:
        raise RuntimeError(f"{unresolved} flat pixels have no way off: the fill is inconsistent")
    first = targets(rcv, sink, outlet, valid)
    n = int(sink.max()) if sink.size else 0
    spill_pixel, spill_first = spills(filled, valid, sink, outlet, dist, first, n)
    level = np.full(n + 1, np.inf)
    rr, cc = np.nonzero(sink)
    level[sink[rr, cc]] = filled[rr, cc]
    final_of = resolve(spill_first, level, np.asarray(kept, dtype=bool))
    final = np.where(first > 0, final_of[np.maximum(first, 0)], first)
    spill_to = np.full(n + 1, OUTLET, np.int64)
    for s in range(1, n + 1):
        t = spill_first[s]
        spill_to[s] = OUTLET if t == OUTLET else final_of[t]
    return {"first": first.reshape(h, w), "final": final.reshape(h, w),
            "spill_pixel": spill_pixel, "spill_to": spill_to}
