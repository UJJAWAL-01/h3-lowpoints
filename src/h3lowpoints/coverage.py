"""How much of each H3 cell does the DEM actually cover?

A cell at the edge of the DEM, or beside a lake or sea that has no elevation data, holds depressions
from only part of its area. Reading its totals next to a fully covered cell would mislead, so
every cell gets `coverage` (0 to 1) and `covered_area_m2`.

Definition. A pixel belongs to the H3 cell that contains its centre, the same rule that assigns
depressions to cells. The coverage of a cell is the area of its valid pixels divided by the area
of all pixel positions inside it, counting positions beyond the edge of the raster as not covered.
`covered_area_m2` is the coverage times the cell's exact H3 area.

Method. Almost every cell lies wholly inside valid data, and its coverage is exactly 1. A small
bitmap with one flag per 16 x 16 block of pixels (set when the block holds an invalid pixel)
finds the cells that could be partial: those whose bounding box touches a flagged block or leaves
the raster. Only those are measured exactly, by assigning each pixel position in the box to
its H3 cell. A cell larger than `SAMPLE_LIMIT` pixel positions is measured on a regular
sub-lattice anchored to the pixel grid (so the result does not depend on tiling), which keeps
the cost bounded at the price of a coverage error well under 1%.
"""
import contextlib
import math

import h3
import numpy as np
import pandas as pd
from affine import Affine
from pyproj import Transformer

from .geometry import crs_info, pixel_areas_m2
from .h3agg.aggregate import h3_ids, lonlat

BLOCK = 16
SAMPLE_LIMIT = 40_000   # pixel positions measured per partial cell before sampling kicks in
CHUNK = 100_000         # cells handled at a time, so memory stays flat for any number of cells


def _block_shape(shape):
    return -(-shape[0] // BLOCK), -(-shape[1] // BLOCK)


def mark_invalid_blocks(blocks, invalid, origin):
    """Flag, in the whole-raster bitmap `blocks`, every 16 x 16 block that holds an invalid pixel.

    `invalid` is a window of the raster whose top-left pixel is `origin` = (row, col).
    """
    r0, c0 = origin
    off_r, off_c = r0 % BLOCK, c0 % BLOCK
    h, w = invalid.shape
    rows, cols = -(-(h + off_r) // BLOCK) * BLOCK, -(-(w + off_c) // BLOCK) * BLOCK
    padded = np.zeros((rows, cols), dtype=bool)
    padded[off_r:off_r + h, off_c:off_c + w] = invalid
    flags = padded.reshape(rows // BLOCK, BLOCK, cols // BLOCK, BLOCK).any(axis=(1, 3))
    br, bc = r0 // BLOCK, c0 // BLOCK
    blocks[br:br + flags.shape[0], bc:bc + flags.shape[1]] |= flags


class Footprint:
    """Where a DEM has valid data, and the coverage of any H3 cell derived from it.

    `opener()` returns a context manager yielding `read(r0, r1, c0, c1)`, which gives the
    boolean "invalid" window of the raster for rows r0..r1 and columns c0..c1 (inside the raster).
    """

    def __init__(self, shape, transform, crs, blocks, opener):
        self.shape, self.transform, self.crs = tuple(shape), transform, crs
        self.blocks, self._opener = blocks, opener
        self._geographic, _ = crs_info(crs)
        left, right = transform.c, transform.c + shape[1] * transform.a
        self._lon_range = (min(left, right), max(left, right))

    @classmethod
    def from_array(cls, invalid, transform, crs):
        blocks = np.zeros(_block_shape(invalid.shape), dtype=bool)
        mark_invalid_blocks(blocks, invalid, (0, 0))

        @contextlib.contextmanager
        def opener():
            yield lambda r0, r1, c0, c1: invalid[r0:r1, c0:c1]

        return cls(invalid.shape, transform, crs, blocks, opener)

    # -- geometry of one cell in pixel space ------------------------------------------------
    def _boxes(self, cells):
        """Conservative pixel bounding box (r0, r1, c0, c1), end exclusive, of each cell."""
        verts, counts = [], []
        for c in cells:
            b = h3.cell_to_boundary(c)
            lat0, lon0 = h3.cell_to_latlng(c)
            lons = [lon + 360 if lon - lon0 < -180 else lon - 360 if lon - lon0 > 180 else lon
                    for _, lon in b]
            shift = self._wrap(min(lons), max(lons)) if self._geographic else 0.0
            verts.extend((lon + shift, lat) for lon, (lat, _) in zip(lons, b))
            counts.append(len(b))
        verts = np.asarray(verts, dtype="float64").reshape(-1, 2)
        if self._geographic:
            x, y = verts[:, 0], verts[:, 1]
        else:
            x, y = Transformer.from_crs(4326, self.crs, always_xy=True).transform(verts[:, 0], verts[:, 1])
        col, row = ~self.transform @ (np.asarray(x), np.asarray(y))
        edges = np.concatenate([[0], np.cumsum(counts)])
        r_min, r_max = np.minimum.reduceat(row, edges[:-1]), np.maximum.reduceat(row, edges[:-1])
        c_min, c_max = np.minimum.reduceat(col, edges[:-1]), np.maximum.reduceat(col, edges[:-1])
        return np.column_stack([np.floor(r_min) - 1, np.ceil(r_max) + 1,
                                np.floor(c_min) - 1, np.ceil(c_max) + 1]).astype(np.int64)

    def _wrap(self, lon_min, lon_max):
        """The multiple of 360 degrees that puts a cell's longitudes nearest the raster.

        A cell beside the antimeridian can have its centre on the far side of it from the pixels
        it holds, so its longitudes must be wrapped to where the raster is.
        """
        left, right = self._lon_range
        best, best_gap = 0.0, None
        for shift in (0.0, -360.0, 360.0):
            gap = max(0.0, left - (lon_max + shift), (lon_min + shift) - right)
            if best_gap is None or gap < best_gap:
                best, best_gap = shift, gap
        return best

    def _could_be_partial(self, box):
        return bool(self._partial_mask(np.asarray([box]))[0])

    def _partial_mask(self, boxes):
        """For each box: does it leave the raster, or touch a block holding an invalid pixel?

        A summed-area table over the block bitmap answers each box with four lookups.
        """
        if getattr(self, "_prefix", None) is None:
            prefix = np.zeros((self.blocks.shape[0] + 1, self.blocks.shape[1] + 1), dtype=np.int64)
            prefix[1:, 1:] = self.blocks.cumsum(axis=0).cumsum(axis=1)
            self._prefix = prefix
        h, w = self.shape
        r0, r1, c0, c1 = (boxes[:, i] for i in range(4))
        outside = (r0 < 0) | (c0 < 0) | (r1 > h) | (c1 > w)
        br0 = np.clip(r0, 0, h - 1) // BLOCK
        br1 = (np.clip(r1, 1, h) - 1) // BLOCK + 1
        bc0 = np.clip(c0, 0, w - 1) // BLOCK
        bc1 = (np.clip(c1, 1, w) - 1) // BLOCK + 1
        p = self._prefix
        invalid_blocks = p[br1, bc1] - p[br0, bc1] - p[br1, bc0] + p[br0, bc0]
        return outside | (invalid_blocks > 0)

    def _exact(self, cell, box, read, res):
        """Coverage of one cell by assigning the pixel positions in its box to H3 cells."""
        r0, r1, c0, c1 = (int(v) for v in box)
        n = (r1 - r0) * (c1 - c0)
        step = 1 if n <= SAMPLE_LIMIT else math.ceil(math.sqrt(n / SAMPLE_LIMIT))
        rows = np.arange(-(-r0 // step) * step, r1, step)      # lattice anchored to the pixel grid
        cols = np.arange(-(-c0 // step) * step, c1, step)
        rr, cc = np.meshgrid(rows, cols, indexing="ij")
        x, y = self.transform @ (cc + 0.5, rr + 0.5)
        lon, lat = lonlat(self.crs, np.asarray(x), np.asarray(y))
        member = h3_ids(lat.ravel(), lon.ravel(), res) == int(cell, 16)
        # which sampled positions hold valid data (positions beyond the raster do not)
        h, w = self.shape
        inside = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
        valid = np.zeros(rr.shape, dtype=bool)
        if inside.any():
            win_r0, win_r1 = max(r0, 0), min(r1, h)
            win_c0, win_c1 = max(c0, 0), min(c1, w)
            invalid = read(win_r0, win_r1, win_c0, win_c1)
            valid[inside] = ~invalid[rr[inside] - win_r0, cc[inside] - win_c0]
        areas = self._row_areas(rows.min(), rows.max() + 1)[rr - rows.min()]
        member_area = np.where(member.reshape(rr.shape), areas, 0.0)
        total = member_area.sum()
        # two sums of the same numbers in a different order can exceed 1 by a rounding error
        return min(1.0, float(member_area[valid].sum() / total)) if total > 0 else 1.0

    def _row_areas(self, r_start, r_stop):
        shifted = self.transform @ Affine.translation(0, r_start)
        return pixel_areas_m2(shifted, self.crs, int(r_stop - r_start))

    # -- public -----------------------------------------------------------------------------
    def coverage(self, cells, chunk=CHUNK):
        """DataFrame with `coverage` and `covered_area_m2` for H3 cells (a Series or list of ids).

        Cells are handled `chunk` at a time, so memory does not grow with the number of cells.
        """
        cells = list(cells)
        n = len(cells)
        if not n:
            return pd.DataFrame({"coverage": [], "covered_area_m2": []})
        res = h3.get_resolution(cells[0])
        coverage, nominal = np.ones(n), np.empty(n)
        with contextlib.ExitStack() as stack:
            read = None
            for start in range(0, n, chunk):
                part = cells[start:start + chunk]
                boxes = self._boxes(part)
                for i in np.nonzero(self._partial_mask(boxes))[0]:
                    if read is None:              # open the DEM only if some cell needs it
                        read = stack.enter_context(self._opener())
                    coverage[start + i] = self._exact(part[i], boxes[i], read, res)
                nominal[start:start + len(part)] = [h3.cell_area(c, unit="m^2") for c in part]
        return pd.DataFrame({"coverage": coverage, "covered_area_m2": coverage * nominal})


def file_opener(dem_path, mask_path=None):
    """An opener for `Footprint` that reads validity windows from a DEM file (and a mask file)."""
    import rasterio
    from rasterio.windows import Window

    from .sources._common import gdal_env

    @contextlib.contextmanager
    def opener():
        with gdal_env(), rasterio.open(dem_path) as src, \
                (rasterio.open(mask_path) if mask_path else contextlib.nullcontext()) as mask:
            def read(r0, r1, c0, c1):
                window = Window(c0, r0, c1 - c0, r1 - r0)
                arr = src.read(1, window=window)
                invalid = np.isnan(arr)
                if src.nodata is not None:
                    invalid |= arr == src.nodata
                if mask is not None:
                    invalid |= mask.read(1, window=window).astype(bool)
                return invalid
            yield read
    return opener

