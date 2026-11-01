import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import pytest

from climauy.analysis.lag import HoldoutResult, LagResult
from climauy.analysis.status import StationStatus
from climauy.catalog import Station
from climauy.site.charts import (
    forecast_band_svg,
    gauge_svg,
    lag_svg,
    profile_svg,
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


def status(sid, pct=50.0, pct3=None, stale=False, alert_pct=None, level=None):
    return StationStatus(
        station_id=sid, variable_used="level", latest_value=level,
        latest_time_utc=datetime(2026, 11, 1, tzinfo=timezone.utc), stale=stale,
        stale_since=date(2026, 10, 29) if stale else None,
        level_change_3d_m=None, discharge_change_3d_pct=None, trend=None, trend_basis=None,
        percentile_today=None if stale else pct, percentile_3d_ago=None if stale else pct3,
        alert_percentile=alert_pct, level_m=None if stale else level,
        above_alert=None, above_evac=None, history_years=30.0,
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
        lag_svg(lag_result()),
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


def test_profile_font_sizes_readable_at_390():
    svg = profile_svg([status(s.id, 80.0) for s in CATALOG], CATALOG)
    width = float(parse(svg).get("viewBox").split()[2])
    rendered = 390 - 2 * 16
    for size in re.findall(r'font-size="([\d.]+)"', svg):
        assert float(size) * rendered / width >= 12


@pytest.mark.parametrize("empty", [{}, None])
def test_charts_render_without_data(empty):
    empty_lag = LagResult("b:level", "d:level", None, None, {}, {}, False, "No validado: no hay crecidas para estimar el rezago.")
    for svg in (
        sparkline_svg(empty or {}),
        forecast_band_svg(empty or {}, []),
        season_compare_svg(empty or {}, None),
        lag_svg(empty_lag),
        profile_svg([], CATALOG),
    ):
        root = parse(svg)
        assert root.find("s:desc", NS).text


def test_forecast_uses_latest_run_only():
    old = [Forecast("ina", "b", "level", datetime(2026, 10, 30, 3, tzinfo=timezone.utc),
                    datetime(2026, 11, 3, 3, tzinfo=timezone.utc), "main", 99.0)]
    svg = forecast_band_svg({date(2026, 10, 31): 6.0}, forecasts() + old)
    assert "99" not in parse(svg).find("s:desc", NS).text
