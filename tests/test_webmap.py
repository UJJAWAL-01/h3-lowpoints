"""The interactive web map: its data, its safety, its command line, and (with a browser) its page."""
import json
import os
import re
import shutil
import subprocess

import h3
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from h3lowpoints import analyze
from h3lowpoints.cli import main
from h3lowpoints.viz.webmap import build_map, map_from_dir


def payload_of(path):
    page = open(path, encoding="utf-8").read()
    raw = re.search(r'<script id="payload" type="application/json">(.*?)</script>', page, re.S).group(1)
    return json.loads(raw), page


@pytest.fixture
def result(tmp_path):
    rng = np.random.default_rng(0)
    from scipy import ndimage

    arr = ndimage.gaussian_filter(rng.normal(size=(150, 180)), 2.5) * 10 + np.linspace(0, 3, 180)
    arr[20:60, 100:140] = -9999.0
    prof = {"driver": "GTiff", "height": 150, "width": 180, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1), "nodata": -9999.0}
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype("float32"), 1)
    from shapely.geometry import box
    return analyze(str(path), res=12, min_depth=0.1,
                   drains_xy=np.array([[500050.0, 3349950.0]]),
                   buildings=[box(500020, 3349990, 500060, 3349960)])


def test_the_page_holds_valid_data(result, tmp_path):
    out = result.to_html(tmp_path / "m.html", title="My <area>")
    data, page = payload_of(out)
    assert "__DATA__" not in page and "__TITLE__" not in page
    assert data["title"] == "My <area>"
    assert [s[0] for s in data["stats"]] == ["depressions", "area depressed", "volume held", "deepest"]
    assert data["stats"][0][1] == f"{len(result.depressions):,}"
    west, south, east, north = data["bounds"]
    assert west < east and south < north
    lats, lons = zip(*[h3.cell_to_latlng(c) for c in result.cells["h3"]])
    assert west < min(lons) and east > max(lons) and south < min(lats) and north > max(lats)


def test_levels_run_from_the_analysis_resolution_to_coarser_ones(result, tmp_path):
    data, _ = payload_of(result.to_html(tmp_path / "m.html"))
    res = [level["res"] for level in data["levels"]]
    assert res[0] == 12 and res == sorted(res, reverse=True) and len(res) == 6
    counts = [len(level["rows"]) for level in data["levels"]]
    assert counts == sorted(counts, reverse=True)           # coarser means fewer cells
    first = data["levels"][0]
    assert first["cols"][:4] == ["h", "depth", "area", "volume"] and len(first["rows"]) == len(result.cells)
    assert first["edge_m"] == pytest.approx(h3.average_hexagon_edge_length(12, unit="m"))
    # roll-ups conserve area and volume
    for level in data["levels"]:
        ix = level["cols"].index("area")
        assert sum(r[ix] for r in level["rows"]) == pytest.approx(result.cells["depress_area_m2"].sum(), rel=1e-3)


def test_coverage_is_present_at_every_resolution_when_the_footprint_is_known(result, tmp_path):
    data, _ = payload_of(result.to_html(tmp_path / "m.html"))
    for level in data["levels"]:
        assert "covered" in level["cols"] and "covered_frac" in level["cols"]
        frac = [r[level["cols"].index("covered_frac")] for r in level["rows"]]
        assert min(frac) > 0 and max(frac) <= 1
    assert any(r[data["levels"][0]["cols"].index("covered_frac")] < 1 for r in data["levels"][0]["rows"])


def test_without_a_footprint_only_the_analysis_level_has_coverage(result, tmp_path):
    out = build_map(result.cells, result.depressions, result.meta, tmp_path / "m.html")
    data, _ = payload_of(out)
    assert "covered" in data["levels"][0]["cols"]
    assert all("covered" not in level["cols"] for level in data["levels"][1:])


def test_points_are_the_deepest_first_with_flags(result, tmp_path):
    data, _ = payload_of(result.to_html(tmp_path / "m.html", max_points=50))
    c = data["points"]["cols"]
    rows = data["points"]["rows"]
    assert c == ["lon", "lat", "depth", "area", "volume", "edge", "artifact", "drain"]
    assert len(rows) == min(50, len(result.depressions))
    depths = [r[c.index("depth")] for r in rows]
    assert depths == sorted(depths, reverse=True)
    assert depths[0] == pytest.approx(result.depressions["max_depth_m"].max(), abs=1e-3)
    assert any(r[c.index("artifact")] for r in rows) or not result.depressions["likely_building_artifact"].any()


def test_page_cannot_be_broken_by_text_in_the_title_or_data(result, tmp_path):
    nasty = "</title></script><script>window.pwned=1</script>"
    out = result.to_html(tmp_path / "m.html", title=nasty)
    data, page = payload_of(out)
    assert data["title"] == nasty                                   # the data is intact ...
    assert "<script>window.pwned" not in page                       # ... but cannot close the tags
    assert "</title></script>" not in page


def test_big_levels_are_left_out_and_the_page_says_so(result, tmp_path):
    n = len(result.cells)
    data, _ = payload_of(result.to_html(tmp_path / "m.html", max_cells=n - 1))
    assert data["levels"][0]["res"] < 12 and "not drawn" in data["subtitle"]
    with pytest.raises(ValueError, match="No resolution"):
        result.to_html(tmp_path / "x.html", max_cells=0)


def test_nothing_to_draw_is_an_error(tmp_path):
    flat = tmp_path / "f.tif"
    prof = {"driver": "GTiff", "height": 20, "width": 20, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 1, 1)}
    with rasterio.open(flat, "w", **prof) as dst:
        dst.write(np.zeros((20, 20), dtype="float32"), 1)
    with pytest.raises(ValueError, match="no depressions"):
        analyze(str(flat), res=12).to_html(tmp_path / "m.html")


def test_a_coarse_dem_adds_its_caveat(tmp_path):
    arr = np.full((60, 60), 100.0, dtype="float32")
    arr[20:35, 20:35] = 90.0
    prof = {"driver": "GTiff", "height": 60, "width": 60, "count": 1, "dtype": "float32",
            "crs": "EPSG:26914", "transform": from_origin(500000, 3350000, 30, 30), "nodata": -9999.0}
    with rasterio.open(tmp_path / "c.tif", "w", **prof) as dst:
        dst.write(arr, 1)
    res = analyze(str(tmp_path / "c.tif"), res=8, min_depth=1.0)
    data, _ = payload_of(res.to_html(tmp_path / "m.html"))
    assert "Coarse DEM" in data["caveat"] or "Medium" in data["caveat"]


def test_map_from_a_saved_folder(result, tmp_path):
    folder = result.save(tmp_path / "out")
    data, _ = payload_of(map_from_dir(folder, tmp_path / "m.html"))
    assert data["levels"][0]["res"] == 12 and len(data["levels"][0]["rows"]) == len(result.cells)
    with pytest.raises(ValueError, match="missing"):
        map_from_dir(tmp_path / "nowhere", tmp_path / "x.html")


def test_command_line(result, tmp_path):
    folder = result.save(tmp_path / "out")
    assert main(["map", str(folder), "--out", str(tmp_path / "a.html"), "--title", "From CLI"]) == 0
    assert payload_of(tmp_path / "a.html")[0]["title"] == "From CLI"
    dem = tmp_path / "dem.tif"
    assert dem.exists()
    assert main(["analyze", str(dem), "--res", "12", "--min-depth", "0.1", "--out", str(tmp_path / "r"),
                 "--map", str(tmp_path / "b.html")]) == 0
    assert (tmp_path / "b.html").exists()
    with pytest.raises(SystemExit, match="missing"):
        main(["map", str(tmp_path / "empty"), "--out", str(tmp_path / "c.html")])


# ------------------------------------------------------------------ the page itself

def _script(page):
    return re.findall(r"<script>(.*?)</script>", page, re.S)[-1]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to check the JavaScript syntax")
def test_the_page_script_is_valid_javascript(result, tmp_path):
    _, page = payload_of(result.to_html(tmp_path / "m.html"))
    js = tmp_path / "page.js"
    js.write_text(_script(page), encoding="utf-8")
    run = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_the_page_pins_its_libraries(result, tmp_path):
    _, page = payload_of(result.to_html(tmp_path / "m.html"))
    # an unpinned CDN URL can break the page silently when the library changes
    for url in re.findall(r'<script src="([^"]+)"', page):
        assert re.search(r"@\d+\.\d+\.\d+", url), url
    assert "h3-js@4" in page and "deck.gl@9" in page


def _chrome():
    for candidate in (os.environ.get("CHROME_PATH"), shutil.which("google-chrome"), shutil.which("chrome"),
                      r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


@pytest.mark.network
@pytest.mark.skipif(_chrome() is None, reason="needs Chrome or Edge")
@pytest.mark.parametrize("hash_", ["", "#res=9&metric=volume&base=satellite&points=1", "#base=dark&height=0"])
def test_the_page_runs_without_console_errors(result, tmp_path, hash_):
    out = result.to_html(tmp_path / "m.html")
    shot = tmp_path / "shot.png"
    run = subprocess.run(
        [_chrome(), "--headless=new", "--disable-gpu-sandbox", "--use-angle=swiftshader",
         "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-logging=stderr", "--v=0",
         "--window-size=1200,800", "--timeout=20000", f"--screenshot={shot}",
         out.as_uri() + hash_], capture_output=True, text=True, timeout=120)
    console = [ln for ln in run.stderr.splitlines() if "CONSOLE" in ln]
    problems = [ln for ln in console if re.search(r"Uncaught|TypeError|is not a function|ReferenceError", ln)]
    assert not problems, "\n".join(problems)
    assert shot.exists() and shot.stat().st_size > 20_000             # it drew something


def test_a_map_of_a_roll_up_says_so(tmp_path):
    import json as _json

    from h3lowpoints.pipeline import rollup

    from h3lowpoints import analyze
    from test_verify import dem

    res = analyze(dem(tmp_path / "d.tif"), res=12, min_depth=0.1)
    for cells, note in ((res.cells, "analysis"), (rollup(res.cells, 10), "rolled up from resolution 12")):
        page = build_map(cells, res.depressions, res.meta, tmp_path / "m.html", subtitle="my words").read_text(encoding="utf-8")
        payload = _json.loads(page.split('<script id="payload" type="application/json">')[1].split("</script>")[0])
        assert payload["first_note"] == note and payload["subtitle"] == "my words"
