import json
from h3lowpoints.sources.dem import find_tiles

BBOX = (-97.76, 30.29, -97.72, 30.32)  # downtown Austin pilot: lon/lat
items = find_tiles(BBOX)
print(len(items), "tiles found")
total = 0
for it in items:
    mb = round(it.get("sizeInBytes", 0) / 1e6)
    total += mb
    print(it["title"], "|", it.get("publicationDate"), "|", mb, "MB")
print("total approx", total, "MB")
json.dump(items, open("data_tiles.json", "w"), indent=2)
