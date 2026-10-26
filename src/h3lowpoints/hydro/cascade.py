"""Depressions filling and overflowing into each other: the spill cascade.

Every depression keeps the water that reaches it, up to its volume, and passes the rest to the
depression it spills into (`spill_to`), or off the map when it spills to 0 or to a depression
not in the table. Water reaching a depression is its own runoff plus whatever the depressions
spilling into it pass on, so they are settled first: `spill_order` puts every depression before
the one it spills into (spill links never go round in a loop; `verify` checks that).

NaN means unknown: a depression whose own runoff is unknown, or that receives water from one,
gets NaN, and so does everything downstream of it.
"""
import numpy as np
from numba import njit


@njit(cache=True)
def _order(target):
    n = len(target)
    waiting = np.zeros(n, dtype=np.int64)            # depressions still to spill into each one
    for i in range(n):
        if target[i] >= 0:
            waiting[target[i]] += 1
    order = np.empty(n, dtype=np.int64)
    tail = 0
    for i in range(n):
        if waiting[i] == 0:
            order[tail] = i
            tail += 1
    head = 0
    while head < tail:
        j = target[order[head]]
        head += 1
        if j >= 0:
            waiting[j] -= 1
            if waiting[j] == 0:
                order[tail] = j
                tail += 1
    return order[:tail]


def spill_order(ids, spill_to):
    """(order, target) for depressions `ids` spilling into `spill_to`.

    target[i] is the position (in `ids`) of the depression row i spills into, or -1 when its
    water leaves the table. `order` lists every position, each before the one it spills into.
    """
    ids, spill_to = np.asarray(ids, dtype=np.int64), np.asarray(spill_to, dtype=np.int64)
    by_id = np.argsort(ids, kind="stable")
    at = np.minimum(np.searchsorted(ids[by_id], spill_to), max(len(ids) - 1, 0))
    known = (len(ids) > 0) & (spill_to != 0)
    known = known & (ids[by_id][at] == spill_to) if len(ids) else known
    target = np.where(known, by_id[at], -1).astype(np.int64)
    order = _order(target)
    if len(order) != len(ids):
        raise ValueError("following spill_to goes round in a loop")
    return order, target


@njit(cache=True)
def _cascade(order, target, own, volume):
    inflow = own.copy()
    leaving = np.zeros(own.shape[1])
    for s in range(own.shape[1]):
        for i in order:
            extra = inflow[i, s] - volume[i]
            j = target[i]
            if np.isnan(extra):
                if j >= 0:
                    inflow[j, s] = np.nan
                else:
                    leaving[s] = np.nan
            elif extra > 0:
                if j >= 0:
                    inflow[j, s] += extra
                else:
                    leaving[s] += extra
    return inflow, leaving


def cascade(order, target, own, volume):
    """Water reaching each depression (m3), its own runoff plus the overflow passed on to it.

    own     runoff of each depression's own catchment, m3: one column per storm (or a 1-d array
            for one storm)
    volume  each depression's volume when full, m3

    Returns (inflow, leaving): inflow has the shape of `own`; leaving is the water that left the
    table per storm. Water is conserved: own.sum() = stored + leaving, where each depression
    stores min(inflow, volume).
    """
    own = np.asarray(own, dtype=np.float64)
    flat = own.ndim == 1
    inflow, leaving = _cascade(order, target, own.reshape(len(own), -1),
                               np.asarray(volume, dtype=np.float64))
    return (inflow[:, 0], leaving[0]) if flat else (inflow, leaving)


@njit(cache=True)
def _rain_to_fill(order, target, runoff_area, volume, low, high, step):
    n = len(order)
    found = np.full(n, np.nan)
    before = np.zeros(n)                            # inflow at the previous rain depth
    inflow = np.empty(n)
    rain_before, rain = 0.0, low
    while True:
        for i in range(n):
            inflow[i] = rain * runoff_area[i]
        for i in order:
            extra = inflow[i] - volume[i]
            j = target[i]
            if j >= 0 and extra > 0:
                inflow[j] += extra
        for i in range(n):
            if np.isnan(found[i]) and inflow[i] >= volume[i]:
                # filled between the two depths: inflow is linear in between unless a depression
                # upstream filled there too, so interpolating stays within the bracket
                share = (volume[i] - before[i]) / (inflow[i] - before[i])
                found[i] = rain_before + share * (rain - rain_before)
        if rain >= high:
            break
        rain_before = rain
        for i in range(n):
            before[i] = inflow[i]
        rain = min(rain * step, high)
    return found


def rain_to_fill_upstream(order, target, runoff_area, volume, rel=0.01):
    """The uniform rain depth (m) that fills each depression, counting the overflow of every
    depression upstream that fills first. Exact to within a factor 1 + `rel`.

    runoff_area  each depression's catchment area times its runoff coefficient, m2: the rain
                 depth times it is the catchment's own runoff
    volume       each depression's volume when full, m3

    The answer lies between volume / (runoff area of it and everything upstream) (all upstream
    water arrives) and volume / its own runoff area (none does). Rain depths stepping by 1 + rel
    across that range are tried in turn, each with one pass down the cascade; a depression's
    answer is interpolated between the last depth that left it short and the first that filled
    it. Depressions with an unknown (NaN) or zero runoff area get NaN, and so does everything
    downstream of them.
    """
    runoff_area = np.asarray(runoff_area, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    known = np.isfinite(runoff_area) & (runoff_area > 0) & np.isfinite(volume) & (volume > 0)
    tainted, _ = cascade(order, target, np.where(known, 0.0, np.nan), np.zeros(len(volume)))
    known &= ~np.isnan(tainted)
    area = np.where(known, runoff_area, 0.0)
    if not known.any():
        return np.full(len(area), np.nan)
    upstream, _ = cascade(order, target, area, np.zeros(len(area)))   # area of it and all upstream
    low = float(np.min(volume[known] / upstream[known])) / (1 + rel)
    high = float(np.max(volume[known] / area[known])) * (1 + rel)
    found = _rain_to_fill(order, target, area, np.where(known, volume, np.inf), low, high, 1 + rel)
    return np.where(known, found, np.nan)
