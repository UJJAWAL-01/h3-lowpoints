"""Storms that last: Green-Ampt, the time-stepping model, storms of any duration and their rarity,
NOAA's temporal patterns, dry and wet ground, and soil parameters from the soil survey.

Oracles: Green-Ampt against its closed form under ponding; a fill time worked by hand; SCS runoff
in steps summing to the SCS total; water conserved in every depression and overall on random
spill forests with every loss switched on; the model with no losses and uniform rain giving each
depression exactly the water of the susceptibility index; NRCS's own worked example of ARC I and
III; return periods exact at NOAA's published depths. Planted bugs must be caught.
"""
import numpy as np
import pandas as pd
import pytest

from h3lowpoints.runoff import ARC_TABLE, SUCTION_MM, adjust_curve_number, runoff_mm, texture_class
from h3lowpoints.sources import rainfall, soils
from h3lowpoints.storms import STORM_COLUMNS, green_ampt_step, pattern_steps, simulate, storm_response
from h3lowpoints.susceptibility import susceptibility
from test_susceptibility import result  # noqa: F401  (module fixture: a result with catchments)


# ------------------------------------------------------------------------------ Green-Ampt

@pytest.mark.parametrize("ks,sd", [(10.0, 50.0), (0.5, 300.0), (100.0, 5.0)])
def test_green_ampt_matches_its_closed_form_in_one_step_or_many(ks, sd):
    for F in (0.5, 10.0, 100.0, 800.0):
        t = (F - sd * np.log1p(F / sd)) / ks            # time to take in F mm, ponded from the start
        assert green_ampt_step(0.0, ks, sd, t) == pytest.approx(F, rel=1e-9)
        x = 0.0
        for _ in range(500):
            x = green_ampt_step(x, ks, sd, t / 500)
        assert x == pytest.approx(F, rel=1e-9)


def test_green_ampt_limits():
    assert green_ampt_step(3.0, 7.0, 0.0, 2.0) == pytest.approx(17.0)       # no suction: ks t
    assert green_ampt_step(3.0, 0.0, 50.0, 2.0) == 3.0                      # impermeable
    assert green_ampt_step(0.0, 1.0, 100.0, 0.01) > 0.01 * 1.0               # faster than ks early on


# ------------------------------------------------------------------------- the time steps

def test_fill_time_and_overflow_worked_by_hand():
    # all rain runs off (CN 100): 100 mm in 10 h on 1 ha is 100 m3/h; 250 m3 fill in 2.5 h
    cum = pattern_steps([0, 1], [0, 1], 120)
    out, leaving = simulate([-1], 100.0, cum, catch_area=1e4, volume=250.0, area=500.0, max_depth=1.0,
                            dt=300.0, curve_number=100.0)
    assert out["time_to_fill_h"][0] == pytest.approx(2.5)
    assert out["overflow_m3"][0] == pytest.approx(750.0)
    assert leaving == pytest.approx(750.0)
    assert out["peak_fill"][0] == pytest.approx(1.0) and out["peak_depth_m"][0] == pytest.approx(1.0)
    assert out["ponding_hours"][0] == np.inf                    # nothing lets the water out


def test_curve_number_in_steps_sums_to_the_scs_total():
    rng = np.random.default_rng(0)
    for _ in range(5):
        times = np.r_[0, np.sort(rng.uniform(0, 1, 6)), 1]
        cum = np.r_[0, np.sort(rng.uniform(0, 1, 6)), 1]
        p, cn = rng.uniform(10, 400), rng.uniform(40, 98)
        out, _ = simulate([-1], p, pattern_steps(times, cum, 97), catch_area=1e4, volume=1e9, area=1e6,
                          max_depth=1e3, dt=300.0, curve_number=cn)
        assert out["own_m3"][0] == pytest.approx(runoff_mm(p, cn) / 1000 * 1e4, rel=1e-9)


def forest(seed, n=300):
    rng = np.random.default_rng(seed)
    target = np.full(n, -1)
    for i in range(n - 1):
        if rng.random() < 0.7:
            target[i] = rng.integers(i + 1, n)
    return rng, target


@pytest.mark.parametrize("method", ["curve-number", "green-ampt"])
@pytest.mark.parametrize("seed", range(3))
def test_water_is_conserved_with_every_loss(method, seed):
    rng, target = forest(seed)
    n = len(target)
    kw = ({"curve_number": rng.uniform(50, 98, n)} if method == "curve-number" else
          {"impervious": rng.uniform(0, 1, n), "ksat_mm_h": rng.uniform(0.3, 60, n),
           "suction_deficit_mm": rng.uniform(0, 150, n)})
    out, leaving = simulate(target, rng.uniform(20, 300, n), pattern_steps([0, .4, 1], [0, .8, 1], 72),
                            catch_area=rng.uniform(1e3, 1e6, n), volume=rng.uniform(10, 1e4, n),
                            area=rng.uniform(50, 1e4, n), max_depth=rng.uniform(.1, 3, n), dt=300.0,
                            n_after=300, method=method, seepage_mm_h=rng.uniform(0, 10, n),
                            drain_mm_h=rng.uniform(0, 20, n), **kw)
    each = (out["inflow_m3"] - out["seepage_m3"] - out["drained_m3"] - out["overflow_m3"]
            - out["water_left_m3"])
    assert np.abs(each).max() <= 1e-9 * out["inflow_m3"].max()
    kept = (out["seepage_m3"] + out["drained_m3"] + out["water_left_m3"]).sum()
    assert out["own_m3"].sum() == pytest.approx(kept + leaving, rel=1e-12)
    assert (out["peak_fill"] <= 1 + 1e-12).all() and (out["water_left_m3"] <= out["peak_volume_m3"] + 1e-9).all()


def test_overflow_reaches_the_depression_downstream_and_unknown_spreads():
    cum = pattern_steps([0, 1], [0, 1], 12)
    kw = dict(catch_area=[1e4, 1e4, 1e4], volume=[10.0, 1e6, 1e6], area=[100.0] * 3, max_depth=[1.0] * 3,
              dt=300.0)
    out, _ = simulate([1, -1, -1], 50.0, cum, curve_number=100.0, **kw)
    assert out["inflow_m3"][1] == pytest.approx(500.0 + 490.0)       # its own plus the overflow
    assert out["inflow_m3"][2] == pytest.approx(500.0)
    out, _ = simulate([1, -1, -1], 50.0, cum, curve_number=[np.nan, 100.0, 100.0], **kw)
    assert np.isnan(out["inflow_m3"][:2]).all() and out["inflow_m3"][2] == pytest.approx(500.0)
    # a planted bug: passing on all the water, not only the overflow, must change the answer
    wrong, _ = simulate([1, -1, -1], 50.0, cum, curve_number=100.0, **{**kw, "volume": [0.0, 1e6, 1e6]})
    assert np.isnan(wrong["inflow_m3"][:2]).all()                    # an empty bowl is unknown


def test_seepage_and_drains_empty_a_bowl_at_their_rates():
    # walls straight up (k = 1): wet area stays 100 m2; seeping at 36 mm/h = 3.6 m3/h
    cum = pattern_steps([0, 1], [0, 1], 1)
    out, _ = simulate([-1], 36.0, cum, catch_area=1e4, volume=1e3, area=100.0, max_depth=10.0, dt=3600.0,
                      n_after=200, curve_number=100.0, seepage_mm_h=36.0, ponding_depth_m=1.0)
    # 360 m3 arrive in hour 1 and 3.6 m3 seep each hour: 360/3.6 = 100 h; deeper than 1 m (100 m3)
    # until 360 - 3.6 t < 100, about 72.2 h
    assert out["seepage_m3"][0] == pytest.approx(360.0)
    assert 72.0 <= out["ponding_hours"][0] <= 74.0
    out, _ = simulate([-1], 36.0, cum, catch_area=1e4, volume=1e3, area=100.0, max_depth=10.0, dt=3600.0,
                      n_after=10, curve_number=100.0, drain_mm_h=3.6)      # 36 m3/h from 1 ha
    assert out["drained_m3"][0] == pytest.approx(360.0) and out["water_left_m3"][0] == pytest.approx(0.0)


def test_intense_rain_runs_off_more_with_green_ampt_but_not_with_curve_numbers():
    soil = {"impervious": 0.2, "ksat_mm_h": 5.0, "suction_deficit_mm": 30.0}
    base = dict(catch_area=1e4, volume=1e9, area=1e6, max_depth=1e3)
    totals = {}
    for hours in (1, 6, 24):
        n_steps = int(hours * 12)
        cum = pattern_steps([0, 1], [0, 1], n_steps)
        b, _ = simulate([-1], 100.0, cum, dt=300.0, method="green-ampt", **soil, **base)
        a, _ = simulate([-1], 100.0, cum, dt=300.0, curve_number=80.0, **base)
        totals[hours] = (b["own_m3"][0], a["own_m3"][0])
    assert totals[1][0] > totals[6][0] > totals[24][0]
    assert totals[1][1] == pytest.approx(totals[24][1])


def test_rain_curves_are_checked():
    with pytest.raises(ValueError, match="rain curve"):
        pattern_steps([0, 1], [0, 0.5], 10)
    with pytest.raises(ValueError, match="rain curve"):
        pattern_steps([0, .5, 1], [0, .8, .6], 10)
    assert pattern_steps([0, .5, 1], [0, .8, 1], 4).tolist() == pytest.approx([0, .4, .8, .9, 1])


# ----------------------------------------------------------------- dry and wet, soil names

def test_arc_table_and_nrcs_worked_example():
    assert len(ARC_TABLE) == 77
    # NEH 630 ch. 10, example 10-2: CN 74 is 55 for ARC I and 88 for ARC III
    assert adjust_curve_number(74, "dry") == 55 and adjust_curve_number(74, "wet") == 88
    assert adjust_curve_number(74.5, "dry") == pytest.approx(56.0)      # between 55 (74) and 57 (75)
    assert adjust_curve_number(80, "average") == 80
    assert np.isnan(adjust_curve_number([np.nan], "wet")).all()
    assert (np.diff(ARC_TABLE[:, 1]) >= 0).all() and (np.diff(ARC_TABLE[:, 2]) >= 0).all()
    with pytest.raises(ValueError):
        adjust_curve_number(80, "III")


def test_texture_names():
    assert texture_class("Fine sandy loam") == "sandy loam"
    assert texture_class("Loamy very fine sand") == "loamy sand"
    assert texture_class("Coarse sand") == "sand"
    assert texture_class("Silty clay loam") == "silty clay loam"
    assert texture_class("Muck") is None and texture_class(None) is None
    assert SUCTION_MM["clay"] == 316.3 and SUCTION_MM["sand"] == 49.5      # Rawls et al. 1983


# ---------------------------------------------------------------- rarity of any storm

def test_return_period_exact_at_published_depths_and_bounded():
    row = np.array([50, 60, 75, 90, 110, 125, 140, 160, 185, 205], float)
    years = np.asarray(rainfall.ATLAS14_YEARS, float)
    assert rainfall.years_of_depth(row, np.tile(row, (10, 1))) == pytest.approx(years)
    mid = rainfall.years_of_depth(np.array([100.0]), row[None, :])[0]
    assert mid == pytest.approx(np.sqrt(10 * 25))                      # halfway in log years
    assert rainfall.years_of_depth(np.array([40.0, 300.0, np.nan]), np.tile(row, (3, 1))).tolist()[:2] == [0, np.inf]
    assert np.isnan(rainfall.years_of_depth(np.array([100.0]), np.full((1, 10), np.nan)))[0]


def test_any_duration_between_published_ones():
    short, long, w = rainfall.Atlas14Storms.bracket(4.0)
    assert (short, long) == ("03h", "06h") and w == pytest.approx(np.log(4 / 3) / np.log(2))
    assert rainfall.Atlas14Storms.bracket(1.0) == ("60m", "60m", 1.0)
    with pytest.raises(ValueError, match="5 minutes to 60 days"):
        rainfall.Atlas14Storms.bracket(2000.0)

    class Grid:                                   # depth grows with the square root of hours
        def __init__(self, hours):
            self.hours = hours

        def depth(self, years, lat, lon):
            return np.full(np.shape(lat), 10.0 * np.sqrt(self.hours) * years ** 0.2)
    storms = rainfall.Atlas14Storms()
    storms.sets = {d: Grid(rainfall.duration_hours(d)) for d in rainfall.ATLAS14_DURATIONS}
    lat, lon = np.array([30.0]), np.array([-97.0])
    assert storms.depth(100, 4.0, lat, lon)[0] == pytest.approx(10 * 2 * 100 ** 0.2)   # exact for a power law
    d = storms.depth(25, 5.0, lat, lon)
    assert storms.return_period(d, 5.0, lat, lon)[0] == pytest.approx(25)


NEW_LAYOUT = """Tabulated temporal distribution data for NOAA Atlas 14 Volume 11: Texas Region  1
This is a comma delimited file containing temporal distribution data for all four quartile cases.

{blocks}"""
NEW_BLOCK = """CUMULATIVE PERCENTAGES OF TOTAL PRECIPITATION FOR {name} CASES
Time,Percent of occurrence
hours,90%,80%,70%,60%,50%,40%,30%,20%,10%
0,0,0,0,0,0,0,0,0,0
3.0,10,20,30,40,50,60,70,80,90
6.0,100,100,100,100,100,100,100,100,100

"""
OLD_BLOCK = """{name},

percent of duration,0,50,100
10%,0,90,100
20%,0,80,100
30%,0,70,100
40%,0,60,100
50%,0,50.02,100
60%,0,40,100
70%,0,30,100
80%,0,20,100
90%,0,10,100

"""


def test_temporal_files_in_both_layouts():
    new = NEW_LAYOUT.format(blocks="".join(NEW_BLOCK.format(name=n) for n in
                                           ("FIRST-QUARTILE", "SECOND-QUARTILE", "THIRD-QUARTILE",
                                            "FOURTH-QUARTILE", "ALL")))
    old = "Tabulated Temporal Distribution Data,\n\n" + "".join(
        OLD_BLOCK.format(name=n) for n in ("First Quartile", "Second Quartile", "Third Quartile",
                                           "Fourth Quartile", "All Cases"))
    for text in (new, old):
        t = rainfall.parse_temporal(text)
        assert set(t) == set(rainfall.TEMPORAL_CASES)
        times, curves = t["all"]
        assert times.tolist() == [0, .5, 1]
        assert curves[50][1] == pytest.approx(0.5, abs=1e-3) and curves[10][1] == pytest.approx(0.9)
    # rounding that falls back is held level
    t = rainfall.parse_temporal(old.replace("50%,0,50.02,100", "50%,0,100.02,100"))
    assert (np.diff(t["all"][1][50]) >= 0).all()
    with pytest.raises(ValueError, match="cases"):
        rainfall.parse_temporal(new.split("CUMULATIVE PERCENTAGES OF TOTAL PRECIPITATION FOR ALL")[0])
    assert rainfall.temporal_url("ca_3", 24).endswith("/sw/ca_3_24h_temporal.csv")
    assert rainfall.temporal_url("tx_1", 6).endswith("/tx/tx_1_6h_temporal.csv")
    assert [rainfall.nearest_temporal_hours(h) for h in (1, 8, 9, 30, 200)] == [6, 6, 12, 24, 96]


# ------------------------------------------------------------------------ soil survey

def horizon(mukey, cokey, pct, top, bottom, ksat, texcl="Loam", sat=40, fc=25, wp=10, name="A"):
    return {"mukey": str(mukey), "cokey": str(cokey), "compname": name, "comppct_r": str(pct),
            "hzdept_r": str(top), "hzdepb_r": str(bottom), "ksat_r": None if ksat is None else str(ksat),
            "wsatiated_r": str(sat), "wthirdbar_r": str(fc), "wfifteenbar_r": str(wp), "texcl": texcl}


def test_infiltration_parameters_from_horizons():
    rows = [horizon(1, 10, 60, 0, 20, 9.0), horizon(1, 10, 60, 20, 45, 2.0), horizon(1, 10, 60, 45, 120, 0.1),
            horizon(1, 11, 40, 0, 50, 30.0, "Sand"),
            # the largest component has no texture: the next one is used
            horizon(2, 20, 70, 0, 30, 5.0, None), horizon(2, 21, 30, 0, 30, 4.0, "Fine sandy loam"),
            # no conductivity in the top 50 cm and no other component: left out
            horizon(3, 30, 100, 0, 30, None)]
    t = soils.infiltration_parameters(rows)
    assert t["mukey"].tolist() == [1, 2]
    one = t.iloc[0]
    assert one["ksat_mm_h"] == pytest.approx(0.1 * 3.6)          # least of those starting above 50 cm
    assert one["suction_mm"] == SUCTION_MM["loam"] and one["theta_fc"] == pytest.approx(0.25)
    assert t.iloc[1]["texture"] == "sandy loam" and t.iloc[1]["ksat_mm_h"] == pytest.approx(4.0 * 3.6)
    assert soils.infiltration_parameters([]).empty


def test_infiltration_table_asks_each_map_unit_once(tmp_path):
    calls = []

    def post(body):
        import json
        sql = json.loads(body)["query"]
        keys = sql.rsplit("(", 1)[1].rstrip(")").split(",")
        calls.append(keys)
        rows = [list(horizon(k, int(k) * 10, 100, 0, 60, 5.0).values()) for k in keys if k != "7"]
        names = list(horizon(0, 0, 0, 0, 0, 0).keys())
        return json.dumps({"Table": [names] + rows}).encode()
    t = soils.infiltration_table([5, 6, 7, 5], tmp_path, post=post)
    assert t["mukey"].tolist() == [5, 6] and len(calls) == 1
    t = soils.infiltration_table([6, 7, 8], tmp_path, post=post)          # only 8 is new
    assert calls[-1] == ["8"] and t["mukey"].tolist() == [5, 6, 8]


# ------------------------------------------------------------------------ end to end

def test_without_losses_it_gives_exactly_the_water_of_the_index(result):  # noqa: F811
    t = storm_response(result, depth_mm=120.0, hours=24, curve_number=80.0, pattern="uniform",
                       rarity=False)
    s = susceptibility(result, curve_number=80.0, rain=120.0)
    m = t.merge(s, on="id")
    deps = result.depressions.set_index("id")["volume_m3"]
    ratio = m["inflow_m3"] / deps.loc[m["id"]].to_numpy()
    assert np.allclose(ratio, m["fill_ratio"], rtol=1e-9)
    assert list(t.columns[:len(STORM_COLUMNS)]) == STORM_COLUMNS
    assert (t["time_to_fill_h"].notna() == (m["fill_ratio"] >= 1 - 1e-9)).all()


def test_green_ampt_end_to_end_and_paving(result):  # noqa: F811
    cells = result.catchments["h3"].unique()
    paved = pd.DataFrame({"h3": cells, "impervious_pct": 100.0})
    soil = {"ksat": 10.0, "suction": 100.0, "deficit_average": 0.3, "deficit_dry": 0.4}
    t = storm_response(result, depth_mm=80.0, hours=3, method="green-ampt", impervious=paved, soil=soil,
                       pattern="uniform", seepage=False, rarity=False)
    assert np.allclose(t["runoff_mm"], 80.0)                       # all paved: all rain runs off
    open_ground = paved.assign(impervious_pct=0.0)
    runs = {c: storm_response(result, depth_mm=80.0, hours=3, method="green-ampt", impervious=open_ground,
                              soil=soil, condition=c, pattern="uniform", rarity=False)
            for c in ("dry", "average", "wet")}
    dry, avg, wet = (runs[c]["runoff_mm"].mean() for c in ("dry", "average", "wet"))
    assert dry < avg < wet
    assert wet == pytest.approx(80.0 - 10.0 * 3)                   # saturated: takes ks x hours
    assert (runs["average"]["seepage_m3"] >= 0).all()


def test_given_pattern_and_drains(result, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rainfall.Atlas14Storms, "depth",
                        lambda self, years, hours, lat, lon: np.full(np.shape(np.atleast_1d(lat)), 40.0))
    cells = result.catchments["h3"].unique()
    imp = pd.DataFrame({"h3": cells, "impervious_pct": 50.0})
    front = ([0, 0.25, 1], [0, 0.75, 1])
    a = storm_response(result, depth_mm=100.0, hours=6, curve_number=85.0, pattern=front, rarity=False)
    b = storm_response(result, depth_mm=100.0, hours=6, curve_number=85.0, pattern="uniform", rarity=False)
    assert np.allclose(a["runoff_mm"], b["runoff_mm"])            # totals do not depend on timing
    filled = a["time_to_fill_h"].notna() & b["time_to_fill_h"].notna()
    assert (a.loc[filled, "time_to_fill_h"] <= b.loc[filled, "time_to_fill_h"] + 1e-9).all()
    d = storm_response(result, depth_mm=100.0, hours=6, curve_number=85.0, pattern="uniform",
                       impervious=imp, drains={"years": 10, "hours": 1}, rarity=False)
    assert np.allclose(d["drain_mm_h"], 40.0) and d["drained_m3"].sum() > 0
    assert d["peak_fill"].sum() < b["peak_fill"].sum()
    none = storm_response(result, depth_mm=100.0, hours=6, curve_number=85.0, pattern="uniform",
                          impervious=imp.assign(impervious_pct=5.0), drains={"years": 10, "hours": 1},
                          rarity=False)
    assert (none["drained_m3"] == 0).all()                       # below 20% paved: not drained


def test_bad_requests(result):  # noqa: F811
    with pytest.raises(ValueError, match="either years"):
        storm_response(result, hours=6, curve_number=80.0)
    with pytest.raises(ValueError, match="green-ampt needs"):
        storm_response(result, depth_mm=50.0, hours=6, method="green-ampt")
    with pytest.raises(ValueError, match="seepage needs soil"):
        storm_response(result, depth_mm=50.0, hours=6, curve_number=80.0, seepage=True)
    with pytest.raises(ValueError, match="pattern"):
        storm_response(result, depth_mm=50.0, hours=6, curve_number=80.0, pattern="scs", rarity=False)


def test_cli_storm_writes_a_table_and_the_folder_still_verifies(result, tmp_path, monkeypatch):  # noqa: F811
    from h3lowpoints.cli import main
    from h3lowpoints.store import read_meta
    from h3lowpoints.verify import check_saved

    monkeypatch.setattr(rainfall.Atlas14Storms, "return_period",
                        lambda self, depth, hours, lat, lon: np.full(np.shape(np.atleast_1d(lat)), 7.0))
    folder = result.save(tmp_path / "r")
    assert main(["storm", str(folder), "--hours", "6", "--depth", "90", "--curve-number", "85",
                 "--pattern", "uniform"]) == 0
    t = pd.read_csv(folder / "storm_90mm_6h.csv")
    assert list(t.columns[:len(STORM_COLUMNS)]) == STORM_COLUMNS and (t["storm_years"] == 7).all()
    assert read_meta(folder)["storms"]["90mm_6h"]["hours"] == 6
    assert check_saved(folder) == []
    with pytest.raises(SystemExit, match="--years or --depth"):
        main(["storm", str(folder), "--hours", "6", "--curve-number", "85"])
    with pytest.raises(SystemExit, match="missing"):
        main(["storm", str(folder), "--hours", "6", "--depth", "9", "--method", "green-ampt",
              "--soil", str(tmp_path / "nothing"), "--impervious", "x.tif"])


@pytest.mark.parametrize("how", ["leak", "overfull", "twice", "stray", "missing"])
def test_damaged_storm_tables_are_caught(result, tmp_path, how):  # noqa: F811
    from h3lowpoints.storms import write_storm
    from h3lowpoints.verify import check_saved

    folder = result.save(tmp_path / "r")
    t = storm_response(result, depth_mm=150.0, hours=3, curve_number=90.0, pattern="uniform", rarity=False)
    write_storm(folder, t, "s")
    assert check_saved(folder) == []
    known = t.index[t["inflow_m3"].notna()]
    if how == "leak":
        t.loc[known[0], "seepage_m3"] += 5.0
    elif how == "overfull":
        t.loc[known[0], "peak_fill"] = 1.5
    elif how == "twice":
        t = pd.concat([t, t.iloc[:1]])
    elif how == "stray":
        t.loc[known[0], "id"] = 10 ** 9
    if how == "missing":
        (folder / "storm_s.csv").unlink()
    else:
        write_storm(folder, t, "s")
    assert check_saved(folder) != []


def test_clip_keeps_storm_rows_of_kept_depressions(result, tmp_path):  # noqa: F811
    import shapely
    from h3lowpoints.clip import clip_result
    from h3lowpoints.store import read_meta, read_table
    from h3lowpoints.storms import write_storm
    from h3lowpoints.verify import check_saved

    folder = result.save(tmp_path / "r")
    write_storm(folder, storm_response(result, depth_mm=150.0, hours=3, curve_number=90.0, pattern="uniform",
                                       rarity=False), "s")
    d = result.depressions
    half = shapely.box(d["lon"].min() - 1, d["lat"].min() - 1, d["lon"].median(), d["lat"].max() + 1)
    out = clip_result(folder, half, tmp_path / "c")
    kept = read_table(out, "depressions")["id"]
    assert set(read_table(out, "storm_s")["id"]) == set(kept)
    assert read_meta(out)["storms"]["s"]["clipped_after"] is True
    assert check_saved(out) == []
