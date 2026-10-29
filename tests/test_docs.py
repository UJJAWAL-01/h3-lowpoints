"""The documentation must match the code: links resolve, and every option, function, column and
metadata field it names exists, and every one the code has is documented."""
import importlib
import re
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

from h3lowpoints import analyze_array, read_result
from h3lowpoints.cli import build_parser
from h3lowpoints.pipeline import (BUILDING_COLUMNS, CATCHMENT_COLUMNS, CATCHMENT_TABLE, CELL_TABLE,
                                  CORE_COLUMNS, DEPRESSION_CELL_TABLE, DETAIL_COLUMNS,
                                  DRAIN_COLUMNS)
from h3lowpoints.drainage import DRAINAGE_COLUMNS
from h3lowpoints.storms import STORM_COLUMNS
from h3lowpoints.susceptibility import BASE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md", ROOT / "CONTRIBUTING.md"]
LINK = re.compile(r"\]\(([^)\s]+)\)")


def slug(heading):
    """The anchor GitHub gives a heading."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path):
    return {slug(m.group(1)) for m in re.finditer(r"^#+\s+(.*)$", path.read_text(encoding="utf-8"), re.M)}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_link_resolves(doc):
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file, _, anchor = target.partition("#")
        path = (doc.parent / file).resolve() if file else doc
        assert path.exists(), f"{doc.name}: broken link {target}"
        if anchor and path.suffix == ".md":
            assert anchor in anchors(path), f"{doc.name}: no heading for #{anchor} in {path.name}"


def test_every_page_is_in_the_index():
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    for page in (ROOT / "docs").glob("*.md"):
        if page.name != "index.md":
            assert f"({page.name})" in index, f"{page.name} is not listed in docs/index.md"


def _cli_options():
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    return {name: {s for a in p._actions for s in a.option_strings if s.startswith("--") and s != "--help"}
            for name, p in sub.choices.items()}


def test_every_command_option_is_documented_and_every_documented_one_exists():
    text = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    sections = dict(re.findall(r"^## (\S+)\n(.*?)(?=^## |\Z)", text, re.M | re.S))
    for command, options in _cli_options().items():
        assert command in sections, f"no section for {command}"
        # options count as documented in option tables and in usage lines, not in passing prose
        lines = [ln for ln in sections[command].splitlines()
                 if ln.startswith("| `--") or ln.startswith("h3lowpoints ")]
        documented = set(re.findall(r"(--[a-z][a-z-]*)", " ".join(lines)))
        assert options <= documented, f"{command}: undocumented {options - documented}"
        assert documented <= options, f"{command}: documented but missing {documented - options}"


def test_every_documented_function_exists():
    text = (ROOT / "docs" / "python-api.md").read_text(encoding="utf-8")
    names = re.findall(r"^### `([\w.]+)\(", text, re.M)
    assert len(names) > 15
    for name in names:
        module, _, attr = ("h3lowpoints." + name).rpartition(".")
        if module == "h3lowpoints":
            obj = getattr(importlib.import_module("h3lowpoints"), attr)
        else:
            obj = getattr(importlib.import_module(module), attr, None)
        assert callable(obj), f"python-api.md documents {name}, which does not exist"


def test_every_column_is_documented():
    text = (ROOT / "docs" / "outputs.md").read_text(encoding="utf-8")
    for column in (CORE_COLUMNS + DRAIN_COLUMNS + BUILDING_COLUMNS + CATCHMENT_COLUMNS + CELL_TABLE
                   + CATCHMENT_TABLE + DETAIL_COLUMNS + DEPRESSION_CELL_TABLE + BASE_COLUMNS
                   + ["fill_ratio", "fill_ratio_2yr", "fill_ratio_10yr", "fill_ratio_100yr",
                      "fills_in_years"] + DRAINAGE_COLUMNS + STORM_COLUMNS
                   + ["curve_number", "seepage_mm_h", "drain_mm_h", "soil_fallback_share"]):
        assert f"`{column}`" in text, f"column {column} is not described in outputs.md"


def test_every_metadata_field_is_documented(tmp_path):
    arr = np.full((40, 40), 10.0)
    arr[10:20, 10:20] = 9.0
    res = analyze_array(arr, from_origin(500000, 3350000, 1, 1), "EPSG:32614", res=12,
                        drains_xy=np.array([[500015.0, 3349985.0]]), buildings=[])
    meta = read_result(res.save(tmp_path / "out")).meta
    text = (ROOT / "docs" / "outputs.md").read_text(encoding="utf-8")
    # `params` is documented field by field; tiled runs add `tiling`, `build` adds `build`, and
    # a susceptibility table adds `susceptibility`
    fields = ((set(meta) - {"params"}) | {f"params.{k}" for k in meta["params"]}
              | {"tiling", "build", "susceptibility"})
    for field in fields:
        assert f"`{field}`" in text, f"meta field {field} is not described in outputs.md"


def test_package_metadata_is_consistent():
    import h3lowpoints

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    name = re.search(r'authors = \[\{ name = "([^"]+)" \}\]', pyproject).group(1)
    assert h3lowpoints.__author__ == name
    assert f"Copyright 2026 {name}" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    given, family = name.split(" ", 1)
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert f'family-names: "{family}"' in citation and f'given-names: "{given}"' in citation
