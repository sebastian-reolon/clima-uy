import pytest

from climauy.catalog import Station, load_catalog
from climauy.config import Config


@pytest.fixture(scope="module")
def stations() -> list[Station]:
    return load_catalog()


def test_ids_unique(stations):
    ids = [s.id for s in stations]
    assert len(ids) == len(set(ids))


def test_order_is_downstream_and_unique(stations):
    orders = [s.order for s in stations]
    assert orders == sorted(orders)
    assert len(orders) == len(set(orders))


def test_uy_cities_present(stations):
    represented = {s.represents for s in stations if s.represents}
    assert {"Bella Unión", "Artigas", "Salto", "Paysandú", "Fray Bentos"} <= represented


def test_alerts_have_source(stations):
    for s in stations:
        if s.alert_m is not None:
            assert s.cota_source, s.id


def test_no_alert_on_ana_only_stations(stations):
    for s in stations:
        if "ana" in s.sources and "ina_level" not in s.sources:
            assert s.alert_m is None and s.evac_m is None, s.id


def test_tributaries_point_to_existing_station(stations):
    ids = {s.id for s in stations}
    for s in stations:
        if s.tributary_of is not None:
            assert s.tributary_of in ids, s.id


def test_paso_de_los_libres_has_both_sources(stations):
    libres = next(s for s in stations if s.sources.get("ina_level") == "72")
    assert libres.sources["ana"] == "77140000"
    assert libres.sources["ina_q"] == "921"
    assert libres.forecast_series == ["9646"]


def test_config_requires_env(monkeypatch):
    monkeypatch.setenv("CLIMAUY_ARCHIVE_DIR", "/a")
    monkeypatch.setenv("CLIMAUY_OUT", "/o")
    monkeypatch.delenv("CLIMAUY_DB", raising=False)
    with pytest.raises(KeyError, match="CLIMAUY_DB"):
        Config.from_env()
    monkeypatch.setenv("CLIMAUY_DB", "/d.sqlite")
    cfg = Config.from_env()
    assert str(cfg.db_path) == "/d.sqlite"


def test_same_point_stations_merged(stations):
    import math

    # Pairs that are intentionally separate: opposite banks, or two gauges of one dam.
    allowed = {
        frozenset({"ana_77150000", "ina_72"}),  # Uruguaiana vs Paso de los Libres
        frozenset({"ana_74720000", "ina_63"}),  # Porto Mauá (BR) vs Alba Posse (AR), twin towns
        frozenset({"ana_75900000", "ina_69"}),  # Itaqui (BR) vs Alvear (AR), opposite banks
        frozenset({"ina_77", "ina_78"}),  # Salto Grande upstream vs downstream gauge
    }

    def km(a, b):
        dy = (a.lat - b.lat) * 111.0
        dx = (a.lon - b.lon) * 111.0 * math.cos(math.radians((a.lat + b.lat) / 2))
        return math.hypot(dx, dy)

    for i, a in enumerate(stations):
        for b in stations[i + 1:]:
            if km(a, b) >= 2.0:
                continue
            if a.tributary_of == b.id or b.tributary_of == a.id:
                continue
            assert frozenset({a.id, b.id}) in allowed, (a.id, b.id)
