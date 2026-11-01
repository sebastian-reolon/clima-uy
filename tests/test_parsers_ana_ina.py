from datetime import datetime, timedelta, timezone
from pathlib import Path

from climauy.parsers import flag_outliers, ana, ina
from climauy.store import Obs

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc


def _telemetry_xml(rows):
    """Build a minimal ANA telemetry document from (time, vazao, nivel, chuva) tuples."""
    body = ""
    for i, (t, q, n, c) in enumerate(rows):
        body += (
            f'<DadosHidrometereologicos diffgr:id="D{i}" msdata:rowOrder="{i}">'
            f"<CodEstacao>1</CodEstacao><DataHora>{t} </DataHora>"
            f"<Vazao>{q}</Vazao><Nivel>{n}</Nivel><Chuva>{c}</Chuva>"
            "</DadosHidrometereologicos>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?><DataTable xmlns="http://MRCS/">'
        '<diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata" '
        'xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">'
        f'<DocumentElement xmlns="">{body}</DocumentElement></diffgr:diffgram></DataTable>'
    ).encode("utf-8")


def _history_xml(rows):
    """rows: (consistency, 'YYYY-MM-01 00:00:00', prefix, {day: text})."""
    body = ""
    for i, (cons, month, prefix, days) in enumerate(rows):
        cells = "".join(
            f"<{prefix}{d:02d}>{days[d]}</{prefix}{d:02d}>"
            if d in days
            else f"<{prefix}{d:02d} />"
            for d in range(1, 32)
        )
        body += (
            f'<SerieHistorica diffgr:id="S{i}" msdata:rowOrder="{i}">'
            f"<EstacaoCodigo>1</EstacaoCodigo><NivelConsistencia>{cons}</NivelConsistencia>"
            f"<DataHora>{month}</DataHora>{cells}</SerieHistorica>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?><DataTable xmlns="http://MRCS/">'
        '<diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata" '
        'xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">'
        f'<DocumentElement xmlns="">{body}</DocumentElement></diffgr:diffgram></DataTable>'
    ).encode("utf-8")


# ---------------------------------------------------------------- ANA telemetry

def test_ana_telemetry_uruguaiana():
    obs = ana.parse_telemetry((FIX / "ana/telemetry_77150000_uruguaiana.xml").read_bytes(), "ana_77150000")
    assert len(obs) > 100
    assert {o.variable for o in obs} >= {"discharge", "level", "rain"}
    assert all(o.source == "ana" and o.station_id == "ana_77150000" for o in obs)
    assert all(o.time_utc.tzinfo is not None and o.time_utc.utcoffset() == timedelta(0) for o in obs)
    levels = [o.value for o in obs if o.variable == "level"]
    assert levels and all(0 < v < 20 for v in levels)
    # 2026-11-01 17:30 Brasilia (UTC-3) -> 20:30 UTC, Nivel 798.80 cm -> 7.988 m
    first = [o for o in obs if o.variable == "level" and o.time_utc == datetime(2026, 11, 1, 20, 30, tzinfo=UTC)]
    assert len(first) == 1 and abs(first[0].value - 7.988) < 1e-9


def test_ana_empty_station_returns_empty():
    xml = (FIX / "ana/telemetry_78500000_concordia_empty.xml").read_bytes()
    assert ana.parse_telemetry(xml, "ana_78500000") == []


def test_ana_fill_values_dropped():
    xml = _telemetry_xml([
        ("2026-11-01 10:00:00", "", "-999", "0.00"),
        ("2026-11-01 10:15:00", "-999", "", ""),
        ("2026-11-01 10:30:00", "100.5", "250,0", "nan"),
    ])
    obs = ana.parse_telemetry(xml, "ana_1")
    got = {(o.time_utc.strftime("%H:%M"), o.variable): o.value for o in obs}
    assert got == {("13:00", "rain"): 0.0, ("13:30", "discharge"): 100.5, ("13:30", "level"): 2.5}


# ---------------------------------------------------------------- ANA history

def test_ana_history_daily_expansion():
    xml = (FIX / "ana/history_77150000_vazao_2015.xml").read_bytes()
    obs = ana.parse_history(xml, "ana_77150000", "discharge")
    dec = [o for o in obs if o.time_utc.astimezone(timezone(timedelta(hours=-3))).month == 12
           and o.time_utc.astimezone(timezone(timedelta(hours=-3))).year == 2015]
    assert len(dec) == 31
    assert all(o.variable == "discharge" and o.source == "ana" for o in obs)
    # day 1 at 00:00 Brasilia == 03:00 UTC
    assert min(o.time_utc for o in dec) == datetime(2015, 12, 1, 3, 0, tzinfo=UTC)
    nov = [o for o in obs if o.time_utc.month == 11 and o.time_utc.year == 2015]
    assert len(nov) == 30  # day 31 is an empty cell and November has no 31st
    feb = [o for o in obs if o.time_utc.month == 2 and o.time_utc.year == 2016]
    assert len(feb) <= 29


def test_ana_history_month_with_30_days_does_not_invent_day_31():
    days = {d: "10.0" for d in range(1, 32)}  # source mistakenly fills day 31 in a 30-day month
    xml = _history_xml([(2, "2015-11-01 00:00:00", "Vazao", days)])
    obs = ana.parse_history(xml, "ana_1", "discharge")
    assert len(obs) == 30


def test_ana_history_prefers_consisted():
    raw = {d: "1.0" for d in range(1, 4)}
    cons = {d: "2.0" for d in range(1, 4)}
    for rows in ([(1, "2015-12-01 00:00:00", "Vazao", raw), (2, "2015-12-01 00:00:00", "Vazao", cons)],
                 [(2, "2015-12-01 00:00:00", "Vazao", cons), (1, "2015-12-01 00:00:00", "Vazao", raw)]):
        obs = ana.parse_history(_history_xml(rows), "ana_1", "discharge")
        assert [o.value for o in obs] == [2.0, 2.0, 2.0]


def test_ana_history_cota_is_centimeters_to_meters():
    xml = _history_xml([(2, "2015-12-01 00:00:00", "Cota", {1: "798,8", 2: "-999"})])
    obs = ana.parse_history(xml, "ana_1", "level")
    assert [(o.variable, round(o.value, 3)) for o in obs] == [("level", 7.988)]


# ---------------------------------------------------------------- outliers

def _series(values, variable="discharge", sid="ana_1", source="ana", step=timedelta(hours=1)):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    return [Obs(sid, variable, t0 + step * i, v, source) for i, v in enumerate(values)]


def test_outlier_421_flagged():
    vals = [13800 + (i % 5) * 20 for i in range(40)]
    vals[20] = 421
    out = flag_outliers(_series(vals))
    assert [o.quality for o in out].count("suspect") == 1
    assert out[20].quality == "suspect" and out[20].value == 421
    assert all(o.quality == "ok" for i, o in enumerate(out) if i != 20)


def test_outlier_at_series_edge_and_independent_series():
    a = _series([421] + [13800] * 12)
    b = _series([100.0] * 13, sid="ana_2")
    out = flag_outliers(a + b)
    assert out[0].quality == "suspect"
    assert all(o.quality == "ok" for o in out[1:])
    # a different series' level must not influence the median
    lvl = _series([5.0] * 13, variable="level")
    out = flag_outliers(a + lvl)
    assert sum(o.quality == "suspect" for o in out) == 1


def test_outlier_leaves_rain_and_unsorted_input_alone():
    rain = _series([0.0, 0.0, 0.2, 30.0, 0.0, 0.2, 0.0, 0.4, 0.0], variable="rain")
    assert all(o.quality == "ok" for o in flag_outliers(rain))
    vals = [13800] * 20
    vals[10] = 421
    s = _series(vals)
    shuffled = s[::-1]
    out = flag_outliers(shuffled)
    assert [o.time_utc for o in out] == [o.time_utc for o in shuffled]  # input order kept
    assert [o.value for o in out if o.quality == "suspect"] == [421]


def test_daily_series_never_flagged_even_on_steep_limb():
    daily = timedelta(days=1)
    limb = [2.0, 2.2, 3.0, 4.5, 7.0, 9.0, 8.0, 6.0, 4.95, 4.0, 3.5]
    for variable in ("level", "discharge"):
        out = flag_outliers(_series(limb, variable=variable, step=daily))
        assert all(o.quality == "ok" for o in out)
    # even a blatant spike is left alone at daily resolution
    spike = [13800] * 10
    spike[5] = 421
    assert all(o.quality == "ok" for o in flag_outliers(_series(spike, step=daily)))


def test_low_stage_level_wiggle_not_flagged_but_big_jump_is():
    step = timedelta(minutes=15)
    vals = [0.4] * 20
    vals[10] = 0.7  # +75% but only 0.3 m
    out = flag_outliers(_series(vals, variable="level", step=step))
    assert all(o.quality == "ok" for o in out)
    vals[10] = 1.2  # +0.8 m
    out = flag_outliers(_series(vals, variable="level", step=step))
    assert [o.quality for o in out].count("suspect") == 1 and out[10].quality == "suspect"


def test_421_spike_still_flagged_at_15_minutes():
    vals = [13800 + (i % 5) * 20 for i in range(40)]
    vals[20] = 421
    out = flag_outliers(_series(vals, step=timedelta(minutes=15)))
    assert [i for i, o in enumerate(out) if o.quality == "suspect"] == [20]


# ---------------------------------------------------------------- INA

def test_ina_observations_72():
    obs = ina.parse_observations((FIX / "ina/obs_72_series72.json").read_bytes(), "ina_72", "level")
    assert len(obs) == 4
    assert all(o.source == "ina" and o.variable == "level" and o.station_id == "ina_72" for o in obs)
    assert all(o.time_utc.utcoffset() == timedelta(0) for o in obs)
    assert obs[0].time_utc == datetime(2026, 10, 29, 3, 0, tzinfo=UTC)
    assert 0 < obs[0].value < 20 and obs[0].value == 8.01
    q = ina.parse_observations((FIX / "ina/obs_72_series921.json").read_bytes(), "ina_72", "discharge")
    assert q[0].value == 13175 and q[0].variable == "discharge"


def test_ina_forecast_run_qualifiers():
    fc = ina.parse_forecast_run((FIX / "ina/forecast_cal704_series9646.json").read_bytes(), "ina_72", "level")
    assert len(fc) == 42
    assert {f.qualifier for f in fc} == {"main", "inferior", "superior"}
    assert all(f.issuer == "ina" and f.station_id == "ina_72" and f.variable == "level" for f in fc)
    issued = {f.issued_utc for f in fc}
    assert issued == {datetime(2026, 11, 1, 3, 0, tzinfo=UTC)}
    assert all(f.valid_utc >= f.issued_utc - timedelta(days=1) for f in fc)
    assert all(f.valid_utc.tzinfo is not None for f in fc)


def test_ina_comma_decimal_and_latin1():
    js = (
        '[{"series_id":1,"timestart":"2026-10-29T03:00:00.000Z","nombre":"Paraná ó","valor":"7,56"},'
        '{"series_id":1,"timestart":"2026-10-30T03:00:00.000Z","nombre":"x","valor":null},'
        '{"series_id":1,"timestart":"2026-10-31T03:00:00.000Z","nombre":"x","valor":"-999"},'
        '{"series_id":1,"timestart":"2026-11-01T03:00:00.000Z","nombre":"x","valor":99.99},'
        '{"series_id":1,"timestart":"2026-11-02T03:00:00.000Z","nombre":"x","valor":""}]'
    ).encode("latin-1")
    obs = ina.parse_observations(js, "ina_1", "level")
    assert [o.value for o in obs] == [7.56]
