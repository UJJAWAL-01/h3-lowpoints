"""Building blocks for filling a DEM tile by tile and still getting the whole-raster answer.

The fill level of a cell is the lowest level water could stand at: the minimum, over all paths to
an outlet, of the highest elevation on the path. Cut the raster into tiles and every path is a
chain of in-tile pieces joined at tile-border cells. So:

1. Flood each tile from its border cells (and its real outlets) at their own elevations. This
   gives each cell a *root* (the seed it was reached from) and a local level. Where two
   neighbouring cells have different roots, the larger of their local levels is the lowest level
   at which water can cross between those roots. Keep only those crossings (`region_edges`).
2. Add the single steps between border cells of neighbouring tiles, and solve a minimax shortest
   path over this small graph from the real outlets (`solve_levels`). That gives the exact final
   level of every border cell.
3. Flood each tile again, this time starting its border cells at those exact levels
   (`flood_from_seeds`). The result is identical to filling the whole raster at once.

Memory is bounded by the tile plus the border graph, not by the raster. The argument that the
crossings of step 1 preserve every minimax path inside a tile is in docs/definition.md.
"""
import heapq

import numpy as np
from numba import njit
from scipy import ndimage

_STRUCT8 = np.ones((3, 3), dtype=bool)


def tile_seeds(valid, real_sides):
    """Seeds of a tile: valid cells on its border or next to an invalid cell inside it.

    real_sides is (top, bottom, left, right): whether that side of the tile is the edge of the
    whole raster (a real outlet) rather than a cut between tiles. Returns row and column arrays
    in row-major order, a flag for seeds that are real outlets (pinned: their level is their
    own elevation), and a flag for seeds on the tile border.
    """
    h, w = valid.shape
    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    next_to_invalid = ndimage.binary_dilation(~valid, structure=_STRUCT8) & valid
    real = np.zeros((h, w), dtype=bool)
    top, bottom, left, right = real_sides
    if top:
        real[0, :] = True
    if bottom:
        real[-1, :] = True
    if left:
        real[:, 0] = True
    if right:
        real[:, -1] = True
    seed = valid & (border | next_to_invalid)
    rows, cols = np.nonzero(seed)
    pinned = ((real | next_to_invalid) & valid)[rows, cols]
    return rows, cols, pinned, border[rows, cols]


@njit(cache=True)
def flood_from_seeds(dem, valid, seed_rows, seed_cols, seed_levels):
    """Priority-flood from seeds that start at given levels.

    Returns the level of every cell and, for each cell, the index of the seed it was reached from
    (-1 for invalid cells).
    """
    h, w = dem.shape
    level = dem.copy()
    root = np.full((h, w), -1, np.int32)
    closed = np.zeros((h, w), np.uint8)
    dr = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
    dc = np.array([-1, 0, 1, -1, 1, -1, 0, 1])
    heap = [(-1e30, -1)]  # dummy entry so numba can infer the list type

    for r in range(h):
        for c in range(w):
            if not valid[r, c]:
                closed[r, c] = 1
    for i in range(len(seed_rows)):
        r = seed_rows[i]
        c = seed_cols[i]
        closed[r, c] = 1
        root[r, c] = i
        level[r, c] = seed_levels[i]
        heapq.heappush(heap, (seed_levels[i], r * w + c))

    while len(heap) > 0:
        z, idx = heapq.heappop(heap)
        if idx < 0:
            continue
        r = idx // w
        c = idx % w
        for k in range(8):
            rr = r + dr[k]
            cc = c + dc[k]
            if rr < 0 or rr >= h or cc < 0 or cc >= w:
                continue
            if closed[rr, cc] == 1:
                continue
            closed[rr, cc] = 1
            root[rr, cc] = root[r, c]
            zz = dem[rr, cc]
            if zz < z:
                zz = z
            level[rr, cc] = zz
            heapq.heappush(heap, (zz, rr * w + cc))
    return level, root


@njit(cache=True)
def _crossings(level, root, count_only, out_a, out_b, out_w):
    h, w = level.shape
    n = 0
    for r in range(h):
        for c in range(w):
            ra = root[r, c]
            if ra < 0:
                continue
            for k in range(4):  # right, down-left, down, down-right: each neighbour pair once
                if k == 0:
                    rr, cc = r, c + 1
                elif k == 1:
                    rr, cc = r + 1, c - 1
                elif k == 2:
                    rr, cc = r + 1, c
                else:
                    rr, cc = r + 1, c + 1
                if rr >= h or cc < 0 or cc >= w:
                    continue
                rb = root[rr, cc]
                if rb < 0 or rb == ra:
                    continue
                if not count_only:
                    out_a[n] = min(ra, rb)
                    out_b[n] = max(ra, rb)
                    out_w[n] = max(level[r, c], level[rr, cc])
                n += 1
    return n


def region_edges(level, root):
    """Lowest crossing level between every pair of neighbouring roots: arrays (a, b, w), a < b."""
    n = _crossings(level, root, True, np.empty(0, np.int32), np.empty(0, np.int32),
                   np.empty(0))
    a, b, w = np.empty(n, np.int32), np.empty(n, np.int32), np.empty(n)
    _crossings(level, root, False, a, b, w)
    if n == 0:
        return a, b, w
    order = np.lexsort((w, b, a))                   # for equal (a, b) the lowest w comes first
    a, b, w = a[order], b[order], w[order]
    first = np.ones(n, dtype=bool)
    first[1:] = (a[1:] != a[:-1]) | (b[1:] != b[:-1])
    return a[first], b[first], w[first]


@njit(cache=True)
def _solve(z, pinned, indptr, indices, weights):
    n = len(z)
    best = np.full(n, np.inf)
    heap = [(-1e30, -1)]
    for i in range(n):
        if pinned[i]:
            best[i] = z[i]
            heapq.heappush(heap, (z[i], i))
    while len(heap) > 0:
        f, u = heapq.heappop(heap)
        if u < 0 or f > best[u]:
            continue
        for k in range(indptr[u], indptr[u + 1]):
            v = indices[k]
            c = weights[k] if weights[k] > f else f
            if c < best[v]:
                best[v] = c
                heapq.heappush(heap, (c, v))
    return best


def solve_levels(z, pinned, edge_a, edge_b, edge_w):
    """Exact fill level of every node of the border graph.

    z are node elevations, pinned marks real outlets (level = elevation), and the edges are
    undirected with the level at which water can cross. A node's level is the lowest, over paths
    to a pinned node, of the highest edge weight on the path. Nodes with no path keep their own
    elevation.
    """
    n = len(z)
    u = np.concatenate([edge_a, edge_b]).astype(np.int64)
    v = np.concatenate([edge_b, edge_a]).astype(np.int64)
    w = np.concatenate([edge_w, edge_w])
    order = np.argsort(u, kind="stable")
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(u, minlength=n), out=indptr[1:])
    best = _solve(np.asarray(z, dtype=np.float64), np.asarray(pinned, dtype=np.bool_),
                  indptr, v[order], w[order])
    return np.where(np.isfinite(best), best, z)


def neighbour_pairs(keys, width, height):
    """Index pairs (i, j) of cells that are 8-neighbours, from unique flat keys row * width + col."""
    keys = np.asarray(keys, dtype=np.int64)
    if len(keys) == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    order = np.argsort(keys)
    sorted_keys = keys[order]
    rows, cols = keys // width, keys % width
    out_i, out_j = [], []
    for dr, dc in ((0, 1), (1, -1), (1, 0), (1, 1)):
        nr, nc = rows + dr, cols + dc
        ok = (nc >= 0) & (nc < width) & (nr < height)
        target = nr * width + nc
        pos = np.searchsorted(sorted_keys, target)
        pos = np.minimum(pos, len(keys) - 1)
        hit = ok & (sorted_keys[pos] == target)
        out_i.append(np.nonzero(hit)[0])
        out_j.append(order[pos[hit]])
    return np.concatenate(out_i), np.concatenate(out_j)
