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
Tests marked `network` read real data and drive a headless browser; run them with
`H3LP_NETWORK=1 pytest` (they need internet, and Chrome or Edge for the map test).

## Ground rules

- **Tests with every change.** Prefer properties (tiled equals whole, vectorized equals the
  simple loop, fill equals the reference method) over single examples. A bug fix needs a test
  that failed before it.
- **Say what is validated.** The depression core is checked against an independent method.
  Anything beyond that must state its evidence and its limits in `docs/`. One city is not
  evidence for a global method.
- **Parameters in metres**, never cells, so settings mean the same at any resolution.
- **Keep layers separate.** `hydro/` and `h3agg/` are the general core; `context/` (drains,
  buildings) is optional; `sources/` holds data downloaders for specific places. `hydro/` and `h3agg/` must not import the others; only `pipeline.py` wires the layers together.
- **Document what you add.** The documentation in `docs/` is published with the library, so a
  new parameter, column, command option or metadata field needs its line there.
  `tests/test_docs.py` fails when a command option, API function, output column or metadata field
  is missing from the docs, or when a link or anchor is broken.
- Research scripts and exploratory notebooks do not belong in this repository.

## Good first contributions

- A `sources/` adapter for another country or city (DEM, drains, buildings).
- A `sources/` reader for another open DEM (national lidar programs, FABDEM, NASADEM), or a
  water-mask source to pair with it. Follow `sources/usgs.py`: read only the windows a box needs,
  treat only HTTP 404 as "missing" (`_common.url_exists`), and test against fake tiles that copy the
  real layout, plus a `network` test against the real service.
- Validation of the planned susceptibility index against local flood records, including negative results.
- Test terrain from varied places: karst, glacial potholes, flat coast, steep hills.
- A hierarchical version of the exact tiling for continental rasters (the border graph is the
  last part of memory that grows with the region). Compare against `analyze`, as
  `tests/test_tiling.py` does.
- A vertical-units option for DEMs whose heights are in feet (see docs/limitations.md).

## Reporting a problem

Open an issue with the DEM's cell size and CRS, the call you made, and the `meta.json`.
