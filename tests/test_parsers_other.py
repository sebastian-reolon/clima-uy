import gzip
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from climauy.parsers import cpc, ctm, glofas

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc


def _ctm_pdf() -> bytes:
    return gzip.open(FIX / "ctm" / "2026-11-01_caudales_niveles.pdf.gz").read()


def test_ctm_report_2026_11_01():
    dams, obs = ctm.parse_report(_ctm_pdf())
    assert len(dams) == 4
    last = next(d for d in dams if d.date_local == date(2026, 11, 1))
    assert (last.q_total, last.q_turb, last.q_vert) == (13158, 8294, 4864)
    assert last.vol_ita_pct == 59 and last.vol_machadinho_pct == 66
    t = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    level = [o for o in obs if o.station_id == "ina_72" and o.variable == "level" and o.time_utc == t]
    assert [o.value for o in level] == [7.66]
    assert level[0].source == "ctm" and level[0].quality == "ok"
    q = [o for o in obs if o.station_id == "ina_72" and o.variable == "discharge" and o.time_utc == t]
    assert [o.value for o in q] == [12197]
    # Salto Grande reservoir level maps to Salto Grande Arriba; Federación is not in the catalog.
    assert any(o.station_id == "ina_77" and o.value == 34.99 for o in obs)
    assert {o.station_id for o in obs} >= {"ina_67", "ina_79", "ina_61", "ina_65"}


def test_ctm_unknown_station_name_is_skipped_and_logged(caplog):
    text = (
        "Niveles Hora 07:00 (m)\n29/10/2026 30/10/2026\n"
        "Paso de los Libres 8.01 8.04\nPuerto Inventado 1.00 2.00\n"
    )
    with caplog.at_level(logging.WARNING, logger="climauy.parsers.ctm"):
        _, obs = ctm.parse_text(text)
    assert {o.station_id for o in obs} == {"ina_72"}
    assert len(obs) == 2
    assert "Puerto Inventado" in caplog.text


def _glofas(name):
    return gzip.open(FIX / "glofas" / f"{name}.json.gz").read()


def test_glofas_salto_forecast():
    fetched = datetime(2026, 11, 1, 21, 26, 40, tzinfo=UTC)
    fc = glofas.parse(_glofas("salto"), "ina_79", fetched)
    issued = datetime(2026, 11, 1, tzinfo=UTC)
    assert fc and all(f.valid_utc > issued and f.issued_utc == issued for f in fc)
    assert {f.qualifier for f in fc} == {"median", "p25", "p75", "min", "max"}
    assert all(f.issuer == "glofas" and f.station_id == "ina_79" and f.variable == "discharge" for f in fc)
    by_day = {}
    for f in fc:
        by_day.setdefault(f.valid_utc, {})[f.qualifier] = f.value
    for q in by_day.values():
        assert q["min"] <= q["p25"] <= q["median"] <= q["p75"] <= q["max"]
    assert min(by_day) == datetime(2026, 11, 2, tzinfo=UTC)


def test_glofas_uruguaiana_and_naive_fetch_day():
    fc = glofas.parse(_glofas("uruguaiana"), "ana_77150000", datetime(2026, 11, 3, 0, 5, tzinfo=UTC))
    assert min(f.valid_utc for f in fc) == datetime(2026, 11, 4, tzinfo=UTC)


def _cpc(name):
    return (FIX / "cpc" / name).read_text()


def test_cpc_oni_last_is_jas_2026_2_16():
    last = cpc.parse_oni(_cpc("oni.ascii.txt"))[-1]
    assert (last.name, last.period, last.value) == ("ONI", "2026-JAS", 2.16)


def test_cpc_roni_last_is_jas_2026_1_69():
    last = cpc.parse_roni(_cpc("RONI.ascii.txt"))[-1]
    assert (last.name, last.period, last.value) == ("RONI", "2026-JAS", 1.69)


def test_cpc_weekly_glued_negative_values():
    rows = cpc.parse_weekly(_cpc("wksst9120.for"))
    first, last = rows[0], rows[-1]
    assert (first.name, first.period, first.value) == ("NINO34_WEEKLY", "1981-09-02", -0.2)
    assert last.period == "2026-10-21" and last.value == 3.4


def test_cpc_fill_dropped():
    oni = "SEAS  YR   TOTAL   ANOM\n  DJF 1950  25.01  -1.32\n  JFM 1950  25.36  -99.9\n"
    roni = "SEAS  YR  ANOM\nDJF  1950 -99.9\nJFM  1950 -1.08\n"
    weekly = " 07OCT2026     26.1 5.2     29.0 4.1     30.0-99.9     29.9 1.3\n"
    assert [(v.period, v.value) for v in cpc.parse_oni(oni)] == [("1950-DJF", -1.32)]
    assert [(v.period, v.value) for v in cpc.parse_roni(roni)] == [("1950-JFM", -1.08)]
    assert cpc.parse_weekly(weekly) == []
