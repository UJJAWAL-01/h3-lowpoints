# Contributing

Thanks for helping. The aim is a small library people can trust, so correctness and honesty
about limits matter more than features.

## Setup

```
git clone https://github.com/UJJAWAL-01/h3-lowpoints
cd h3-lowpoints
pip install -e ".[sources,viz,dev]"
pytest
ruff check src tests
```

CI runs both on Linux and Windows. The first call compiles the numba fill; later runs use a cache.

## Ground rules

- **Tests with every change.** Prefer properties (tiled equals whole, vectorized equals the
  simple loop, fill equals the reference method) over single examples. A bug fix needs a test
  that failed before it.
- **Say what is validated.** The depression core is checked against an independent method.
  Anything beyond that, such as the waterlogging classes, must state its evidence and its limits
  in `docs/`.
- **Parameters in metres**, never cells, so settings mean the same at any resolution.
- **Keep layers separate.** `hydro/` and `h3agg/` are the general core; `context/` (drains,
  buildings) and `waterlogging/` are optional; `sources/` holds data downloaders for specific
  places. `hydro/` and `h3agg/` must not import the others; only `pipeline.py` wires the layers together.
- Research scripts and exploratory notebooks do not belong in this repository.

## Good first contributions

- A `sources/` adapter for another country or city (DEM, drains, buildings).
- Validation of the waterlogging screen against local flood records, including negative results.
- Test terrain from places that differ from Austin: karst, glacial potholes, flat coast, steep hills.
- Exact tiled filling (Barnes 2016, two passes over a graph of spill levels), so tiled runs
  equal whole-raster runs without a buffer and `level_uncertain` disappears. Compare against
  `analyze` on random terrain, as `tests/test_tiling.py` does.

## Reporting a problem

Open an issue with the DEM's cell size and CRS, the call you made, and the `meta.json`.
