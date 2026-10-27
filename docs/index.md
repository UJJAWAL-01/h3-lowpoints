# h3-lowpoints documentation

h3-lowpoints finds **closed depressions** (low points in the ground that water cannot flow out of)
in an elevation model, and describes them on **H3 hexagon cells** at any resolution, ready for
maps, 3D views and analysis.

It is written for GIS analysts, urban planners, researchers and developers who want to know
where the terrain holds water, at any scale from a city block to a whole state, without
running a GIS desktop application.

> **Status: alpha (0.x).** The method is tested against independent methods and on real terrain
> (see [Verification](verification.md)). A depression is a feature of the terrain model: this
> library does not say whether water actually collects there, and its output is **not a flood
> map**. See [Accuracy and limits](limitations.md).

**National dataset:** every closed depression on land in the lower 48 US states (USGS 3DEP
30 m, open water masked), on H3, with catchments, spill links and fill curves, is published as
open data: [doi.org/10.5281/zenodo.23669497](https://doi.org/10.5281/zenodo.23669497).

## Where to start

| If you want to... | Read |
|---|---|
| Try it in five minutes | [Getting started](getting-started.md) |
| Understand the ideas (elevation models, depressions, H3) from scratch | [Concepts](concepts.md) |
| Know exactly what every number means | [Outputs](outputs.md) and [Definition](definition.md) |
| Look up a function or a parameter | [Python API](python-api.md) |
| Look up a command | [Command line](cli.md) |
| Choose and download elevation data | [Data sources](data-sources.md) |
| Analyze a state or a country | [Large regions](large-regions.md) |
| Read and share the interactive map | [The map](map.md) |
| Know how results are checked, and check your own | [Verification](verification.md) |
| Test the depressions against observed flooding | [Validation](validation.md) |
| Know how easily each depression fills in a storm | [Susceptibility](susceptibility.md) |
| See example maps of three test areas | [Example maps](example-maps.md) |
| Know what the results cannot tell you | [Accuracy and limits](limitations.md) |
| Look up a term | [Glossary](glossary.md) |
| See where the project is going | [Roadmap](roadmap.md) |

## All pages, in reading order

1. [Getting started](getting-started.md): install, a first analysis, a first map.
2. [Concepts](concepts.md): elevation models, closed depressions, filling, H3, resolution and
   coverage, explained without assuming GIS knowledge.
3. [Definition](definition.md): the precise contract, what is computed and how edge cases are
   treated.
4. [Outputs](outputs.md): every file, every column, every field of `meta.json`, and how to open
   them in other tools.
5. [Python API](python-api.md): every public function, class and parameter.
6. [Command line](cli.md): every command and option.
7. [Data sources](data-sources.md): the elevation, land cover and rainfall data the library can
   fetch, with their accuracy, coverage, licences and quirks.
8. [Large regions](large-regions.md): how regions far larger than memory are analyzed exactly,
   what it costs, and how to run one.
9. [The map](map.md): the interactive web map, its controls and its shareable links.
10. [Verification](verification.md): the checks every result can be put through, anywhere.
11. [Validation](validation.md): whether observations (flood reports, claims) fall in or near
    depressions more often than chance, with honest uncertainty.
12. [Susceptibility](susceptibility.md): how easily each depression fills: runoff, the rain that
    fills it, and design storms.
13. [Example maps](example-maps.md): interactive maps of three test areas (Houston, North Dakota,
    the Central Valley), a preview of the explorer.
14. [Accuracy and limits](limitations.md): what the results can and cannot be used for.
15. [Glossary](glossary.md): terms used across these pages.
16. [Roadmap](roadmap.md): what is done and what comes next.

The [changelog](../CHANGELOG.md) lists every change, and [contributing](../CONTRIBUTING.md) explains
how to help.

## In one paragraph

Give the library an elevation raster (a GeoTIFF, or a box to download). It fills every pit the
way water would, up to the level at which it would spill out, and the difference between the
filled surface and the ground is the **depth** of water each spot could hold. Connected areas of
positive depth are **depressions**; each gets a depth, an area, a volume and a lowest point. Every
depressed pixel is then assigned to the H3 cell that contains its centre, giving one row per
cell with the deepest depth, the area and volume held, the number of lowest points and how much
of the cell the data covered. Coarser H3 resolutions are exact roll-ups of the finest one, so the
same result can be viewed from a street to a continent.

## How it compares with existing data

Other national maps of US depressions exist, and may suit some uses better:

- **National Surface Depressions** (Qiusheng Wu, on [Source Cooperative](https://source.coop/giswqs/depressions)):
  depression outlines for the lower 48 states from the 10 m 3DEP model, by the level-set method
  that also describes depressions nested in larger ones (Wu et al. 2019, *JAWRA* 55(2), 354–368,
  [doi.org/10.1111/1752-1688.12689](https://doi.org/10.1111/1752-1688.12689)), with area, volume
  and depth, and a separate set outside floodplains. Finer than the 30 m used here; prefer it
  when you need outlines.
- **Closed depression density in karst regions of the conterminous US** (USGS,
  [catalog.data.gov](https://catalog.data.gov/dataset/closed-depression-density-in-karst-regions-of-the-conterminous-united-states-features-and-)):
  closed depressions in karst, for sinkhole studies.

What this project adds: a library that runs on any elevation model anywhere; results on H3
cells, exact at every resolution; each depression's catchment and the depression it spills into;
fill curves; for the US, which storm fills each depression (counting overflow from upstream) and
whether a mapped stream, canal or highway drains it; and tools to test all of it against
observations, with a published test. We did not find another open dataset that combines these;
if you know of one, please open an issue.

## Citing and licence

Code is Apache-2.0. If you use the library in published work, cite it with its DOI,
[doi.org/10.5281/zenodo.23552522](https://doi.org/10.5281/zenodo.23552522) (all versions), or `CITATION.cff` in the
repository. Data you download through it keeps its own licence (see
[Data sources](data-sources.md)).
