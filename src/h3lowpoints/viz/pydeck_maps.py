import numpy as np
import pandas as pd
import pydeck as pdk


def ramp(series, vmin, vmax, c0, c1):
    t = np.clip((series - vmin) / (vmax - vmin + 1e-9), 0, 1).to_numpy()
    out = pd.DataFrame(index=series.index)
    for name, i in (("r", 0), ("g", 1), ("b", 2)):
        out[name] = (c0[i] + t * (c1[i] - c0[i])).astype(int)
    return out


def make_map(df, path, elev_col=None, elev_scale=1.0, hover_cols=(),
             center=(30.305, -97.74), zoom=13):
    cols = ["h3", "r", "g", "b"] + ([elev_col] if elev_col else []) + list(hover_cols)
    cols = list(dict.fromkeys(cols))
    data = df[cols].copy()
    num = data.select_dtypes("number").columns
    data[num] = data[num].fillna(0).round(2)  # NaN would break the browser's JSON parser
    layer = pdk.Layer(
        "H3HexagonLayer",
        data,
        get_hexagon="h3",
        get_fill_color="[r, g, b, 210]",
        extruded=elev_col is not None,
        get_elevation=elev_col if elev_col else 0,
        elevation_scale=elev_scale,
        pickable=True,
        auto_highlight=True,
    )
    view = pdk.ViewState(latitude=center[0], longitude=center[1], zoom=zoom, pitch=45)
    pdk.Deck(layers=[layer], initial_view_state=view, map_style="light").to_html(
        path, open_browser=True
    )
