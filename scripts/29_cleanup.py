from pathlib import Path

p = Path("src/h3lowpoints/sources/dem.py")
s = p.read_text(encoding="utf-8")
first = s.find("def crop_mosaic(")
second = s.find("def crop_mosaic(", first + 1)
if first != -1 and second != -1:
    p.write_text(s[:first] + s[second:], encoding="utf-8")
    print("removed duplicate crop_mosaic")

Path("tests/test_import.py").write_text(
    'import importlib\n\n\ndef test_import():\n    assert importlib.import_module("h3lowpoints")\n',
    encoding="utf-8")

p = Path("pyproject.toml")
s = p.read_text(encoding="utf-8")
if "[tool.ruff.lint]" not in s:
    s = s.replace("[tool.ruff]\nline-length = 100\n",
                  '[tool.ruff]\nline-length = 100\n\n[tool.ruff.lint]\nselect = ["E4", "E7", "E9", "F"]\n')
    p.write_text(s, encoding="utf-8")
    print("pinned ruff rules")
