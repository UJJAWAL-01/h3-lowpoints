"""3D maps of H3 cells. Needs the `viz` extra (pydeck); the GeoJSON export needs nothing."""
import json

import h3
import numpy as np

# Light to dark blue: deeper depressions read darker. Colour-blind safe and prints in greyscale.
RAMP = ((198, 219, 239), (8, 48, 107))


def _import_pydeck():
    try:
        import pydeck as pdk
    except ImportError as exc:
        raise ImportError("3D maps need pydeck: pip install 'h3-lowpoints[viz]'") from exc
    return pdk


def colour_ramp(values, vmin=None, vmax=None, ramp=RAMP):
    """RGB (n, 3) ints for `values`, linearly from ramp[0] at vmin to ramp[1] at vmax."""
    v = np.asarray(values, dtype="float64")
    vmin = np.nanmin(v) if vmin is None else vmin
    vmax = np.nanmax(v) if vmax is None else vmax
    t = np.clip((v - vmin) / (vmax - vmin + 1e-12), 0, 1)[:, None]
    lo, hi = np.array(ramp[0]), np.array(ramp[1])
    return (lo + t * (hi - lo)).astype(int)


def cells_deck(cells, value="max_depth_m", elevation_scale=20.0, hover=(), pitch=45):
    """A pydeck Deck of extruded H3 hexagons: height and colour both follow `value`.

    `cells` is the `cells` table of a Result (or a roll-up of it). The view is centred on the
    data. Call `.to_html(path)` on the result to save a standalone page.
    """
    pdk = _import_pydeck()
    if len(cells) == 0:
        raise ValueError("There are no cells to draw")
    data = cells[["h3", value, *[c for c in hover if c not in ("h3", value)]]].copy()
    num = data.select_dtypes("number").columns
    data[num] = data[num].fillna(0).round(3)  # NaN would break the browser's JSON parser
    data[["r", "g", "b"]] = colour_ramp(data[value])
    centre = np.array([h3.cell_to_latlng(c) for c in data["h3"]]).mean(axis=0)
    layer = pdk.Layer(
        "H3HexagonLayer", data, get_hexagon="h3", get_fill_color="[r, g, b, 220]",
        extruded=True, get_elevation=value, elevation_scale=elevation_scale,
        pickable=True, auto_highlight=True)
    view = pdk.ViewState(latitude=float(centre[0]), longitude=float(centre[1]), zoom=13,
                         pitch=pitch)
    return pdk.Deck(layers=[layer], initial_view_state=view, map_style="light",
                    tooltip={"text": f"{{h3}}\n{value}: {{{value}}}"})


def cells_to_geojson(cells, path=None, properties=None):
    """GeoJSON FeatureCollection of cell outlines (for deck.gl, kepler.gl, QGIS, ...).

    `properties` lists the columns to include; by default all except `h3`, which becomes the
    feature id. Returns the dict, and writes it to `path` when given.
    """
    props = [c for c in (properties if properties is not None else cells.columns) if c != "h3"]
    features = []
    for row in cells.itertuples(index=False):
        ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(row.h3)]
        ring.append(ring[0])
        features.append({
            "type": "Feature", "id": row.h3,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {c: _json_safe(getattr(row, c)) for c in props}})
    out = {"type": "FeatureCollection", "features": features}
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
    return out


def _json_safe(v):
    if isinstance(v, np.generic):
        v = v.item()
    return None if isinstance(v, float) and v != v else v
