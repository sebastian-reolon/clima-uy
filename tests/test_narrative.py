import dataclasses
from datetime import datetime, timezone

from climauy.analysis.status import StationStatus
from climauy.catalog import Station
from climauy.narrative import enso_summary, river_headline, river_summary
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
        "El Niño sigue activo: el índice oficial de la NOAA (RONI) está en +1,69 para julio a septiembre de 2026; "
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
    assert "+0,00 para julio a septiembre de 2026" in out


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


# ---- river_headline ----

def rstation(sid, name, order, represents=None, alert_m=None, country="AR", river="Uruguay"):
    return Station(
        id=sid, name=name, country=country, river=river, lat=0.0, lon=0.0, river_km=None, km_source=None,
        sources={}, represents=represents, alert_m=alert_m, evac_m=None, cota_source=None, order=order,
        tributary_of=None,
    )


def rstatus(sid, trend=None, pct=50.0, change=None, above=False, stale=False, value=1.0):
    s = status(sid, pct=pct, trend=trend, stale=stale, above_alert=above)
    return dataclasses.replace(s, latest_value=value, level_change_3d_m=change)


RCAT = [
    rstation("up1", "Alba Posse", 1),
    rstation("up2", "Santo Tomé", 2),
    rstation("pdl", "Paso de los Libres", 3, alert_m=7.0),
    rstation("mc", "Monte Caseros", 4, represents="Bella Unión", alert_m=6.5),
    rstation("moc", "Mocoretá", 5),
    rstation("con", "Concordia", 6, represents="Salto", alert_m=10.0),
    rstation("col", "Colón", 7, represents="Paysandú", alert_m=6.0),
    rstation("br", "Artigas", 8, represents="Artigas", alert_m=5.0, country="UY", river="Quaraí"),
]


def sts(mc=None, con=None, col=None, up=("baja", "baja"), pdl=("baja", True), moc="sube"):
    """Statuses; each city arg is (trend, pct, change, above)."""
    out = [rstatus("up1", up[0]), rstatus("up2", up[1])]
    out.append(rstatus("pdl", pdl[0], above=pdl[1]))
    for sid, v in (("mc", mc), ("con", con), ("col", col)):
        if v is not None:
            out.append(rstatus(sid, v[0], pct=v[1], change=v[2], above=v[3]))
    out.append(rstatus("moc", moc))
    return out


def test_headline_today_exact():
    s = sts(mc=("sube", 95.0, 0.25, False), con=("sube", 92.3, 0.30, False), col=("estable", 91.0, 0.0, False))
    s.append(rstatus("up3", "baja"))  # unknown to catalog: ignored
    h1, sub = river_headline(s, RCAT)
    assert h1 == "El río viene alto y sube despacio frente a Bella Unión y Salto."
    assert sub == (
        "Ninguna regla de las que miden enfrente llegó a su cota de alerta. Río arriba ya baja. "
        "La única sobre alerta es Paso de los Libres, del lado argentino, y también baja."
    )


def test_headline_city_above_alert():
    s = sts(mc=("sube", 95, 0.2, True), con=("sube", 92, 0.3, False), col=("baja", 91, -0.1, True))
    h1, sub = river_headline(s, RCAT)
    assert h1 == "El río pasó la cota de alerta frente a Bella Unión y Paysandú."
    assert sub == (
        "Río arriba ya baja. La única sobre alerta es Paso de los Libres, del lado argentino, y también baja."
    )


def test_headline_rises_fast_and_plain():
    fast = sts(mc=("sube", 80, 1.2, False))
    assert river_headline(fast, RCAT)[0] == "El río está por encima de lo normal y sube rápido frente a Bella Unión."
    plain = sts(mc=("sube", 50, 0.7, False))
    assert river_headline(plain, RCAT)[0] == "El río está en valores normales y sube frente a Bella Unión."
    # speed comes from the largest rise
    mixed = sts(mc=("sube", 50, 0.2, False), con=("sube", 50, 1.0, False))
    assert river_headline(mixed, RCAT)[0] == "El río está en valores normales y sube rápido frente a Bella Unión y Salto."


def test_headline_falls():
    s = sts(mc=("baja", 10, -0.3, False), con=("estable", 20, 0.0, False))
    assert river_headline(s, RCAT)[0] == "El río está bajo y baja frente a Bella Unión."


def test_headline_stable():
    s = sts(mc=("estable", 60, 0.0, False), con=("estable", 40, 0.0, False))
    assert river_headline(s, RCAT)[0] == "El río está en valores normales y se mantiene estable frente a Bella Unión y Salto."


def test_headline_no_trend():
    s = sts(mc=(None, 95, None, False))
    assert river_headline(s, RCAT)[0] == "El río viene alto."


def test_headline_no_fresh_city_data():
    s = sts(mc=("sube", 95, 0.2, False))
    s = [x if x.station_id != "mc" else rstatus("mc", "sube", stale=True) for x in s]
    assert river_headline(s, RCAT) == ("No hay datos al día del río frente a las ciudades uruguayas.", "")
    assert river_headline([], RCAT) == ("No hay datos al día del río frente a las ciudades uruguayas.", "")


def test_headline_excludes_non_main_river_city():
    s = sts(mc=("baja", 50, -0.1, False)) + [rstatus("br", "sube", pct=99, change=2.0, above=True)]
    assert river_headline(s, RCAT)[0] == "El río está en valores normales y baja frente a Bella Unión."


def test_headline_several_non_city_above_alert():
    s = sts(mc=("baja", 50, -0.1, False), up=("baja", "baja"), pdl=("baja", True))
    s = [x if x.station_id != "up2" else rstatus("up2", "baja", above=True) for x in s]
    sub = river_headline(s, RCAT)[1]
    assert sub.endswith("Sobre la alerta: Santo Tomé y Paso de los Libres.")


def test_headline_single_above_alert_trend_wording():
    s = sts(mc=("baja", 50, -0.1, False), up=("sube", "sube"), pdl=("sube", True))
    assert river_headline(s, RCAT)[1].endswith("La única sobre alerta es Paso de los Libres, del lado argentino, y sigue subiendo.")
    s = sts(mc=("baja", 50, -0.1, False), up=("baja", "baja"), pdl=("estable", True))
    assert river_headline(s, RCAT)[1].endswith("del lado argentino, y está estable.")
    # plain "baja" when the upstream sentence did not say "ya baja"
    s = sts(mc=("baja", 50, -0.1, False), up=("sube", "estable"), pdl=("baja", True))
    sub = river_headline(s, RCAT)[1]
    assert "Río arriba" not in sub and sub.endswith("del lado argentino, y baja.")


def test_headline_brazilian_side():
    cat = RCAT + [rstation("br2", "Garruchos", 0, alert_m=3.0, country="BR")]
    s = sts(mc=("baja", 50, -0.1, False), pdl=("baja", False)) + [rstatus("br2", "baja", above=True)]
    assert "del lado brasileño" in river_headline(s, cat)[1]


def test_headline_upstream_rising_and_tie():
    s = sts(mc=("baja", 50, -0.1, False), up=("sube", "sube"), pdl=("baja", False))
    assert "Río arriba sigue subiendo." in river_headline(s, RCAT)[1]
    s = sts(mc=("baja", 50, -0.1, False), up=("sube", "baja"), pdl=("estable", False))
    assert "Río arriba" not in river_headline(s, RCAT)[1]


def test_headline_upstream_excludes_downstream_and_stale():
    # Mocoretá (downstream of the first city) rising must not count; stale upstream is ignored
    s = sts(mc=("baja", 50, -0.1, False), up=("baja", "baja"), pdl=("baja", False), moc="sube")
    s = [x if x.station_id != "up1" else rstatus("up1", "sube", stale=True) for x in s]
    assert "Río arriba ya baja." in river_headline(s, RCAT)[1]


def test_headline_estable_names_only_stable_cities():
    s = sts(mc=("estable", 60, 0.0, False), con=("estable", 40, 0.0, False), col=(None, 40, None, False))
    assert river_headline(s, RCAT)[0].endswith("se mantiene estable frente a Bella Unión y Salto.")


def test_headline_rise_boundaries():
    assert river_headline(sts(mc=("sube", 50, 0.5, False)), RCAT)[0] == "El río está en valores normales y sube frente a Bella Unión."
    assert river_headline(sts(mc=("sube", 50, 1.0, False)), RCAT)[0].endswith("sube rápido frente a Bella Unión.")


def test_headline_no_percentile_no_trend():
    s = sts(mc=(None, None, None, False), con=(None, None, None, False))
    assert river_headline(s, RCAT)[0] == (
        "El río frente a Bella Unión y Salto tiene datos al día, pero no alcanzan para compararlos con su historia."
    )


def test_headline_city_without_alert_comparison_is_not_a_city():
    # fresh but discharge-only (above_alert None): excluded; the level city decides
    s = sts(mc=("sube", 95, 0.2, None), con=("baja", 50, -0.1, False))
    h1, sub = river_headline(s, RCAT)
    assert h1 == "El río está en valores normales y baja frente a Salto."
    assert sub.startswith("Ninguna de las reglas con dato al día llegó a su cota de alerta. "
                          "Frente a Bella Unión y Paysandú no hay dato al día.")
    only_none = sts(mc=("sube", 95, 0.2, None))
    assert river_headline(only_none, RCAT) == ("No hay datos al día del río frente a las ciudades uruguayas.", "")


def test_headline_stale_city_next_to_fresh_ones():
    # As compute_status leaves a stale gauge: no level, no percentile, no alert comparison.
    s = sts(mc=("sube", 95, 0.2, False), con=("baja", 50, -0.1, False), col=("baja", 60, -0.1, False))
    s = [x if x.station_id != "mc" else rstatus("mc", None, pct=None, above=None, stale=True) for x in s]
    h1, sub = river_headline(s, RCAT)
    assert h1 == "El río está en valores normales y baja frente a Salto y Paysandú."
    assert sub.startswith(
        "Ninguna de las reglas con dato al día llegó a su cota de alerta. Frente a Bella Unión no hay dato al día. "
    )
    assert "Ninguna regla de las que miden enfrente" not in sub


def test_headline_city_with_no_status_counts_as_missing():
    s = sts(mc=("baja", 50, -0.1, False), con=("baja", 50, -0.1, False))  # Colón has no status at all
    sub = river_headline(s, RCAT)[1]
    assert sub.startswith("Ninguna de las reglas con dato al día llegó a su cota de alerta. "
                          "Frente a Paysandú no hay dato al día.")


def test_headline_missing_city_said_even_with_an_alert():
    s = sts(mc=("sube", 95, 0.2, True), con=("baja", 50, -0.1, False))
    h1, sub = river_headline(s, RCAT)
    assert h1 == "El río pasó la cota de alerta frente a Bella Unión."
    assert sub.startswith("Frente a Paysandú no hay dato al día.")
    assert "Ninguna" not in sub


def test_headline_every_city_stale_keeps_the_no_data_h1():
    s = sts() + [rstatus(c, None, pct=None, above=None, stale=True) for c in ("mc", "con", "col")]
    assert river_headline(s, RCAT) == ("No hay datos al día del río frente a las ciudades uruguayas.", "")


def test_headline_stale_non_city_over_alert_ignored():
    s = sts(mc=("baja", 50, -0.1, False), pdl=("baja", True))
    s = [x if x.station_id != "pdl" else rstatus("pdl", "baja", above=True, stale=True) for x in s]
    assert "sobre alerta" not in river_headline(s, RCAT)[1]


def test_headline_unknown_country_falls_back():
    cat = RCAT + [rstation("xx", "Rara", 0, alert_m=3.0, country="PY")]
    s = sts(mc=("baja", 50, -0.1, False), pdl=("baja", False)) + [rstatus("xx", "baja", above=True)]
    sub = river_headline(s, cat)[1]
    assert "La única sobre alerta es Rara y también baja." in sub
    assert "del lado" not in sub and "del otro lado" not in sub
