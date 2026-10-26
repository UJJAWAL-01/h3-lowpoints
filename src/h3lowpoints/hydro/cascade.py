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
def _rain_to_fill(order, target, area, retention, volume, low, high, step):
    n = len(order)
    found = np.full(n, np.nan)
    before = np.zeros(n)                            # inflow at the previous rain depth
    inflow = np.empty(n)
    rain_before, rain = 0.0, low
    while True:
        for i in range(n):                          # each catchment's SCS runoff, m3
            excess = rain - 0.2 * retention[i]
            inflow[i] = area[i] * excess * excess / (excess + retention[i]) / 1000.0 if excess > 0 else 0.0
        for i in order:
            extra = inflow[i] - volume[i]
            j = target[i]
            if j >= 0 and extra > 0:
                inflow[j] += extra
        for i in range(n):
            if np.isnan(found[i]) and inflow[i] >= volume[i]:
                # filled between the two depths; inflow grows with the rain in between, so
                # interpolating stays within the bracket
                share = (volume[i] - before[i]) / (inflow[i] - before[i])
                found[i] = rain_before + share * (rain - rain_before)
        if rain >= high:
            break
        rain_before = rain
        for i in range(n):
            before[i] = inflow[i]
        rain = min(rain * step, high)
    return found


@njit(cache=True)
def _upstream_min(order, target, values):
    out = values.copy()
    for i in order:
        j = target[i]
        if j >= 0 and out[i] < out[j]:
            out[j] = out[i]
    return out


def upstream_min(order, target, values):
    """For each depression, the smallest of `values` over it and every depression upstream."""
    return _upstream_min(order, target, np.asarray(values, dtype=np.float64))


def rain_to_fill_upstream(order, target, area, curve_number, volume, rel=0.01):
    """The uniform rain (mm) that fills each depression, counting the overflow of every
    depression upstream that fills first. Exact to within a factor 1 + `rel`.

    area          each depression's catchment area, m2
    curve_number  its catchment's SCS curve number (`runoff`)
    volume        its volume when full, m3

    No catchment sheds water before the rain passes its initial abstraction (0.2 S), and the
    answer is at most the rain that fills a depression from its own catchment. Rain depths
    stepping by 1 + rel from the smallest initial abstraction to the largest such own rain are
    tried in turn, each with one pass down the cascade; a depression's answer is interpolated
    between the last depth that left it short and the first that filled it. Depressions that
    nothing spills into get their own rain exactly. Depressions with an unknown (NaN) area or
    curve number get NaN, and so does everything downstream of them.
    """
    from ..runoff import rain_for_runoff_mm, retention_mm

    area = np.asarray(area, dtype=np.float64)
    cn = np.asarray(curve_number, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    known = (np.isfinite(area) & (area > 0) & np.isfinite(cn) & (cn > 0) & (cn <= 100)
             & np.isfinite(volume) & (volume > 0))
    tainted, _ = cascade(order, target, np.where(known, 0.0, np.nan), np.zeros(len(volume)))
    known &= ~np.isnan(tainted)
    if not known.any():
        return np.full(len(area), np.nan)
    retention = np.where(known, retention_mm(np.where(known, cn, 100.0)), 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        own = rain_for_runoff_mm(1000.0 * volume / area, np.where(known, cn, 100.0))
    low = max(float(np.min(0.2 * retention[known])), 1e-3) / (1 + rel)
    high = float(np.max(own[known])) * (1 + rel)
    found = _rain_to_fill(order, target, np.where(known, area, 0.0), retention,
                          np.where(known, volume, np.inf), low, high, 1 + rel)
    fed = np.zeros(len(area), dtype=bool)
    fed[target[target >= 0]] = True
    return np.where(known, np.where(fed, np.minimum(found, own), own), np.nan)
