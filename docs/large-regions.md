# Large regions

A state or a country at 30 m is billions of pixels, far more than fits in memory, and its result
has millions of rows. This page explains how the library analyzes such regions **exactly** (the
answer is the same as if the whole raster had been analyzed at once), what it costs, and how to
run one.

## The short version

```
h3lowpoints build --bbox -106.65 25.84 -93.51 36.50 --out texas --min-depth 0.5 \
    --cache-dir tiles --format parquet
```

or in Python, for any raster (a GeoTIFF, or a VRT of many files):

```python
from h3lowpoints import analyze_tiled
res = analyze_tiled("big.vrt", tile=4096, min_depth=0.5, out_dir="texas", format="parquet")
res.meta["n_depressions"], res.meta["output"]["n_cells"]
```

The tables go straight to the output folder; `read_result("texas")` or the files themselves are
used afterwards (see [Outputs](outputs.md)). Leave out `--cache-dir` to read the tiles over the
internet instead of storing them: slower, but it needs almost no disk (see
[Streaming or caching the tiles](#streaming-or-caching-the-tiles)).

## Why tiling is hard for depressions

Most raster analyses can cut the data into tiles and process each on its own. Filling cannot:
whether a pit holds water depends on the lowest way out, which can lie hundreds of kilometres
away. A tile seen alone treats its own border as an outlet, so every depression that touches a
tile border would drain away, and a depression spanning several tiles would be split.

## How the library does it exactly

The method follows Barnes, Callaghan and Wheeler (2016), "Parallel priority-flood depression
filling for trillion cell digital elevation models":

1. **Pass 1: summarize each tile.** Each tile is flooded from its own border, recording only how
   water could move between points on that border and the lowest level at which it could cross
   (a few numbers per border pixel). The interior is then forgotten.
2. **Solve the borders.** The border pixels of all tiles, joined where tiles touch (diagonals
   included), form one graph. A shortest-path computation over it (where a path's cost is its
   highest point) gives every border pixel the exact level water stands at there in the full
   raster.
3. **Pass 2: finish each tile.** Each tile is flooded again, starting its border from those exact
   levels, which gives its exact depths. Depressions are labelled within the tile, and pieces that
   meet across tile borders are joined into one depression with one lowest point.

Why the border summaries suffice is proven in [Definition](definition.md#tiles). The test suite
compares tiled and whole runs depression by depression and cell by cell on many kinds of terrain
and tile sizes, and on real terrain in five countries; they agree exactly.

## How memory is kept bounded

What has to be held at once:

| Held | Size |
|---|---|
| One tile being processed | about 30 to 40 bytes per pixel: 0.6 GB for a 4096 × 4096 tile |
| The border graph | a few tens of bytes per tile-border pixel; grows with the region (see below) |
| One summary row per depression piece | grows with the number of depressions |
| One partition of cells at a time | bounded (see below) |
| GDAL's read cache | capped at 256 MB (GDAL's default is 5% of the machine's memory) |

What is **not** held:

- **Per-cell sums wait on disk.** As each tile finishes, its depressed pixels are summed per
  (depression piece, H3 cell) and appended to a spill file for their **partition**: the cells
  sharing one H3 parent six resolutions coarser (at most 7⁶ ≈ 118,000 cells per partition; a
  resolution-4 parent for a resolution-10 table). Because H3 ids sort parent by parent, the
  partitions come out in id order.
- **Tables are written as they are produced.** At the end, partitions are read one at a time,
  pieces of depressions that were dropped (shallower than `min_depth` once joined) are removed,
  the cells are summed, counted and measured for coverage, and appended to the output file. The
  depression table is written in pieces of 500,000 rows. The spill folder is removed afterwards,
  also when the run fails.

Without `out_dir`, the same partitions are kept in memory and the tables are returned; this is
the right choice for anything up to a few hundred million pixels.

## Choosing the tile size

The tile size never changes the result. Large tiles need more memory per tile; small tiles make
the border graph larger (it has a node per tile-border pixel) and add per-tile overhead. Measured
on 38.9 million pixels of real terrain around Austin:

| mode | time | extra memory at peak |
|---|---|---|
| `analyze` (whole raster) | 28 to 30 s | 1.4 to 1.5 GB |
| `analyze_tiled`, 2048-pixel tiles | 34 s | 0.50 GB |
| `analyze_tiled`, 1024-pixel tiles | 30 s | 0.31 GB |
| `analyze_tiled`, 512-pixel tiles | 29 s | 0.43 GB |

For regions, 4096 (the default) is a good balance: about 0.6 GB per tile, and a border graph of
about one node per 1,000 pixels.

## What a state costs: Texas

USGS 3DEP 30 m over the box −106.65, 25.84, −93.51, 36.50 (Texas and its surroundings, including
parts of Mexico and the Gulf): 155 tiles, 47,304 × 38,376 = **1.82 billion pixels**, analyzed at
H3 resolution 10 with `min_depth` 0.5 m, tiles of 4096, on a laptop (4 cores, 8 threads, 25.5 GB
memory). The analysis itself runs on one core.

| | |
|---|---|
| Depressions | 1,745,430 |
| H3 cells (resolution 10) | 7,906,030 |
| Download of the tiles (6.0 GB, once, into `--cache-dir`) | 10 to 11 minutes |
| Analysis | 25 minutes |
| Verification of the saved result (in pieces) | 31 seconds, no problems |
| Extra memory at peak, analysis | **2.1 GB** |
| Extra memory at peak, verification | 1.7 GB (0.6 GB of data; the rest is the Parquet reader's reusable buffers) |
| Output, Parquet | 56 MB depressions + 273 MB cells |
| Output, CSV (same content) | 1.1 GB |

How the memory came down over the course of development, for this same run with identical output:

| Version | Extra memory at peak |
|---|---|
| First tiled version (tables assembled with pandas in memory) | 11.0 GB |
| Per-tile summaries as arrays, chunked coverage, capped GDAL cache | 3.6 GB |
| Per-cell sums spilled to disk, tables written as produced (`out_dir`) | 2.1 GB |

About 0.9 GB of the 2.1 GB does not grow with the region (one tile and the GDAL cache); the rest
is the border graph and one summary row per depression piece.

## Scaling to the whole country

The lower 48 states are roughly six to seven times the Texas box. Time grows with the number of
pixels; memory now grows only with the border graph and the per-depression summaries, not with
the cell table.

An estimate from the Texas measurement, not yet a measurement, for 3DEP 30 m at resolution 10
and `min_depth` 0.5 m:

| | Texas box (measured, tiles cached) | Lower 48, tiles cached (estimate) | Lower 48, tiles streamed (estimate) |
|---|---|---|---|
| Pixels | 1.8 billion | 11 to 13 billion | 11 to 13 billion |
| Time | 11 min download + 25 min analysis | about 1 h download + 3 h analysis | 7 to 8 hours |
| Extra memory at peak | 2.1 GB | 8 to 10 GB | 8 to 10 GB |
| Disk during the run | 6 GB tiles + results | 40 GB tiles + about 5 GB | about 5 GB |
| Disk afterwards (Parquet results) | 0.33 GB | about 2 GB | about 2 GB |
| Internet traffic | 6 GB | 40 GB | 85 to 100 GB |

The streamed column scales the 4-tile measurement below.

So the national layer fits a single laptop with 16 GB of memory or more. The part that still grows
with the region is the border graph (one node per tile-border pixel) and the per-depression
summaries; a hierarchical version of the method would bound those too ([Roadmap](roadmap.md)).
The national run itself will be measured and its figures published with the data.

## Streaming or caching the tiles

A tiled run reads every pixel twice (once per pass). `build` can get the 3DEP tiles in two ways,
with exactly the same result:

- **Streamed** (no `--cache-dir`): each part of a tile is read over the internet when it is
  needed, and nothing is stored. Needs almost no disk, but every read waits on the network and
  each tile is fetched twice.
- **Cached** (`--cache-dir DIR`): each tile is downloaded once (about 40 MB per 1° tile at 30 m)
  and read from disk. Faster, but needs room for all the tiles; delete the folder afterwards.

Measured on one box of 2° × 2° (−99, 30, −97, 32: 4 tiles, 51.8 million pixels, 54,619
depressions) on the same laptop and home internet connection, one run each way:

| | Streamed | Cached |
|---|---|---|
| Getting the tiles | 1 s (only checks they exist) | 22 s (221 MB downloaded) |
| Analysis | 114 s | 47 s |
| Total | **114 s** | **69 s** |
| Internet traffic | 398 MB | 221 MB |
| Disk used | 8 MB (results) | 219 MB tiles + 8 MB results |
| Extra memory at peak | 1.2 GB | 1.1 GB |
| Result | identical | identical |

So streaming took about 1.65 times as long in total and moved about 1.8 times as much data,
in exchange for storing nothing. The ratio depends on the connection; measure a small box first
on yours. If a run is interrupted, cached tiles already downloaded are kept (a half-downloaded
tile is never used) and the next run fetches only the missing ones; the analysis itself starts
again from the beginning. A streamed read that fails is retried three times, after which the run
stops with an error rather than produce a wrong result.

## Practical advice

- **Cache the tiles if you have the disk space**, stream them if you do not (see above). If your
  internet plan has a data cap, note that a streamed national run moves 85 to 100 GB.
- **Use Parquet** for large outputs: smaller files, exact types, fast to read, and DuckDB can
  query it without loading everything into memory.
- **Keep the computer awake.** A sleeping laptop pauses the run (it resumes afterwards, but wall
  time becomes meaningless).
- **Choose `min_depth` for the data.** On 30 m data, depressions under about 0.5 m are within the
  vertical error and also multiply the output size.
- **Check the result.** `build` runs the invariants on completion and writes `verify.json`; add
  `--spot-check N` to re-run N random windows whole and tiled.
- **Disk space.** Besides any tile cache and the results, the spill files need 40 bytes per
  (depression piece, cell) pair while the run lasts; they are removed when it ends.
