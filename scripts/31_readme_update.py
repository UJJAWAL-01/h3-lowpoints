from pathlib import Path

p = Path("README.md")
s = p.read_text(encoding="utf-8")
add = (
    "Robustness check (building confound): flood tickets and lidar artifacts could both concentrate\n"
    "at buildings, so we repeated the test using only depressions with under 10% of their area inside\n"
    "a building footprint and only tickets within 10 m of a building. Flood tickets were 2.3x to 6.4x\n"
    "as likely as other 311 tickets to be within 10 m of such a depression (about 3.7x pooled). The\n"
    "criterion was fixed before the run; the north area passed narrowly (interval lower bound about\n"
    "equal to the control rate). Footprints are City of Austin data of mixed vintage (imagery 2012 to 2017).\n\n"
)
edits = [
    ("Limits:\n", add + "Limits:\n"),
    ("- Most flood tickets are *not* near",
     "- Depressions mostly under building footprints are likely lidar artifacts (about 7 to 17% of significant "
     "depressions in the three areas). Pass `buildings=` to flag them.\n- Most flood tickets are *not* near"),
    ("to add drain context.",
     "to add drain context. Pass `buildings=` (footprints in the same CRS) to add `building_overlap` "
     "and `likely_building_artifact`."),
]
for old, new in edits:
    assert s.count(old) == 1, "README pattern not found exactly once: " + old
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("README updated")
