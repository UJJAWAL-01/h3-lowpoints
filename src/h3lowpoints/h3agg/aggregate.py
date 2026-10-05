import h3
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer


def pixels_to_h3(values, transform, crs, res=11, step=1):
    """Assign every `step`-th pixel with a valid (non-NaN) value to an H3 cell."""
    rows = np.arange(0, values.shape[0], step)
    cols = np.arange(0, values.shape[1], step)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    rr, cc = rr.ravel(), cc.ravel()
    v = values[rr, cc]
    ok = ~np.isnan(v)
    rr, cc, v = rr[ok], cc[ok], v[ok]
    xs, ys = rasterio.transform.xy(transform, rr, cc)
    to_ll = Transformer.from_crs(crs, 4326, always_xy=True)
    lon, lat = to_ll.transform(np.asarray(xs), np.asarray(ys))
    cells = [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)]
    return pd.DataFrame({"h3": cells, "v": v})


def build_cell_table(dem_path, depth, transform, crs, res=11,
                     min_depth=0.15, flat_relief=0.3, terrain_step=4):
    with rasterio.open(dem_path) as src:
        elev = src.read(1).astype("float64")
        if src.nodata is not None:
            elev[elev == src.nodata] = np.nan
    cell_area = abs(transform.a * transform.e)

    terr = pixels_to_h3(elev, transform, crs, res, step=terrain_step)
    terr = terr.groupby("h3")["v"].agg(
        mean_elev="mean", relief=lambda x: x.max() - x.min()
    ).reset_index()
    terr["flat"] = terr["relief"] < flat_relief

    d = np.where(depth >= min_depth, depth, np.nan)
    dep = pixels_to_h3(d, transform, crs, res, step=1)
    dep = dep.groupby("h3")["v"].agg(
        max_depth="max", n_pix="count", depth_sum="sum"
    ).reset_index()
    dep["depress_area_m2"] = dep["n_pix"] * cell_area
    dep["depress_volume_m3"] = dep["depth_sum"] * cell_area
    dep = dep.drop(columns=["n_pix", "depth_sum"])

    cells = terr.merge(dep, on="h3", how="outer")
    for col in ("max_depth", "depress_area_m2", "depress_volume_m3"):
        cells[col] = cells[col].fillna(0.0)
    cells["flat"] = cells["flat"].fillna(False).astype(bool)
    return cells


def depression_cells(depth, labels, code_by_label, transform, crs, res=11):
    """One row per H3 cell that contains depression pixels."""
    rr, cc = np.nonzero(labels > 0)
    if len(rr) == 0:
        return pd.DataFrame(columns=["h3", "max_depth_m", "code", "depress_area_m2", "depress_volume_m3"])
    xs, ys = rasterio.transform.xy(transform, rr, cc)
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(np.asarray(xs), np.asarray(ys))
    cells = [h3.latlng_to_cell(la, lo, res) for la, lo in zip(lat, lon)]
    df = pd.DataFrame({"h3": cells, "depth": depth[rr, cc], "code": code_by_label[labels[rr, cc]]})
    cell_area = abs(transform.a * transform.e)
    out = df.groupby("h3").agg(max_depth_m=("depth", "max"), n_pix=("depth", "size"),
                               depth_sum=("depth", "sum"), code=("code", "max")).reset_index()
    out["depress_area_m2"] = out["n_pix"] * cell_area
    out["depress_volume_m3"] = out["depth_sum"] * cell_area
    return out.drop(columns=["n_pix", "depth_sum"])
