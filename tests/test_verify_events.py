from datetime import date, datetime, timedelta, timezone

import pytest

from climauy.analysis.events import compare_floods, season_series
from climauy.analysis.verify import verify_forecasts
from climauy.catalog import Station
from climauy.store import Forecast, Obs, Store


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def st(sid="s1", sources=None, alert=None):
    return Station(
        id=sid, name=sid, country="UY", river="Uruguay", lat=0.0, lon=0.0, river_km=None, km_source=None,
        sources=sources if sources is not None else {"ina_level": "1"}, represents=None,
        alert_m=alert, evac_m=None, cota_source=None, order=1, tributary_of=None,
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.sqlite") as s:
        yield s


def put_obs(store, day, value, sid="s1", source="ina", variable="level"):
    store.upsert_obs([Obs(sid, variable, utc(day.year, day.month, day.day, 15), value, source)])


def fc(issued, valid_day, value, qualifier="main", issuer="ina", sid="s1", variable="level"):
    return Forecast(issuer, sid, variable, issued, utc(valid_day.year, valid_day.month, valid_day.day, 15), qualifier, value)


def issue_runs(store, n_runs, obs_value=5.0, fc_value=5.0, leads=(1,)):
    start = date(2026, 1, 1)
    for r in range(n_runs):
        issued = utc(2026, 1, 1, 12) + timedelta(days=r)
        for lead in leads:
            day = issued.date() + timedelta(days=lead)
            put_obs(store, day, obs_value)
            store.upsert_forecasts([fc(issued, day, fc_value)])


def test_verify_not_enough_until_14_runs(store):
    cat = [st()]
    issue_runs(store, 13)
    r = verify_forecasts(store, "ina", "s1", "level", catalog=cat)
    assert r.n_runs == 13 and r.by_lead[1].n == 13 and not r.enough
    issue_runs(store, 14)
    r = verify_forecasts(store, "ina", "s1", "level", catalog=cat)
    assert r.n_runs == 14 and r.enough


def test_verify_lead_stats_known_values(store):
    cat = [st()]
    issued = utc(2026, 2, 1, 12)
    # run A and B at lead 1 (errors +1, -3), run A at lead 2 (error +3)
    issued_b = utc(2026, 2, 2, 12)
    put_obs(store, date(2026, 2, 2), 5.0)
    put_obs(store, date(2026, 2, 3), 5.0)
    store.upsert_forecasts([
        fc(issued, date(2026, 2, 2), 6.0),
        fc(issued, date(2026, 2, 3), 8.0),
        fc(issued_b, date(2026, 2, 3), 2.0),
        fc(issued, date(2026, 2, 1), 99.0),  # lead 0: ignored
    ])
    r = verify_forecasts(store, "ina", "s1", "level", catalog=cat)
    assert r.n_runs == 2
    assert r.by_lead[1].n == 2 and r.by_lead[1].mae == pytest.approx(2.0) and r.by_lead[1].bias == pytest.approx(-1.0)
    assert r.by_lead[2].n == 1 and r.by_lead[2].mae == pytest.approx(3.0) and r.by_lead[2].bias == pytest.approx(3.0)
    assert 0 not in r.by_lead


def test_verify_skips_missing_observation_days(store):
    issued = utc(2026, 2, 1, 12)
    store.upsert_forecasts([fc(issued, date(2026, 2, 2), 6.0)])
    r = verify_forecasts(store, "ina", "s1", "level", catalog=[st()])
    assert r.n_runs == 0 and r.by_lead == {} and not r.enough


def test_coverage_ina_band(store):
    for i, (obs, lo, hi) in enumerate([(5.0, 4.0, 6.0), (9.0, 4.0, 6.0), (6.0, 4.0, 6.0), (3.0, 4.0, 6.0)]):
        issued = utc(2026, 2, 1, 12) + timedelta(days=i)
        day = issued.date() + timedelta(days=1)
        put_obs(store, day, obs)
        store.upsert_forecasts([
            fc(issued, day, 5.0), fc(issued, day, lo, "inferior"), fc(issued, day, hi, "superior"),
        ])
    r = verify_forecasts(store, "ina", "s1", "level", catalog=[st()])
    assert r.by_lead[1].coverage == pytest.approx(0.5)  # 5.0 and 6.0 (edge inclusive) inside


def test_glofas_uses_median_other_station_obs_and_no_coverage(store):
    cat = [st("g1", {"glofas": "0,0"}), st("o1", {"ina_q": "1"})]
    issued = utc(2026, 2, 1, 12)
    put_obs(store, date(2026, 2, 2), 100.0, sid="o1", variable="discharge")
    store.upsert_forecasts([
        fc(issued, date(2026, 2, 2), 110.0, "median", "glofas", "g1", "discharge"),
        fc(issued, date(2026, 2, 2), 500.0, "max", "glofas", "g1", "discharge"),
    ])
    r = verify_forecasts(store, "glofas", "g1", "discharge", obs_station_id="o1", catalog=cat)
    assert r.by_lead[1].bias == pytest.approx(10.0) and r.by_lead[1].coverage is None


def test_season_series_alignment(store):
    cat = [st()]
    put_obs(store, date(2023, 10, 1), 1.0)
    put_obs(store, date(2023, 10, 3), 3.0)
    put_obs(store, date(2024, 3, 31), 9.0)
    put_obs(store, date(2024, 4, 1), 99.0)
    put_obs(store, date(2023, 9, 30), 99.0)
    got = season_series(store, "s1", "level", date(2023, 10, 1), catalog=cat)
    assert got == [(0, 1.0), (2, 3.0), (182, 9.0)]


def test_compare_floods_days_above_alert(store):
    cat = [st("s1", {"ina_level": "1"}, alert=7.0), st("q", {"ana": "9"}, alert=None)]
    for d, v in [(date(2015, 11, 1), 6.0), (date(2015, 11, 2), 8.0), (date(2015, 11, 3), 9.5),
                 (date(2015, 11, 4), 7.0), (date(2016, 1, 5), 7.5)]:
        put_obs(store, d, v)
    put_obs(store, date(2023, 12, 1), 3.0)
    put_obs(store, date(2015, 12, 1), 4.0, sid="q", source="ana_hist")
    put_obs(store, date(2015, 12, 2), 6.0, sid="q", source="ana_hist")
    res = compare_floods(store, ["s1", "q"], catalog=cat)
    s = res["s1"]["2015-16"]
    # 4/11 sits exactly at the alert (7,0): reaching it counts, as in the station status.
    assert (s.peak_value, s.peak_date, s.days_above_alert, s.variable) == (9.5, date(2015, 11, 3), 4, "level")
    assert res["s1"]["2023-24"].days_above_alert == 0
    assert "2009-10" not in res["s1"]
    q = res["q"]["2015-16"]
    assert (q.peak_value, q.peak_date, q.days_above_alert, q.variable) == (6.0, date(2015, 12, 2), None, "level")
