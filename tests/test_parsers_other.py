import gzip
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

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
    text = _ctm_text().replace(
        "Garruchos 8840 6312 5076 4474",
        "Garruchos 8840 6312 5076 4474\nPuerto Inventado 1 2 3 4",
    )
    with caplog.at_level(logging.WARNING, logger="climauy.parsers.ctm"):
        _, obs = ctm.parse_text(text)
    assert "Puerto Inventado" in caplog.text
    assert not any("Inventado" in o.station_id for o in obs)
    assert _ctm_obs_count() == len(obs)


def _ctm_obs_count():
    return len(ctm.parse_report(_ctm_pdf())[1])


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


def test_glofas_uruguaiana_skips_issue_day():
    fc = glofas.parse(_glofas("uruguaiana"), "ana_77150000", datetime(2026, 11, 3, 0, 5, tzinfo=UTC))
    assert min(f.valid_utc for f in fc) == datetime(2026, 11, 4, tzinfo=UTC)


def test_glofas_naive_fetched_is_taken_as_utc():
    js = _glofas("salto")
    naive = glofas.parse(js, "ina_79", datetime(2026, 11, 1, 21, 0))
    aware = glofas.parse(js, "ina_79", datetime(2026, 11, 1, 21, 0, tzinfo=UTC))
    assert naive == aware and naive


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


def _ctm_text():
    from pypdf import PdfReader
    import io
    return PdfReader(io.BytesIO(_ctm_pdf())).pages[0].extract_text()


def test_ctm_garbled_text_raises():
    text = _ctm_text()
    with pytest.raises(ValueError):
        ctm.parse_text("nothing useful here")
    with pytest.raises(ValueError, match="7 a 7"):
        ctm.parse_text(text.replace("7 a 7", "siete"))
    with pytest.raises(ValueError, match="level"):
        ctm.parse_text(text.replace("Niveles Hora 07:00", "Otra cosa"))


def test_ctm_reordered_blocks_parse_identically():
    import re
    text = _ctm_text()
    parts = re.split(r"(?m)^(?=Volumenes|Caudales|Salto Grande - Erogado|Niveles)", text)
    head, blocks = parts[0], parts[1:]
    reordered = head + "".join(b.rstrip("\n") + "\n" for b in reversed(blocks))
    assert reordered != text
    assert ctm.parse_text(reordered) == ctm.parse_text(text)


def test_ctm_row_with_wrong_value_count_warns(caplog):
    text = _ctm_text().replace("Garruchos 4474", "Garruchos 4474", 1).replace(
        "Garruchos 8840 6312 5076 4474", "Garruchos 8840 6312 5076")
    with caplog.at_level(logging.WARNING, logger="climauy.parsers.ctm"):
        ctm.parse_text(text)
    assert "Garruchos" in caplog.text and "expected 4" in caplog.text
