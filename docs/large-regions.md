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

## The whole country: the lower 48 states

Measured on 19 October 2026: USGS 3DEP 30 m over the box −124.85, 24.4, −66.9, 49.4 (the lower
48 states, with the coastal waters and the parts of Canada and Mexico that 3DEP covers), H3
resolution 10, `min_depth` 0.5 m, tiles of 4096, tiles **streamed** (none stored), on the same
laptop, run in four parts with `--hours`.

| | |
|---|---|
| Raster | 208,620 × 90,000 = 18.8 billion pixel positions, 1,122 tiles (many partly or wholly sea) |
| Depressions | 12,304,679 (240 touching the edge of the data) |
| H3 cells (resolution 10) | 82,027,455 |
| Border graph | 14.3 million tile-border pixels, 69.1 million crossings |
| Time, in four parts | 7.3 hours (2.0 + 2.0 + 2.0 + 1.2) |
| Extra memory at peak | 13.0 GB, for a few minutes while the border levels are solved; 12.4 GB while the tables are assembled; about 2 GB the rest of the time |
| Disk at peak | 12.5 GB (saved tiles and spill files, during the final step) |
| Disk afterwards (Parquet) | 3.1 GB: 0.41 GB depressions + 2.69 GB cells |
| Internet traffic | 100.5 GB |
| Verification | every invariant holds; 3 random windows re-run whole and tiled agree exactly |

Compared with the estimate made from Texas beforehand: the time (7 to 8 hours estimated) and the
traffic (85 to 100 GB) were as expected; memory (8 to 10 GB estimated) and output size (2 GB)
were higher, because the country has more depressions and cells per pixel than Texas.

Memory peaks twice. The **border solve** needs every tile's border summary at once (here
14.3 million border pixels); its result is saved, so a run resumed after it does not repeat it.
The **final assembly** holds one summary row per depression piece and joins pieces across tile
borders. Both grow with the region, so a computer with **16 GB of memory** is the practical
minimum for the lower 48 at resolution 10. A hierarchical version of the method would bound them
([Roadmap](roadmap.md)).

## Running in parts

A national run takes many hours. Every tile's result is saved to `_work/` in the output folder as
soon as it is finished, so the run never has to be done in one go:

- **Interrupted?** Run the same command again. Finished tiles are loaded, not recomputed; at most
  the tile that was in progress (well under a minute) is redone. This covers crashes, shutdowns,
  a lost internet connection and Ctrl+C.
- **On purpose, in parts:** `--hours 2` (or `hours=2` in Python) stops cleanly at the end of the
  first tile after two hours and exits with code 3, saying how far it got. Run the same command
  again for the next part, whenever convenient, until it finishes.
- **The same answer.** A run continued any number of times gives exactly the tables of a run never
  interrupted; the tests stop runs at many points, in both passes and while the tables are being
  written, and compare. A folder can only be continued with the settings it was started with.
- **Memory between the passes.** Joining the passes needs every tile's border summary in
  memory at once: for the lower 48 states this step peaked at 13 GB, for a few minutes. Its
  result (the exact water level of every tile-border pixel) is saved, so later parts of pass 2
  load one tile at a time and stay near 2 GB.
- **Disk.** The saved tiles take room until the run finishes: about 1.5 MB per tile for pass 1
  and roughly the size of the cell table for pass 2. They are deleted once the tables are written.

Why not simply cut the country into regions analyzed separately? Each region's edge would act as
an outlet, draining water across lines that do not exist, and depressions crossing those lines
would be cut in two. Saving tiles keeps the exact method and still allows the work to be split.

## Open water: the water mask

Run regions with `--water-mask` when lakes and reservoirs should count as open water (see
[Data sources](data-sources.md#water-mask-from-esa-worldcover)). The mask is built once before the
analysis. Every pixel next to water becomes an outlet, so the border graph grows (about 9 times
on a box of Lake Erie), and cells along shores are only partly covered, so their coverage is
measured exactly, pixel by pixel. Pass 1 records each tile's invalid pixels (compressed, about
a bit per pixel before compression), and coverage reads those records rather than the elevation
data again, which for streamed tiles would mean the internet once per shoreline cell. On the
Lake Erie box the masked analysis takes 10 s (15 s without the mask, since there is less land);
reading the elevation data per cell had taken 72 s, and on the Great Salt Lake box 75 s against
25 s now, with identical results.

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
again from the beginning. A streamed read that fails is retried up to ten times with a delay that at least doubles
(at least 17 minutes in all), after which the run stops with an error rather than produce a wrong
result.

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
