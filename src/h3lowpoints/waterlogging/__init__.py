"""Optional add-on: flag depressions that may be waterlogging zones.

Everything here is a heuristic. It was checked against Austin, Texas 311 tickets on 1 m lidar and
is not known to transfer elsewhere. See docs/waterlogging.md.
"""
from .classify import CODES, WaterloggingParams, classify

__all__ = ["CODES", "WaterloggingParams", "classify"]
