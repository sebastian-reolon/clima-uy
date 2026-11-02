import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import pytest

from climauy.analysis.lag import HoldoutResult, LagResult
from climauy.analysis.status import StationStatus
from climauy.catalog import Station
from climauy.catalog import load_catalog
from climauy.site.charts import (
    MAX_PAST_SEASONS,
    P_BOTTOM,
    P_TOP,
    forecast_band_svg,
    gauge_svg,
    lag_svg,
    profile_svg,
    profile_y,
    season_compare_svg,
    sparkline_svg,
)
from climauy.store import Forecast

NS = {"s": "http://www.w3.org/2000/svg"}


def station(sid, name, order, represents=None, tributary_of=None, alert_m=None, evac_m=None, ina=True):
    return Station(
        id=sid, name=name, country="AR", river="Uruguay", lat=0.0, lon=0.0, river_km=None, km_source=None,
        sources={"ina_level": "1"} if ina else {"ana": "1"}, represents=represents,
        alert_m=alert_m, evac_m=evac_m, cota_source="INA" if alert_m is not None else None,
        order=order, tributary_of=tributary_of,
    )


def status(sid, pct=50.0, pct3=None, stale=False, alert_pct=None, level=None, variable="level",
           years=30.0, since=1990):
    return StationStatus(
        station_id=sid, variable_used=variable, latest_value=level,
        latest_time_utc=datetime(2026, 11, 1, tzinfo=timezone.utc), latest_source="ina", stale=stale,
        stale_since=date(2026, 10, 29) if stale else None,
        level_change_3d_m=None, discharge_change_3d_pct=None, trend=None, trend_basis=None,
        percentile_today=None if stale else pct, percentile_3d_ago=None if stale else pct3,
        alert_percentile=alert_pct, level_m=None if stale else level,
        above_alert=None, above_evac=None, history_years=years, history_start_year=since,
    )


CATALOG = [
    station("a", "Santo Tomé", 1),
    station("b", "Paso de los Libres", 2, alert_m=7.5, evac_m=8.5),
    station("q", "Quaraí", 3, represents="Artigas", tributary_of="c"),
    station("c", "Monte Caseros", 4, represents="Bella Unión"),
    station("d", "Concordia", 5, represents="Salto"),
    station("e", "Colón", 6, represents="Paysandú"),
]


def parse(svg):
    return ET.fromstring(svg)


def today_path(svg):
    root = parse(svg)
    paths = [p for p in root.iter("{http://www.w3.org/2000/svg}path") if "today" in p.get("class", "").split()]
    assert len(paths) == 1
    return paths[0]


def subpaths(d):
    return len(re.findall(r"M", d))


def lag_result():
    hold = HoldoutResult("2009", 2, date(2009, 12, 3), date(2009, 11, 24), 9, 3300.0, 1730.0)
    return LagResult(
        upstream="b:discharge", downstream="d:discharge", lag_days_median=2.0, lag_days_range=(1.0, 13.0),
        per_event={"1997-06-26": 1.0, "2009-11-24": 2.0, "2015-12-26": 4.0, "2023-11-27": 1.0},
        holdout={"2009": hold}, validated=False, reason="No validado: en 2009 el pronóstico le erró al pico por 9 días.",
    )


def forecasts():
    issued = datetime(2026, 11, 1, 3, tzinfo=timezone.utc)
    out = []
    for k in range(1, 5):
        valid = issued + timedelta(days=k)
        for q, v in (("inferior", 6.0 - k * 0.3), ("main", 6.5 - k * 0.3), ("superior", 7.0 - k * 0.3)):
            out.append(Forecast("ina", "b", "level", issued, valid, q, v))
    return out


def all_svgs():
    obs = {date(2026, 10, 1) + timedelta(days=i): 5.0 + i * 0.05 for i in range(31)}
    return [
        profile_svg([status(s.id, 80.0, 70.0) for s in CATALOG], CATALOG),
        gauge_svg(status("b", level=7.66), CATALOG[1]),
        sparkline_svg(obs),
        forecast_band_svg(obs, forecasts()),
        season_compare_svg({"2009-10": [(0, 8.0), (1, 9.0)], "2026-27": [(0, 7.0), (1, 7.5)]}, 11.0),
        lag_svg(lag_result(), "Paso de los Libres", "Concordia"),
    ]


def test_profile_breaks_on_stale():
    sts = [status("a", 80.0), status("b", 85.0), status("c", stale=True), status("d", 90.0), status("e", 91.0)]
    path = today_path(profile_svg(sts, CATALOG))
    assert subpaths(path.get("d")) == 2


def test_profile_breaks_on_missing_percentile():
    sts = [status("a", 80.0), status("b", 85.0), status("c", pct=None), status("d", 90.0), status("e", 91.0)]
    assert subpaths(today_path(profile_svg(sts, CATALOG)).get("d")) == 2


def test_profile_morph_start_matches_today_path():
    sts = [status("a", 80.0, 60.0), status("b", 85.0, None), status("c", 88.0, 70.0),
           status("d", 90.0, 95.0), status("e", 91.0, 91.0)]
    path = today_path(profile_svg(sts, CATALOG))
    d, d0 = path.get("d"), path.get("data-d0")
    assert d0 is not None
    nums = re.findall(r"-?\d+(?:\.\d+)?", d)
    nums0 = re.findall(r"-?\d+(?:\.\d+)?", d0)
    assert len(nums) == len(nums0)
    assert re.sub(r"[-\d.]+", "#", d) == re.sub(r"[-\d.]+", "#", d0)
    # x coordinates never move; only y does.
    assert nums[0::2] == nums0[0::2]
    # b has no 3-day value: it starts where it ends.
    assert nums[3] == nums0[3]
    assert nums[1] != nums0[1]


def test_profile_excludes_tributaries():
    svg = profile_svg([status(s.id, 80.0) for s in CATALOG], CATALOG)
    assert "Quaraí" not in svg
    assert "Artigas" not in svg
    assert subpaths(today_path(svg).get("d")) == 1


def test_profile_desc_lists_values():
    sts = [status("a", 80.4, 70.0), status("b", 85.0, alert_pct=93.1), status("c", stale=True),
           status("d", 92.3, 91.3), status("e", 91.0)]
    root = parse(profile_svg(sts, CATALOG))
    desc = root.find("s:desc", NS).text
    assert "Santo Tomé: hoy 80, hace 3 días 70" in desc
    assert "Paso de los Libres: hoy 85" in desc
    assert "alerta en 93" in desc
    assert "Bella Unión (medido en Monte Caseros): desactualizada desde el 29/10/2026" in desc
    assert "Salto (medido en Concordia): hoy 92, hace 3 días 91" in desc


def test_profile_labels_uy_cities_with_measuring_station():
    svg = profile_svg([status(s.id, 80.0) for s in CATALOG], CATALOG)
    root = parse(svg)
    texts = ["".join(t.itertext()) for t in root.iter("{http://www.w3.org/2000/svg}text")]
    assert "Salto, medido en Concordia" in texts
    assert "Paysandú, medido en Colón" in texts


def test_gauge_no_lines_without_cota():
    st = station("x", "Garruchos", 1)
    svg = gauge_svg(status("x", level=4.83), st)
    assert 'class="cota' not in svg
    assert "alerta" not in svg
    assert "evacuación" not in svg


def test_gauge_lines_with_cota():
    svg = gauge_svg(status("b", level=7.66), CATALOG[1])
    assert 'class="cota alert"' in svg
    assert 'class="cota evac"' in svg
    assert "7,66 m" in svg


def test_gauge_ignores_cota_without_ina_level():
    st = station("y", "Uruguaiana", 1, alert_m=7.0, evac_m=8.0, ina=False)
    assert 'class="cota' not in gauge_svg(status("y", level=7.9), st)


def test_gauge_without_level():
    svg = gauge_svg(status("b", stale=True), CATALOG[1])
    parse(svg)
    assert "sin dato" in svg


def test_svgs_have_title_desc():
    for svg in all_svgs():
        root = parse(svg)
        assert root.get("role") == "img"
        assert root[0].tag == "{http://www.w3.org/2000/svg}title" and root[0].text
        assert root[1].tag == "{http://www.w3.org/2000/svg}desc" and root[1].text


def test_text_is_escaped():
    cat = [station("a", "Puerto & Río <norte>", 1), station("b", "Otro", 2)]
    svg = profile_svg([status("a", 80.0), status("b", 81.0)], cat)
    parse(svg)
    assert "Puerto &amp; Río &lt;norte&gt;" in svg


def test_no_banned_typography():
    for svg in all_svgs():
        assert "—" not in svg and " -- " not in svg and " · " not in svg and "→" not in svg


def test_wide_chart_font_sizes_readable_at_390():
    rendered = 390 - 2 * 16
    for svg in all_svgs():
        root = parse(svg)
        if "chart-wide" not in root.get("class"):
            continue
        width = float(root.get("viewBox").split()[2])
        for size in re.findall(r'font-size="([\d.]+)"', svg):
            assert float(size) * rendered / width >= 12


def test_charts_render_without_data():
    empty_lag = LagResult("b:level", "d:level", None, None, {}, {}, False, "No validado: no hay crecidas para estimar el rezago.")
    for svg in (
        sparkline_svg({}),
        forecast_band_svg({}, []),
        season_compare_svg({}, None),
        lag_svg(empty_lag, "Paso de los Libres", "Concordia"),
        profile_svg([], CATALOG),
    ):
        root = parse(svg)
        assert root.find("s:desc", NS).text


def test_forecast_uses_latest_run_only():
    old = [Forecast("ina", "b", "level", datetime(2026, 10, 30, 3, tzinfo=timezone.utc),
                    datetime(2026, 11, 3, 3, tzinfo=timezone.utc), "main", 99.0)]
    svg = forecast_band_svg({date(2026, 10, 31): 6.0}, forecasts() + old)
    assert "99" not in parse(svg).find("s:desc", NS).text


def ys(d):
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", d)]
    return nums[1::2]


def test_profile_axis_is_log_exceedance():
    assert profile_y(50) == pytest.approx(P_BOTTOM)
    assert profile_y(99) == pytest.approx(P_TOP)
    assert profile_y(99.9) == pytest.approx(P_TOP)  # above 99 sits on the top edge
    marks = [profile_y(p) for p in (50, 75, 90, 95, 99)]
    assert marks == sorted(marks, reverse=True)
    # Log scale: 90 to 99 takes more room than 50 to 75.
    assert profile_y(90) - profile_y(99) > profile_y(50) - profile_y(75)


def test_profile_points_follow_axis():
    pcts = [55.0, 75.0, 90.0, 95.0, 99.0]
    sts = [status(s.id, p) for s, p in zip([c for c in CATALOG if c.tributary_of is None], pcts)]
    got = ys(today_path(profile_svg(sts, CATALOG)).get("d"))
    assert got == sorted(got, reverse=True)
    assert got[-1] == pytest.approx(P_TOP, abs=0.1)


def test_profile_pins_below_median():
    sts = [status("a", 80.0), status("b", 30.0), status("c", 85.0), status("d", 90.0), status("e", 91.0)]
    svg = profile_svg(sts, CATALOG)
    d = today_path(svg).get("d")
    assert subpaths(d) == 2  # no drop to the bottom: the line breaks
    assert max(ys(d)) < P_BOTTOM
    root = parse(svg)
    markers = [c for c in root.iter("{http://www.w3.org/2000/svg}circle") if c.get("class") == "below-median"]
    assert len(markers) == 1 and float(markers[0].get("cy")) == pytest.approx(P_BOTTOM)
    assert "Paso de los Libres: hoy 30, por debajo de la mediana" in root.find("s:desc", NS).text


def texts_of(svg):
    return ["".join(t.itertext()) for t in parse(svg).iter("{http://www.w3.org/2000/svg}text")]


def test_profile_names_crest():
    sts = [status("a", 80.0), status("b", 85.0), status("c", 88.0), status("d", 96.4), status("e", 91.0)]
    texts = texts_of(profile_svg(sts, CATALOG))
    assert "pico hoy: Salto (medido en Concordia)más alto que el 96% de los días" in texts


def test_profile_crest_plain_station():
    sts = [status("a", 97.0), status("b", 85.0)]
    texts = texts_of(profile_svg(sts, CATALOG))
    assert "pico hoy: Santo Tomémás alto que el 97% de los días" in texts


def test_profile_city_label_stale_names_station():
    sts = [status("a", 80.0), status("b", 85.0), status("c", stale=True), status("d", 90.0), status("e", 91.0)]
    texts = texts_of(profile_svg(sts, CATALOG))
    assert "Bella Unión, medido en Monte Caseros:" in texts
    assert "desactualizado desde el 29/10" in texts


def test_profile_city_label_without_percentile_names_station():
    sts = [status("a", 80.0), status("b", 85.0), status("c", 88.0), status("d", 90.0), status("e", pct=None)]
    texts = texts_of(profile_svg(sts, CATALOG))
    assert "Paysandú, medido en Colón:" in texts
    assert "sin percentil hoy" in texts


def test_profile_city_without_station_data():
    cat = CATALOG + [station("fb", "Fray Bentos", 7, represents="Fray Bentos")]
    fb = StationStatus(
        station_id="fb", variable_used="level", latest_value=None, latest_time_utc=None, latest_source=None,
        stale=True,
        stale_since=None, level_change_3d_m=None, discharge_change_3d_pct=None, trend=None, trend_basis=None,
        percentile_today=None, percentile_3d_ago=None, alert_percentile=None, level_m=None, above_alert=None,
        above_evac=None, history_years=0.0, history_start_year=None,
    )
    texts = texts_of(profile_svg([status(s.id, 80.0) for s in CATALOG] + [fb], cat))
    assert "Fray Bentos:" in texts
    assert "sin estación con datos al día" in texts


def test_profile_alert_ticks_only_with_data():
    sts = [status("a", 80.0, alert_pct=95.0), status("b", stale=True, alert_pct=93.0),
           status("c", pct=None, alert_pct=97.0), status("d", 90.0, alert_pct=96.0), status("e", 91.0)]
    root = parse(profile_svg(sts, CATALOG))
    ticks = [l for l in root.iter("{http://www.w3.org/2000/svg}line") if l.get("class") == "alert-tick"]
    assert len(ticks) == 2


def test_profile_real_catalog_names_stem():
    cat = load_catalog()
    texts = texts_of(profile_svg([status(s.id, 80.0) for s in cat], cat))
    assert "aguas arriba" in texts and "desembocadura" in texts
    assert "Paso de los Libres" in texts
    assert sum(t in texts for t in ("Itá", "Garruchos", "Salto Grande", "Concordia", "Nueva Palmira")) >= 2


def test_gauge_window_above_14_m():
    st = station("sg", "Salto Grande Arriba", 1, alert_m=35.5, evac_m=36.0)
    svg = gauge_svg(status("sg", level=34.99), st)
    assert "Escala de 25 a 37 m" in parse(svg).find("s:desc", NS).text
    texts = texts_of(svg)
    assert "25" in texts and "0" not in texts


def test_season_title_from_keys():
    svg = season_compare_svg({"2014-15": [(0, 5.0)], "1997-98": [(0, 6.0)], "2026-27": [(0, 7.0)]}, None)
    assert parse(svg).find("s:title", NS).text == "Esta temporada contra las crecidas de 1997 y 2014"
    texts = texts_of(svg)
    assert "1997" in texts and "2014" in texts and "2026-27" in texts


def test_season_too_many_past_seasons():
    series = {f"{2000 + k}-{k + 1:02d}": [(0, 5.0)] for k in range(MAX_PAST_SEASONS + 2)}
    with pytest.raises(ValueError):
        season_compare_svg(series, None)


def test_lag_uses_given_names():
    svg = lag_svg(lag_result(), "Paso de los Libres", "Concordia")
    root = parse(svg)
    assert root.find("s:title", NS).text == "Lo que tardó el pico en llegar de Paso de los Libres a Concordia"
    assert "de Paso de los Libres a Concordia" in root.find("s:desc", NS).text


@pytest.mark.parametrize("p, text", [(99.5, "99,5%"), (99.99, "99,9%"), (94.7, "94%"), (99.0, "99,0%")])
def test_crest_percentile_never_rounds_up(p, text):
    texts = texts_of(profile_svg([status("a", p), status("b", 50.0)], CATALOG))
    assert f"pico hoy: Santo Tomémás alto que el {text} de los días" in texts


def test_desc_percentile_never_rounds_up():
    desc = parse(profile_svg([status("a", 99.97, 99.5, alert_pct=99.99), status("b", 94.7)], CATALOG)).find("s:desc", NS).text
    assert "Santo Tomé: hoy 99,9, hace 3 días 99,5, alerta en 99,9" in desc
    assert "Paso de los Libres: hoy 94" in desc
    assert "100" not in desc


def test_alert_below_median_open_tick():
    sts = [status("a", 80.0, alert_pct=40.0), status("b", 85.0)]
    svg = profile_svg(sts, CATALOG)
    root = parse(svg)
    ticks = [l for l in root.iter("{http://www.w3.org/2000/svg}line") if l.get("class") == "alert-tick below-median"]
    assert len(ticks) == 1 and float(ticks[0].get("y1")) == pytest.approx(P_BOTTOM)
    assert "alerta en 40, por debajo de la mediana" in root.find("s:desc", NS).text


def test_season_current_label_stays_clear_of_year_column():
    full = [(d, 5.0) for d in range(182)]
    svg = season_compare_svg({"2009-10": full, "2026-27": full}, None)
    label = [t for t in parse(svg).iter("{http://www.w3.org/2000/svg}text") if t.get("class") == "current-label"][0]
    assert label.get("text-anchor") == "end"
    early = season_compare_svg({"2009-10": full, "2026-27": full[:30]}, None)
    label = [t for t in parse(early).iter("{http://www.w3.org/2000/svg}text") if t.get("class") == "current-label"][0]
    assert label.get("text-anchor") is None


def test_gauge_scale_always_contains_level():
    # Puerto Concepción: low water far below its high cotas.
    st = station("pc", "Puerto Concepción", 1, alert_m=15.5, evac_m=16.5)
    svg = gauge_svg(status("pc", level=3.6), st)
    desc = parse(svg).find("s:desc", NS).text
    lo, hi = (int(v) for v in re.search(r"Escala de (\d+) a (\d+) m", desc).groups())
    assert lo <= 3.6 <= hi
    assert lo == 2
    assert "3,6 m" in texts_of(svg)


def test_tick_labels_keep_decimals_for_fractional_steps():
    from climauy.site.charts import _tick_label
    assert [_tick_label(v, 2.5) for v in (0, 2.5, 5, 7.5, 10)] == ["0", "2,5", "5,0", "7,5", "10,0"]
    assert [_tick_label(v, 2) for v in (0, 2, 4)] == ["0", "2", "4"]
    assert _tick_label(12500, 2500) == "12.500"
    assert _tick_label(0.25, 0.25) == "0,25"


def test_profile_axis_caption_has_no_placeholder():
    svg = profile_svg([status("a", 80.0)], CATALOG)
    assert "X%" not in svg


def test_profile_crest_record_wording():
    svg = profile_svg([status("a", 100.0), status("b", 80.0)], CATALOG)
    texts = texts_of(svg)
    assert "pico hoy: Santo Tomémás alto que el" not in "".join(texts)
    assert "pico hoy: Santo Toméel valor más alto desde 1990" in texts
    assert not any("100%" in t for t in texts)


def test_profile_crest_record_with_short_coverage():
    texts = texts_of(profile_svg([status("a", 100.0, years=12.0, since=2014), status("b", 80.0)], CATALOG))
    assert "pico hoy: Santo Toméel valor más alto desde 2014" in texts


def test_profile_alert_tick_only_for_level_stations():
    # A percentile of discharge cannot be compared with where the alert level falls among levels.
    sts = [status("a", 80.0, alert_pct=95.0, variable="discharge"), status("b", 85.0, alert_pct=93.0)]
    root = parse(profile_svg(sts, CATALOG))
    ticks = [l for l in root.iter("{http://www.w3.org/2000/svg}line") if (l.get("class") or "").startswith("alert-tick")]
    assert len(ticks) == 1
    desc = root.find("s:desc", NS).text
    assert "Santo Tomé: hoy 80;" in desc
    assert "Paso de los Libres: hoy 85, alerta en 93" in desc
    assert desc.count("alerta en") == 1


def test_glofas_chart_says_downloaded_not_issued():
    fetched = datetime(2026, 11, 2, 12, 23, tzinfo=timezone.utc)
    fc = [Forecast("glofas", "x", "discharge", fetched, datetime(2026, 11, 2 + k, 3, tzinfo=timezone.utc), "median", 9000.0)
          for k in (1, 2)]
    svg = forecast_band_svg({date(2026, 11, 1): 9500.0}, fc)
    assert "Pronóstico GloFAS bajado el 2/11/2026" in parse(svg).find("s:desc", NS).text
    assert "bajado el 2/11" in texts_of(svg) and not any("emitido" in t for t in texts_of(svg))
    ina = forecast_band_svg({date(2026, 10, 31): 6.0}, forecasts())
    assert "emitido el" in parse(ina).find("s:desc", NS).text


# ---------------------------------------------------------------- plan profile (home)

from climauy.site import charts as charts_mod  # noqa: E402
from climauy.site.charts import phone_start_km, profile_plan_svg, river_km  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"
REAL = load_catalog()
REAL_BY_ID = {s.id: s for s in REAL}

# Main-river readings of 2026-11-02 (the approved mockup's data):
# (id, percentile today, percentile 3 days ago, level m, above alert).
TODAY = [
    ("ana_72700000", 73.3, None, 3.41, None), ("ana_73200080", 73.4, None, 367.88, None),
    ("ana_74050000", None, None, 2.94, None), ("ana_74100000", 79.3, None, 2.55, None),
    ("ana_74329000", 67.1, None, 2.53, None), ("ina_60", 73.8, 84.9, 3.75, None),
    ("ana_74500000", 66.4, None, 2.64, None), ("ina_61", 79.5, 87.0, 3.2, None),
    ("ina_63", 83.1, 88.6, 4.45, None), ("ana_74720000", None, None, 3.84, None),
    ("ina_64", 89.0, 95.0, 3.7, None), ("ana_74800000", 79.3, None, 2.13, None),
    ("ina_65", 80.5, 89.6, 3.33, False), ("ina_66", 81.6, 95.4, 3.6, False),
    ("ina_67", 74.5, 94.3, 4.83, False), ("ina_68", 86.3, 96.6, 6.64, False),
    ("ana_75780000", 81.8, None, 5.59, None), ("ina_69", 93.6, 96.2, 8.0, False),
    ("ana_75900000", 85.8, None, 5.93, None), ("ina_70", 93.2, 95.8, 8.12, False),
    ("ina_71", 94.5, 95.9, 7.98, False), ("ina_72", 93.9, 95.1, 7.66, True),
    ("ana_77150000", 91.6, None, 7.624, None), ("ina_74", 95.0, 93.7, 6.05, False),
    ("ina_75", 86.6, 69.1, 9.18, False), ("ina_77", 93.8, 92.7, 34.99, False),
    ("ina_78", 93.7, 92.4, 12.35, False), ("ina_79", 92.3, 91.3, 9.3, False),
    ("ina_80", 91.2, 91.2, 5.5, False), ("ina_81", 90.8, 91.2, 4.3, False),
    ("ina_82", 87.4, 88.5, 3.32, False), ("ina_84", 80.9, 86.4, 2.1, False),
    ("fray_bentos", None, None, None, None), ("ina_1699", None, None, 1.17, None),
]


def pstatus(sid, pct, pct3=None, level=None, above=None):
    return StationStatus(
        station_id=sid, variable_used="level", latest_value=level,
        latest_time_utc=datetime(2026, 11, 2, tzinfo=timezone.utc), latest_source="ina", stale=False,
        stale_since=None, level_change_3d_m=None, discharge_change_3d_pct=None, trend=None, trend_basis=None,
        percentile_today=pct, percentile_3d_ago=pct3, alert_percentile=None, level_m=level,
        above_alert=above, above_evac=None, history_years=30.0, history_start_year=1990,
    )


def today_statuses():
    return [pstatus(*row) for row in TODAY]


def flood_statuses():
    """Every main-river station above percentile 99, every one with an alert level above it."""
    out = []
    for sid, pct, _, level, _ in TODAY:
        st = REAL_BY_ID[sid]
        if pct is None:
            out.append(pstatus(sid, None, None, level))
            continue
        out.append(pstatus(sid, 99.6, 99.2, level + 3.0, True if st.alert_m is not None else None))
    return out


def by_class(root, tag, cls):
    return [e for e in root.iter(SVG_NS + tag) if cls in (e.get("class") or "").split()]


def label_boxes(svg):
    root = parse(svg)
    return [(float(r.get("x")), float(r.get("y")), float(r.get("width")), float(r.get("height")))
            for r in by_class(root, "rect", "lbg")]


def overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_palette_matches_design_tokens():
    assert charts_mod.PAPER == "#F7F8F9"      # --paper oklch(97.8% 0.002 250)
    assert charts_mod.GRID == "#D2D8DE"       # --grid-2 oklch(88% 0.01 245)
    assert charts_mod.INK == "#15191E"        # --ink oklch(21% 0.012 255)
    assert charts_mod.INK_2 == "#54585F"      # --ink-2 oklch(46% 0.012 255)
    assert charts_mod.WATER == "#0062A1"      # --water oklch(48% 0.13 245)
    assert charts_mod.ALERT == "#C92420"      # --alert oklch(54% 0.2 28)
    assert charts_mod.SILT == charts_mod.WATER
    assert charts_mod.LOW == charts_mod.INK_2
    assert charts_mod.ALERT_LINE == charts_mod.EVAC == charts_mod.ALERT
    assert charts_mod.FONT == "Barlow, sans-serif"
    assert "mono" not in charts_mod.FONT.lower()


def test_old_charts_use_new_palette_only():
    old = {"#EEF1EC", "#1B2A33", "#4F5E66", "#C9D3CC", "#9C7A4D", "#E3A21A", "#A87000", "#C62828", "#3E6B7A"}
    for svg in all_svgs():
        assert not any(h in svg for h in old)
        assert "Barlow Condensed" not in svg


def test_river_km_monotonic_and_counts_salto_grande_once():
    km = river_km(REAL)
    main = [s for s in REAL if s.river == "Uruguay"]
    assert "ina_78" not in km and "ina_77" in km
    assert len(km) == len(main) - 1
    assert list(km) == [s.id for s in main if s.id != "ina_78"]
    vals = list(km.values())
    assert vals[0] == 0.0
    assert all(b > a for a, b in zip(vals, vals[1:]))
    assert "ana_77500000" not in km  # Quaraí is a tributary


def test_plan_profile_x_monotonic_and_one_point_per_plotted_station():
    root = parse(profile_plan_svg(today_statuses(), REAL))
    circles = [c for c in root.iter(SVG_NS + "circle")]
    xs = [float(c.get("cx")) for c in circles]
    assert all(b > a for a, b in zip(xs, xs[1:]))
    plotted = [r for r in TODAY if r[1] is not None and r[0] != "ina_78"]
    assert len(circles) == len(plotted)


def test_plan_profile_marks_cities_and_alert():
    root = parse(profile_plan_svg(today_statuses(), REAL))
    assert len(by_class(root, "circle", "pc")) == 3  # Bella Unión, Salto, Paysandú
    assert len(by_class(root, "circle", "pa")) == 1  # Paso de los Libres
    names = [t.text for t in by_class(root, "text", "cn")]
    assert names == ["Paso de los Libres", "Bella Unión", "Salto", "Paysandú"]
    alert = by_class(root, "text", "ca")
    assert [t.text for t in alert] == ["Paso de los Libres"]
    values = [t.text for t in by_class(root, "text", "cv")]
    assert values == ["7,66 m · sobre alerta", "6,05 m · P95", "9,30 m · P92", "5,50 m · P91"]


def test_plan_profile_accessible():
    svg = profile_plan_svg(today_statuses(), REAL)
    root = parse(svg)
    assert root.get("role") == "img"
    assert root.get("aria-label")
    assert "perfil" in root.get("class").split()
    title = root.find("s:title", NS)
    assert title is not None and "río Uruguay" in title.text
    assert root[0].tag == SVG_NS + "title"


def y_axis_labels(svg):
    root = parse(svg)
    return [int(t.text) for t in by_class(root, "text", "ax") if t.get("text-anchor") == "end"]


def test_plan_profile_y_axis_50_to_100_by_default():
    labels = y_axis_labels(profile_plan_svg(today_statuses(), REAL))
    assert labels == [50, 60, 70, 80, 90, 100]


def test_plan_profile_y_bottom_drops_below_50():
    sts = today_statuses()
    sts[0] = pstatus("ana_72700000", 47.0, None, 3.41)
    assert min(y_axis_labels(profile_plan_svg(sts, REAL))) == 40
    sts = today_statuses()
    sts[5] = pstatus("ina_60", 73.0, 30.0, 3.75)  # a low 3-day value counts too
    assert min(y_axis_labels(profile_plan_svg(sts, REAL))) == 30


def test_plan_profile_reference_line_at_90_dashed():
    root = parse(profile_plan_svg(today_statuses(), REAL))
    assert len(by_class(root, "line", "g2")) == 1
    assert len(by_class(root, "polyline", "l0")) == 1
    assert len(by_class(root, "polyline", "l3")) == 1
    assert len(by_class(root, "rect", "uy")) == 1


@pytest.mark.parametrize("variant", ["wide", "movil"])
@pytest.mark.parametrize("fixture", [today_statuses, flood_statuses])
def test_plan_profile_labels_never_overlap(variant, fixture):
    sts = fixture()
    if variant == "wide":
        svg = profile_plan_svg(sts, REAL)
    else:
        svg = profile_plan_svg(sts, REAL, width=340, height=560, start_km=phone_start_km(sts, REAL), variant="movil")
    boxes = label_boxes(svg)
    assert boxes
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not overlaps(a, b), (a, b)
    root = parse(svg)
    w, h = (float(v) for v in root.get("viewBox").split()[2:])
    for x, y, bw, bh in boxes:
        assert x >= 0 and x + bw <= w and y + bh <= h


CITIES = ["Bella Unión", "Salto", "Paysandú"]


def legend_lines(root):
    return [t.text for t in by_class(root, "text", "ax") if t.text.startswith("En rojo")]


def test_plan_profile_flood_labels_only_cities_and_counts_the_rest():
    sts = flood_statuses()
    root = parse(profile_plan_svg(sts, REAL))
    assert [t.text for t in by_class(root, "text", "cn")] == CITIES
    assert len(by_class(root, "circle", "pa")) == 16  # every alert dot stays
    assert legend_lines(root) == ["En rojo, 13 estaciones sobre su cota de alerta."]
    # City callouts stay within two rows on wide.
    assert len({t.get("y") for t in by_class(root, "text", "cn")}) <= 2


def test_plan_profile_two_alert_stations_both_labelled():
    sts = today_statuses()
    sts[13] = pstatus("ina_66", 99.0, 98.0, 16.0, True)  # Puerto Concepción joins Paso de los Libres
    root = parse(profile_plan_svg(sts, REAL))
    names = [t.text for t in by_class(root, "text", "cn")]
    assert names == ["Puerto Concepción", "Paso de los Libres"] + CITIES
    assert [t.text for t in by_class(root, "text", "ca")] == ["Puerto Concepción", "Paso de los Libres"]
    assert legend_lines(root) == []


def test_plan_profile_today_has_no_legend_line():
    assert legend_lines(parse(profile_plan_svg(today_statuses(), REAL))) == []


def test_phone_start_km_rule():
    km = river_km(REAL)
    # Today the first callout is Paso de los Libres, at about 648 km: start 60 km above it.
    assert phone_start_km(today_statuses(), REAL) == pytest.approx(km["ina_72"] - 60.0)
    # Callouts all downstream of 680 km: the start stays at 620 km.
    late = [pstatus(sid, pct, None, level, None) for sid, pct, _, level, _ in TODAY]
    assert phone_start_km(late, REAL) == 620.0
    # A callout further upstream pulls the start along.
    sts = today_statuses()
    sts[13] = pstatus("ina_66", 99.0, 98.0, 16.0, True)
    assert phone_start_km(sts, REAL) == pytest.approx(km["ina_66"] - 60.0)


def test_plan_profile_phone_variant_starts_at_start_km():
    km = river_km(REAL)
    svg = profile_plan_svg(today_statuses(), REAL, width=340, height=560, start_km=620.0, variant="movil")
    root = parse(svg)
    assert root.get("class").split() == ["perfil", "movil"]
    assert root.get("viewBox").split()[2] == "340"
    plotted = [r for r in TODAY if r[1] is not None and r[0] != "ina_78" and km[r[0]] >= 620.0]
    circles = list(root.iter(SVG_NS + "circle"))
    assert len(circles) == len(plotted)
    # The first plotted station after 620 km sits right of the left margin, at its km.
    ticks = [int(t.text.replace(".", "")) for t in by_class(root, "text", "ax") if t.get("text-anchor") == "middle"]
    assert ticks and ticks[0] >= 620


@pytest.mark.parametrize("variant", ["wide", "movil"])
@pytest.mark.parametrize("fixture", [today_statuses, flood_statuses])
def test_plan_profile_leaders_never_run_under_another_label(variant, fixture):
    sts = fixture()
    if variant == "wide":
        svg = profile_plan_svg(sts, REAL)
    else:
        svg = profile_plan_svg(sts, REAL, width=340, height=560, start_km=phone_start_km(sts, REAL), variant="movil")
    root = parse(svg)
    leads = [(float(l.get("x1")), float(l.get("y1")), float(l.get("y2"))) for l in by_class(root, "line", "lead")]
    boxes = label_boxes(svg)
    assert len(leads) == len(boxes)
    for k, (x, y1, y2) in enumerate(leads):
        for j, (bx, by, bw, bh) in enumerate(boxes):
            if j == k:
                continue
            assert not (bx <= x <= bx + bw and by < y2 and y1 < by + bh), (k, j)


def test_plan_profile_wide_today_staggers_two_rows_like_the_mockup():
    root = parse(profile_plan_svg(today_statuses(), REAL))
    ys = [float(t.get("y")) for t in by_class(root, "text", "cn")]
    assert ys[0] == ys[2] and ys[1] == ys[3] and ys[1] > ys[0]


def test_plan_profile_phone_flood_labels_only_cities():
    sts = flood_statuses()
    km = river_km(REAL)
    start = phone_start_km(sts, REAL)
    assert start == 620.0  # only the cities count as callouts now
    root = parse(profile_plan_svg(sts, REAL, width=340, height=560, start_km=start, variant="movil"))
    ys = [t.get("y") for t in by_class(root, "text", "cn")]
    assert [t.text for t in by_class(root, "text", "cn")] == CITIES
    assert len(set(ys)) == 3  # one row each
    visible = [sid for sid in km if km[sid] >= start and REAL_BY_ID[sid].represents is None
               and REAL_BY_ID[sid].alert_m is not None]
    assert len(by_class(root, "circle", "pa")) == len(visible) + 3
    assert legend_lines(root) == [f"En rojo, {len(visible)} estaciones sobre su cota de alerta."]


def test_plan_profile_labels_clear_the_km_caption():
    for sts in (today_statuses(), flood_statuses()):
        svg = profile_plan_svg(sts, REAL)
        root = parse(svg)
        cap = next(t for t in by_class(root, "text", "ax") if t.text.startswith("km desde"))
        cap_x, cap_y = float(cap.get("x")), float(cap.get("y"))
        cap_box = (cap_x, cap_y - 10, len(cap.text) * 6.0, 14)
        assert not any(overlaps(cap_box, b) for b in label_boxes(svg))


@pytest.mark.parametrize("variant", ["wide", "movil"])
def test_plan_profile_flood_legend_line_clear_of_labels(variant):
    sts = flood_statuses()
    if variant == "wide":
        svg = profile_plan_svg(sts, REAL)
    else:
        svg = profile_plan_svg(sts, REAL, width=340, height=560, start_km=phone_start_km(sts, REAL), variant="movil")
    root = parse(svg)
    line = next(t for t in by_class(root, "text", "ax") if t.text.startswith("En rojo"))
    box = (float(line.get("x")), float(line.get("y")) - 10, len(line.text) * 6.0, 14)
    assert not any(overlaps(box, b) for b in label_boxes(svg))
    assert box[0] + box[2] <= float(root.get("viewBox").split()[2]) + 20
