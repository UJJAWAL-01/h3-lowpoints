from dataclasses import dataclass

import numpy as np

CODES = {"minor": 1, "drained": 2, "suspect_pit": 3, "unexplained": 4}


@dataclass(frozen=True)
class WaterloggingParams:
    """Thresholds of the waterlogging heuristic. Distances are metres, areas square metres.

    The defaults were fixed before validation against Austin 311 tickets; changing them is a
    deliberate analysis choice.
    """
    sig_depth: float = 0.3      # a "significant" depression is at least this deep ...
    sig_area: float = 150.0     # ... and at least this large
    pit_depth: float = 1.5      # a "suspect pit" is deep but small: often a lidar artifact
    pit_area: float = 150.0
    core_frac: float = 0.8      # a drain must be near the part deeper than this fraction of max
    drain_radius_m: float = 5.0


def classify(max_depth, area, served, edge, params=WaterloggingParams()):
    """Class of each depression.

    drained      a recorded drain is near its deepest part
    suspect_pit  very deep for its area; likely an artifact or an excavation
    unexplained  significant (deep and large) with no recorded drain: the candidates of interest
    minor        everything else
    Depressions that touch the data border (`edge`) are never significant or suspect pits,
    because their true size is unknown.
    """
    p = params
    significant = (max_depth >= p.sig_depth) & (area >= p.sig_area) & ~edge
    pit = (max_depth >= p.pit_depth) & (area < p.pit_area) & ~edge
    cls = np.where(served, "drained", np.where(pit, "suspect_pit",
                   np.where(significant, "unexplained", "minor")))
    return cls, significant, pit
