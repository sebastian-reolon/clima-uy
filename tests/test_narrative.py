from datetime import datetime, timezone

from climauy.analysis.status import StationStatus
from climauy.catalog import Station
from climauy.narrative import enso_summary, river_summary
from climauy.store import IndexValue


def station(sid, name, order, represents=None):
    return Station(
        id=sid, name=name, country="UY", river="Uruguay", lat=0.0, lon=0.0, river_km=None, km_source=None,
        sources={}, represents=represents, alert_m=None, evac_m=None, cota_source=None, order=order,
        tributary_of=None,
    )


def status(sid, pct=50.0, trend=None, stale=False, above_alert=None, years=30.0, since=1990):
    return StationStatus(
        station_id=sid, variable_used="level", latest_value=1.0,
        latest_time_utc=datetime(2026, 11, 1, tzinfo=timezone.utc), latest_source="ina",
        stale=stale, stale_since=None,
        level_change_3d_m=None, discharge_change_3d_pct=None, trend=trend,
        trend_basis="level" if trend else None, percentile_today=pct, percentile_3d_ago=None,
        alert_percentile=None, level_m=None, above_alert=above_alert, above_evac=None,
        history_years=years, history_start_year=since,
    )


CATALOG = [
    station("a", "Santo Tomé", 1),
    station("b", "Concordia", 2, represents="Salto"),
    station("c", "Paysandú", 3),
    station("d", "Fray Bentos", 4),
    station("e", "Nueva Palmira", 5),
]


def test_summary_with_rising_and_falling():
    sts = [
        status("a", pct=94.4, trend="sube", above_alert=True),
        status("b", pct=60, trend="sube"),
        status("c", pct=40, trend="baja"),
        status("d", pct=30, trend="estable"),
        status("e", pct=20, trend=None),
    ]
    assert river_summary(sts, CATALOG) == (
        "El río está más alto en Santo Tomé: más que el 94% de los días desde 1990. "
        "Sube en Santo Tomé y Salto (medido en Concordia); baja en Paysandú. "
        "1 estación alcanzó o superó su cota de alerta: Santo Tomé."
    )


def test_summary_omits_stale():
    sts = [
        status("a", pct=99, trend="sube", stale=True, above_alert=True),
        status("c", pct=92, trend="baja"),
    ]
    out = river_summary(sts, CATALOG)
    assert "Santo Tomé" not in out
    assert out.startswith("El río está más alto en Paysandú: más que el 92%")
    assert "alerta" not in out


def test_summary_normal_river():
    out = river_summary([status("a", pct=50), status("c", pct=89.9)], CATALOG)
    assert out == "El río está dentro de valores habituales en todo el recorrido."


def test_summary_uy_city_phrasing():
    out = river_summary([status("b", pct=95, trend="baja", above_alert=True)], CATALOG)
    assert "Salto (medido en Concordia)" in out
    assert out.count("Salto (medido en Concordia)") == 3


def test_summary_plural_alert_and_overflow_list():
    sts = [status(s, pct=50, trend="sube", above_alert=True) for s in "abcde"]
    out = river_summary(sts, CATALOG)
    assert "Sube en Santo Tomé, Salto (medido en Concordia), Paysandú, Fray Bentos y 1 más." in out
    assert "5 estaciones alcanzaron o superaron su cota de alerta: " in out
    assert out.endswith("alerta: Santo Tomé, Salto (medido en Concordia), Paysandú, Fray Bentos y 1 más.")


def test_summary_empty_when_no_usable_stations():
    assert river_summary([status("a", stale=True)], CATALOG) == ""


def test_enso_summary_decimal_comma():
    idx = [
        IndexValue("RONI", "2026-JJA", 1.5), IndexValue("RONI", "2026-JAS", 1.69),
        IndexValue("ONI", "2026-JJA", 1.1), IndexValue("ONI", "2026-JAS", 1.2),
        IndexValue("NINO34_WEEKLY", "2026-10-25", 1.9),
    ]
    assert enso_summary(idx) == (
        "El Niño sigue activo: el índice oficial de la NOAA (RONI) está en +1,69 para julio-septiembre de 2026; "
        "el índice tradicional (ONI) marca +1,20."
    )


def test_enso_summary_variants():
    neg = enso_summary([IndexValue("RONI", "2026-DJF", -0.4), IndexValue("ONI", "2026-DJF", -0.55)])
    assert neg.startswith("El Pacífico está en condiciones neutrales:")
    assert "-0,40 para diciembre de 2025 a febrero de 2026" in neg and "marca -0,55" in neg
    nina = enso_summary([IndexValue("RONI", "2026-NDJ", -0.5)])
    assert nina.startswith("Hay condiciones de La Niña:")
    assert "noviembre de 2026 a enero de 2027" in nina and "tradicional" not in nina
    assert enso_summary([]) == ""


def test_no_banned_chars():
    outs = [
        river_summary([status("a", pct=95, trend="sube", above_alert=True), status("c", trend="baja")], CATALOG),
        river_summary([status("a")], CATALOG),
        enso_summary([IndexValue("RONI", "2026-JAS", -1.0), IndexValue("ONI", "2026-JAS", -1.0)]),
    ]
    for out in outs:
        assert "—" not in out and " -- " not in out and " · " not in out


def test_enso_latest_period_ordering():
    def latest(*periods):
        return enso_summary([IndexValue("RONI", p, 0.0) for p in periods])
    assert "noviembre de 2026 a enero de 2027" in latest("2026-OND", "2026-NDJ")
    assert "diciembre de 2026 a febrero de 2027" not in latest("2026-OND", "2026-NDJ")
    assert "diciembre de 2026 a febrero de 2027" in latest("2026-NDJ", "2027-DJF")


def test_enso_tiny_negative_and_unknown_code():
    out = enso_summary([IndexValue("RONI", "2026-JAS", -0.001), IndexValue("RONI", "2027-XYZ", 2.0)])
    assert "+0,00 para julio-septiembre de 2026" in out


def test_summary_without_percentiles_keeps_other_sentences():
    sts = [status("a", pct=None, trend="sube", above_alert=True), status("c", pct=None)]
    out = river_summary(sts, CATALOG)
    assert out == "Sube en Santo Tomé. 1 estación alcanzó o superó su cota de alerta: Santo Tomé."


def test_summary_fresh_without_pct_ignored_for_max():
    out = river_summary([status("a", pct=None), status("c", pct=92)], CATALOG)
    assert out == "El río está más alto en Paysandú: más que el 92% de los días desde 1990."


def test_percentile_uses_shared_formatter():
    # Same rule as the charts: never rounded up; 99 to <100 with one decimal; 100 only when exact.
    assert "más que el 99,5%" in river_summary([status("a", pct=99.56)], CATALOG)
    assert "más que el 94%" in river_summary([status("a", pct=94.7)], CATALOG)
    assert "más que el 99,9%" in river_summary([status("a", pct=99.95)], CATALOG)


def test_record_reads_highest_since_1990():
    out = river_summary([status("a", pct=100.0)], CATALOG)
    assert out == "El río está más alto en Santo Tomé: el valor más alto desde 1990."
    assert "100%" not in out


def test_summary_nothing_to_say_is_empty():
    assert river_summary([status("a", pct=None)], CATALOG) == ""


def test_short_coverage_says_since_its_first_year():
    out = river_summary([status("a", pct=94.4, years=15.2, since=2011)], CATALOG)
    assert out == "El río está más alto en Santo Tomé: más que el 94% de los días desde 2011."


def test_short_coverage_record_says_since_its_first_year():
    out = river_summary([status("a", pct=100.0, years=12.0, since=2014)], CATALOG)
    assert out == "El río está más alto en Santo Tomé: el valor más alto desde 2014."


def test_desde_1990_only_when_data_starts_in_1990():
    out = river_summary([status("a", pct=94.4, years=30.0, since=1993)], CATALOG)
    assert out.endswith("más que el 94% de los días desde 1993.")
    out = river_summary([status("a", pct=94.4, years=20.0, since=1990)], CATALOG)
    assert out.endswith("más que el 94% de los días desde 1990.")
