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
