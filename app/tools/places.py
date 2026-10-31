"""The explorer's offline place search: US cities and towns of the lower 48 states and DC.

    python -I app/tools/places.py GAZETTEER_ZIP POPULATION_CSV OUT_JSON

Sources (public domain, US Census Bureau): the 2024 Gazetteer of places (names, internal points)
and the Vintage 2024 population estimates of incorporated places (sub-est2024.csv). Places without
an estimate (census-designated places) keep population 0 and sort after those with one.
Output: [[name, state, lat, lon, population], ...], largest first, coordinates to 4 decimals.
"""
import csv
import io
import json
import sys
import zipfile

EXCLUDED = {"AK", "HI", "PR"}
SUFFIXES = (" city and borough", " consolidated government (balance)", " metro government (balance)",
            " unified government (balance)", " metropolitan government (balance)", " (balance)",
            " city", " town", " village", " borough", " CDP", " municipality", " township",
            " comunidad", " zona urbana")


def clean(name):
    for s in SUFFIXES:
        if name.endswith(s):
            return name[: -len(s)]
    return name


def main(gaz_zip, pop_csv, out):
    pop = {}
    with open(pop_csv, encoding="latin-1", newline="") as f:
        for r in csv.DictReader(f):
            if r["SUMLEV"] == "162":
                pop[r["STATE"] + r["PLACE"]] = int(r["POPESTIMATE2024"] or 0)
    with zipfile.ZipFile(gaz_zip) as z:
        name = next(n for n in z.namelist() if n.endswith(".txt"))
        text = z.read(name).decode("utf-8")
    rows = []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    reader.fieldnames = [h.strip() for h in reader.fieldnames]
    for r in reader:
        if r["USPS"] in EXCLUDED:
            continue
        rows.append([clean(r["NAME"].strip()), r["USPS"], round(float(r["INTPTLAT"]), 4),
                     round(float(r["INTPTLONG"].strip()), 4), pop.get(r["GEOID"], 0)])
    rows.sort(key=lambda x: (-x[4], x[0]))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, separators=(",", ":"), ensure_ascii=False)
    print(f"{len(rows):,} places, {sum(1 for r in rows if r[4]):,} with population, "
          f"largest {rows[0][:2]} {rows[0][4]:,}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
