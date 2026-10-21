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
11. [Accuracy and limits](limitations.md): what the results can and cannot be used for.
12. [Glossary](glossary.md): terms used across these pages.
13. [Roadmap](roadmap.md): what is done and what comes next.

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

## Author, citing and licence

h3-lowpoints is created and maintained by **Ujjawal Patel**
([@UJJAWAL-01](https://github.com/UJJAWAL-01)). Code is Apache-2.0, copyright 2026 Ujjawal Patel. If you use the library in published work, cite it using `CITATION.cff` in
the repository. Data you download through it keeps its own licence (see
[Data sources](data-sources.md)).
