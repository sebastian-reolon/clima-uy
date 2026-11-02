from datetime import date, datetime, timedelta, timezone

import pytest

from climauy.analysis.daily import daily_means, percentile_of, station_daily, station_readings
from climauy.analysis.status import compute_status
from climauy.catalog import Station
from climauy.store import Obs, Store

NOW = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)


def st(sid="s1", sources=None, alert=None, evac=None):
    return Station(
        id=sid, name=sid, country="UY", river="Uruguay", lat=0.0, lon=0.0, river_km=None, km_source=None,
        sources=sources if sources is not None else {"ina_level": "1"}, represents=None,
        alert_m=alert, evac_m=evac, cota_source=None, order=1, tributary_of=None,
    )


def ob(t, v, source="ina", variable="level", sid="s1", quality="ok"):
    return Obs(sid, variable, t, v, source, quality)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.sqlite") as s:
        yield s


def put_daily(store, start, days, fn, source="ina", variable="level", sid="s1"):
    """One reading per day at 15:00Z."""
    store.upsert_obs([
        ob(utc(start.year, start.month, start.day, 15) + timedelta(days=i), fn(i), source, variable, sid)
        for i in range(days)
    ])


# --- pure helpers


def test_percentile_weak():
    assert percentile_of(3, [1, 2, 3, 4]) == 75.0


def test_daily_means_skip_suspect_and_average():
    rows = [ob(utc(2026, 3, 1, 12), 1.0), ob(utc(2026, 3, 1, 14), 3.0), ob(utc(2026, 3, 1, 15), 99.0, quality="suspect")]
    assert daily_means(rows) == {date(2026, 3, 1): 2.0}


def test_day_boundary_ina_0300z():
    # 03:00Z is midnight in Montevideo: belongs to that local day, not the previous one.
    got = daily_means([ob(utc(2026, 3, 2, 3), 5.0), ob(utc(2026, 3, 2, 2, 59), 1.0)])
    assert got == {date(2026, 3, 1): 1.0, date(2026, 3, 2): 5.0}


def test_ana_2330_brasilia_same_local_day():
    # 23:30 Brasilia = 02:30Z next day = 23:30 Montevideo, same local day.
    got = daily_means([ob(utc(2026, 3, 2, 2, 30), 4.0, "ana"), ob(utc(2026, 3, 1, 15), 2.0, "ana")])
    assert got == {date(2026, 3, 1): 3.0}


# --- family selection


def test_ina_over_ctm_same_day(store):
    s = st()
    store.upsert_obs([
        ob(utc(2026, 3, 1, 15), 2.0, "ina"), ob(utc(2026, 3, 1, 10), 9.0, "ctm"),
        ob(utc(2026, 3, 2, 10), 4.0, "ctm"),
    ])
    assert station_daily(store, s, "level") == {date(2026, 3, 1): 2.0, date(2026, 3, 2): 4.0}


def test_station_with_ina_ignores_ana(store):
    s = st(sources={"ina_level": "1", "ana": "x"})
    store.upsert_obs([ob(utc(2026, 3, 1, 15), 2.0, "ina"), ob(utc(2026, 3, 2, 15), 9.0, "ana"),
                      ob(utc(2026, 3, 3, 15), 9.0, "ana_hist")])
    assert station_daily(store, s, "level") == {date(2026, 3, 1): 2.0}
    assert [o.source for o in station_readings(store, s, "level")] == ["ina"]


def test_ana_hist_preferred_over_ana(store):
    s = st(sources={"ana": "x"})
    store.upsert_obs([
        ob(utc(2026, 3, 1, 15), 2.0, "ana_hist"), ob(utc(2026, 3, 1, 16), 8.0, "ana"),
        ob(utc(2026, 3, 2, 15), 4.0, "ana"),
    ])
    assert station_daily(store, s, "level") == {date(2026, 3, 1): 2.0, date(2026, 3, 2): 4.0}


def test_discharge_family_follows_ina_q(store):
    s = st(sources={"ina_level": "1", "ana": "x"})  # no ina_q: discharge comes from ANA
    store.upsert_obs([ob(utc(2026, 3, 1, 15), 100.0, "ina", "discharge"), ob(utc(2026, 3, 1, 15), 7.0, "ana", "discharge")])
    assert station_daily(store, s, "discharge") == {date(2026, 3, 1): 7.0}


# --- status


def test_stale_station_marked(store):
    s = st()
    last = NOW - timedelta(hours=50)
    store.upsert_obs([ob(last - timedelta(days=i), 3.0 + i) for i in range(5)])
    [r] = compute_status(store, [s], NOW)
    assert r.stale is True
    assert r.stale_since == date(2026, 10, 30)
    assert r.percentile_today is None
    assert r.trend is None and r.level_m is None and r.above_alert is None
    assert r.latest_value == 3.0


def test_station_without_data_is_stale(store):
    [r] = compute_status(store, [st()], NOW)
    assert r.stale and r.latest_value is None and r.stale_since is None


def test_percentile_today_long_history(store):
    s = st()
    # 12 years, level = day index (monotonic): latest is the maximum.
    n = 12 * 365
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc) - timedelta(days=n - 1)
    put_daily(store, start, n, lambda i: float(i))
    [r] = compute_status(store, [s], NOW)
    assert not r.stale
    assert r.history_years >= 10
    assert r.percentile_today == 100.0
    assert r.percentile_3d_ago == 100.0  # same reference, also above all of it
    assert r.variable_used == "level"


def test_short_history_no_percentile(store):
    n = 8 * 365
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc) - timedelta(days=n - 1)
    put_daily(store, start, n, lambda i: float(i))
    [r] = compute_status(store, [st()], NOW)
    assert r.history_years < 10
    assert r.percentile_today is None and r.percentile_3d_ago is None
    assert r.alert_percentile is None


def test_history_years_ignores_pre_1990():
    from climauy.analysis.daily import history_years

    d = {date(1985, 1, 1) + timedelta(days=i): 1.0 for i in range(10 * 365 + 10)}  # 1985..1995
    assert 5.0 < history_years(d) < 6.0  # only 1990-1995 counts


def test_trend_thresholds(store, tmp_path):
    for delta, expected in [(0.09, "estable"), (0.10, "estable"), (0.11, "sube"), (-0.10, "estable"), (-0.11, "baja"), (-0.09, "estable")]:
        with Store(tmp_path / f"t{delta}.sqlite") as s2:
            base = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)  # D-3 where D = Nov 1
            s2.upsert_obs([ob(base, 5.0), ob(base + timedelta(days=3), 5.0 + delta)])
            [r] = compute_status(s2, [st()], NOW)
            assert r.trend == expected, (delta, r.trend)
            assert r.level_change_3d_m == pytest.approx(delta)
            assert r.trend_basis == "level"
            assert r.discharge_change_3d_pct is None


def test_discharge_trend_percent_when_only_discharge(tmp_path):
    s = st(sources={"ana": "x"})
    n = 11 * 365
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc) - timedelta(days=n - 1)
    for q_new, expected in [(104.0, "estable"), (105.0, "estable"), (106.0, "sube"), (95.0, "estable"), (94.0, "baja")]:
        with Store(tmp_path / f"q{q_new}.sqlite") as s2:
            put_daily(s2, start, n, lambda i: q_new if i == n - 1 else 100.0, "ana_hist", "discharge")
            [r] = compute_status(s2, [s], NOW)
            assert r.variable_used == "discharge"
            assert r.level_m is None
            assert r.trend == expected, (q_new, r.trend)
            assert r.discharge_change_3d_pct == pytest.approx(q_new - 100.0)
            assert r.level_change_3d_m is None and r.trend_basis == "discharge"


def test_alert_flags(store):
    s = st(alert=7.5, evac=9.0)
    store.upsert_obs([ob(utc(2026, 11, 1, 15), 7.56)])
    [r] = compute_status(store, [s], NOW)
    assert r.level_m == 7.56
    assert r.above_alert is True and r.above_evac is False


def test_no_cota_no_flags(store):
    store.upsert_obs([ob(utc(2026, 11, 1, 15), 7.56)])
    [r] = compute_status(store, [st()], NOW)
    assert r.above_alert is None and r.above_evac is None


def test_ana_only_station_never_flags_cota(store):
    s = st(sources={"ana": "x"}, alert=7.5)
    store.upsert_obs([ob(utc(2026, 11, 1, 15), 7.56, "ana")])
    [r] = compute_status(store, [s], NOW)
    assert r.level_m == 7.56 and r.above_alert is None


def test_discharge_used_with_long_history(store):
    s = st(sources={"ina_level": "1", "ina_q": "2"})
    n = 11 * 365
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc) - timedelta(days=n - 1)
    put_daily(store, start, n, lambda i: float(i), variable="discharge")
    put_daily(store, start, n, lambda i: 1.0, variable="level")
    [r] = compute_status(store, [s], NOW)
    assert r.variable_used == "discharge"
    assert r.latest_value == n - 1
    assert r.percentile_today == 100.0
    assert r.trend == "estable"  # level flat
    assert r.alert_percentile is None  # station has no alert level


def test_dead_discharge_falls_back_to_live_level(store):
    s = st(sources={"ina_level": "1", "ina_q": "2"})
    n = 11 * 365
    end = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
    start = end - timedelta(days=n - 1)
    put_daily(store, start, n - 100, lambda i: float(i), variable="discharge")  # stops 100 days ago
    put_daily(store, start, n, lambda i: float(i), variable="level")
    [r] = compute_status(store, [s], NOW)
    assert r.variable_used == "level" and not r.stale
    assert r.percentile_today == 100.0


def test_percentile_non_extreme():
    assert percentile_of(2.5, [1, 2, 3, 4]) == 50.0
    assert percentile_of(0.5, [1, 2, 3, 4]) == 0.0


def test_alert_percentile_only_with_ina_level(store):
    n = 11 * 365
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc) - timedelta(days=n - 1)
    ana = st("a", sources={"ana": "x"}, alert=100.0)
    ina = st("i", sources={"ina_level": "1"}, alert=100.0)
    put_daily(store, start, n, lambda i: float(i % 200), "ana_hist", "level", "a")
    put_daily(store, start, n, lambda i: float(i % 200), "ina", "level", "i")
    ra, ri = compute_status(store, [ana, ina], NOW)
    assert ra.alert_percentile is None
    assert ri.alert_percentile == pytest.approx(50.5, abs=0.5)


def test_history_years_is_coverage_not_span(store):
    # 15-year span, only ~8 years of days (every other day): no percentile.
    n = 15 * 365
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc) - timedelta(days=n - 1)
    store.upsert_obs([ob(start + timedelta(days=i), 1.0) for i in range(0, n, 2)] + [ob(utc(2026, 11, 1, 15), 1.0)])
    [r] = compute_status(store, [st()], NOW)
    assert 7 < r.history_years < 8
    assert r.percentile_today is None


def test_trend_falls_back_to_discharge_when_level_day_missing(store):
    s = st(sources={"ina_level": "1", "ina_q": "2"})
    n = 11 * 365
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc) - timedelta(days=n - 1)
    put_daily(store, start, n, lambda i: 120.0 if i == n - 1 else 100.0, variable="discharge")
    # level is fresh but has no reading 3 days before the latest
    store.upsert_obs([ob(utc(2026, 11, 1, 15), 5.0), ob(utc(2026, 10, 25, 15), 4.0)])
    [r] = compute_status(store, [s], NOW)
    assert r.level_m == 5.0 and r.level_change_3d_m is None
    assert r.trend == "sube" and r.trend_basis == "discharge"
    assert r.discharge_change_3d_pct == pytest.approx(20.0)


# --- stale threshold by resolution, provenance, coverage start


@pytest.mark.parametrize("age_h, stale", [(40, False), (47.9, False), (48.1, True)])
def test_daily_gauge_stale_after_48h(store, age_h, stale):
    last = NOW - timedelta(hours=age_h)
    store.upsert_obs([ob(last - timedelta(days=i), 3.0) for i in range(10)])
    [r] = compute_status(store, [st()], NOW)
    assert r.stale is stale


@pytest.mark.parametrize("age_h, stale", [(35.9, False), (36.1, True), (40, True)])
def test_subdaily_gauge_stale_after_36h(store, age_h, stale):
    last = NOW - timedelta(hours=age_h)
    store.upsert_obs([ob(last - timedelta(hours=3 * i), 3.0) for i in range(20)])
    [r] = compute_status(store, [st()], NOW)
    assert r.stale is stale


def test_resolution_is_judged_on_recent_readings(store):
    # Years of daily history, then hourly telemetry: the recent hourly spacing decides (36 h).
    s = st(sources={"ana": "x"})
    put_daily(store, date(2020, 1, 1), 2000, lambda i: 3.0, source="ana_hist")
    last = NOW - timedelta(hours=40)
    store.upsert_obs([ob(last - timedelta(hours=i), 3.0, "ana") for i in range(40)])
    [r] = compute_status(store, [s], NOW)
    assert r.stale is True


def test_latest_source_is_the_latest_readings_source(store):
    store.upsert_obs([ob(utc(2026, 11, 1, 10), 5.0, "ina"), ob(utc(2026, 11, 1, 12), 5.1, "ctm")])
    [r] = compute_status(store, [st()], NOW)
    assert r.latest_time_utc == utc(2026, 11, 1, 12) and r.latest_source == "ctm"


def test_latest_source_none_without_data(store):
    [r] = compute_status(store, [st()], NOW)
    assert r.latest_source is None


def test_history_start_year_is_first_day_since_1990(store):
    put_daily(store, date(1985, 6, 1), 30, lambda i: 1.0)
    put_daily(store, date(2014, 3, 1), 30, lambda i: 1.0)
    [r] = compute_status(store, [st()], NOW)
    assert r.history_start_year == 2014


def test_history_start_year_none_without_data(store):
    [r] = compute_status(store, [st()], NOW)
    assert r.history_start_year is None
