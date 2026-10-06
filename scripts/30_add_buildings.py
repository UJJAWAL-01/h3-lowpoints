from pathlib import Path

p = Path("src/h3lowpoints/pipeline.py")
s = p.read_text(encoding="utf-8")
pairs = [
    ("from .classify import CODES, classify, serving_drains, touches_edge\n",
     "from .buildings import building_overlap\nfrom .classify import CODES, classify, serving_drains, touches_edge\n"),
    ('"dist_to_drain_m", "drain_at_core", "significant", "suspect_pit", "edge", "cls"]',
     '"dist_to_drain_m", "drain_at_core", "significant", "suspect_pit", "edge", "cls",\n'
     '            "building_overlap", "likely_building_artifact"]'),
    ("sig_depth=0.3, sig_area=150.0, pit_depth=1.5, pit_area=150.0):",
     "sig_depth=0.3, sig_area=150.0, pit_depth=1.5, pit_area=150.0, buildings=None):"),
    ('"suspect_pit": pit, "edge": edge, "cls": cls})',
     '"suspect_pit": pit, "edge": edge, "cls": cls})\n'
     '    if buildings is not None:\n'
     '        deps["building_overlap"] = building_overlap(labels, ids, buildings, transform, arr.shape)\n'
     '    else:\n'
     '        deps["building_overlap"] = np.nan\n'
     '    deps["likely_building_artifact"] = deps["building_overlap"].fillna(0.0) >= 0.5'),
]
for old, new in pairs:
    assert s.count(old) == 1, "pattern not found exactly once: " + old[:60]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")

p = Path("pyproject.toml")
s = p.read_text(encoding="utf-8")
old = 'dependencies = ["numpy", "scipy", "pandas", "rasterio", "pyproj", "h3>=4", "numba"]'
assert s.count(old) == 1
p.write_text(s.replace(old, old.replace('"numba"]', '"numba", "shapely"]')), encoding="utf-8")
print("patched pipeline.py and pyproject.toml")
