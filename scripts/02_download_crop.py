import json

from h3lowpoints.sources.dem import crop_mosaic, download, find_tiles

BBOX = (-97.76, 30.29, -97.72, 30.32)
items = find_tiles(BBOX)
paths = download(items, "data/raw")
crop_mosaic(paths, "data/pilot_dem.tif", BBOX)

# provenance record: where this DEM came from
prov = [
    {
        "title": i["title"],
        "published": i.get("publicationDate"),
        "url": i["downloadURL"],
    }
    for i in items
]
json.dump({"bbox_lonlat": BBOX, "tiles": prov}, open("data/pilot_dem_provenance.json", "w"), indent=2)
print("done")
