import math
import random
from datetime import date, datetime, timedelta, timezone

from climauy.analysis.lag import (
    HELD_OUT_WINDOWS,
    analyze_lag,
    find_flood_events,
    run_production_lags,
)
from climauy.catalog import Station
from climauy.store import Obs, Store

START = date(2006, 1, 1)
END = date(2025, 12, 31)
# One flood inside each held-out window plus several elsewhere.
UPSTREAM_PEAKS = [
    date(2007, 5, 10), date(2009, 12, 10), date(2012, 6, 1), date(2014, 6, 20),
    date(2015, 12, 20), date(2017, 6, 1), date(2019, 1, 15), date(2021, 3, 1),
    date(2023, 11, 5),
]


def days(start=START, end=END):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def flood_series(peaks, amplitude=10000.0, sigma=8.0, base=1000.0, noise=20.0, seed=1,
                 start=START, end=END):
    """Flat base flow plus a Gaussian flood bump around each peak."""
    rng = random.Random(seed)
    out = {}
    for d in days(start, end):
        v = base + rng.gauss(0, noise)
        for p in peaks:
            v += amplitude * math.exp(-((d - p).days ** 2) / (2 * sigma**2))
        out[d] = v
    return out


def shifted(series, lag, noise=20.0, seed=2):
    """down(t) = up(t - lag) plus small noise."""
    rng = random.Random(seed)
    return {
        d: series[d - timedelta(days=lag)] + rng.gauss(0, noise)
        for d in series
        if d - timedelta(days=lag) in series
    }


def test_held_out_windows_are_fixed():
    assert HELD_OUT_WINDOWS == {
        "2009": (date(2009, 11, 1), date(2010, 1, 31)),
        "2015": (date(2015, 12, 1), date(2016, 1, 31)),
        "2023": (date(2023, 10, 1), date(2023, 12, 31)),
    }


def test_synthetic_shift_3_days_validated():
    up = flood_series(UPSTREAM_PEAKS)
    down = shifted(up, 3)
    r = analyze_lag(up, down, HELD_OUT_WINDOWS)
    assert r.lag_days_median == 3
    assert r.lag_days_range == (3, 3)
    assert len(r.per_event) == len(UPSTREAM_PEAKS)
    assert all(v == 3 for v in r.per_event.values())
    assert "2015-12-23" in r.per_event
    assert set(r.holdout) == {"2009", "2015", "2023"}
    h = r.holdout["2015"]
    assert h.event == "2015"
    assert h.observed_peak == date(2015, 12, 23)
    assert h.peak_error_days <= 1
    assert h.mae_model < h.mae_persistence
    assert r.validated, r.reason
    assert r.reason.startswith("validado")


def test_gaps_are_handled():
    up = flood_series(UPSTREAM_PEAKS)
    down = shifted(up, 3)
    rng = random.Random(7)
    # Drop about 5% of days on each side, but keep the true peaks.
    keep_up = set(UPSTREAM_PEAKS)
    keep_down = {p + timedelta(days=3) for p in UPSTREAM_PEAKS}
    up = {d: v for d, v in up.items() if d in keep_up or rng.random() > 0.05}
    down = {d: v for d, v in down.items() if d in keep_down or rng.random() > 0.05}
    r = analyze_lag(up, down, HELD_OUT_WINDOWS)
    assert r.lag_days_median == 3
    assert r.validated, r.reason


def test_noisy_not_validated_with_reason():
    up = flood_series(UPSTREAM_PEAKS)
    # Downstream floods unrelated to upstream ones.
    down = flood_series(
        [date(2008, 3, 1), date(2010, 1, 25), date(2013, 2, 1), date(2016, 1, 28),
         date(2018, 8, 1), date(2020, 5, 1), date(2023, 10, 5)],
        seed=9,
    )
    r = analyze_lag(up, down, HELD_OUT_WINDOWS)
    assert not r.validated
    assert r.reason.startswith("no validado")
    assert any(y in r.reason for y in ("2009", "2015", "2023"))
    assert "—" not in r.reason and " -- " not in r.reason


def test_too_few_events_not_validated():
    peaks = [date(2009, 12, 10), date(2015, 12, 20)]
    # Wide floods over a short span, so the 95th percentile sits inside them
    # and base-flow noise never counts as an event.
    up = flood_series(peaks, sigma=20.0, start=date(2009, 1, 1), end=date(2016, 6, 30))
    down = shifted(up, 3)
    r = analyze_lag(up, down, HELD_OUT_WINDOWS)
    assert len(r.per_event) == 2
    assert not r.validated
    assert "2 eventos" in r.reason


def test_no_events_gives_none_median():
    r = analyze_lag({}, {}, HELD_OUT_WINDOWS)
    assert r.lag_days_median is None
    assert r.lag_days_range is None
    assert r.per_event == {}
    assert not r.validated


def test_peak_error_is_absolute():
    # Model and persistence get the same downstream data; a late upstream
    # bump makes the predicted peak land 5 days early or late; either way
    # the error must be positive and fail the gate.
    up = flood_series(UPSTREAM_PEAKS)
    down = shifted(up, 3)
    # Move the 2015 downstream flood 6 days later than the shift implies.
    for d in list(down):
        if date(2015, 12, 1) <= d <= date(2016, 1, 31):
            down[d] = up.get(d - timedelta(days=9), down[d])
    r = analyze_lag(up, down, HELD_OUT_WINDOWS)
    assert r.holdout["2015"].peak_error_days >= 5
    assert not r.validated
    assert "en 2015 el pico se erró por" in r.reason


def test_events_separated_30_days():
    s = {d: 0.0 for d in days(date(2000, 1, 1), date(2000, 12, 31))}

    def bump(center, height):
        for k in range(-2, 3):
            s[center + timedelta(days=k)] = height - abs(k)

    bump(date(2000, 3, 1), 100.0)
    bump(date(2000, 3, 11), 120.0)  # 10 days later and larger: wins
    bump(date(2000, 6, 1), 90.0)  # 82 days after: kept
    bump(date(2000, 7, 11), 95.0)  # 40 days after June: kept
    events = find_flood_events(s, quantile=0.95, min_separation_days=30)
    assert events == [date(2000, 3, 11), date(2000, 6, 1), date(2000, 7, 11)]


def test_consecutive_days_form_one_episode():
    s = {d: 0.0 for d in days(date(2000, 1, 1), date(2000, 12, 31))}
    for k, v in enumerate([50, 60, 80, 70, 65, 55, 52, 51]):
        s[date(2000, 5, 1) + timedelta(days=k)] = float(v)
    assert find_flood_events(s, quantile=0.97, min_separation_days=1) == [date(2000, 5, 3)]


def _station(sid, sources):
    return Station(
        id=sid, name=sid, country="AR", river="Uruguay", lat=0.0, lon=0.0, river_km=None,
        km_source=None, sources=sources, represents=None, alert_m=None, evac_m=None,
        cota_source=None, order=1, tributary_of=None,
    )


def test_run_production_lags_uses_both_pairs(tmp_path):
    up = flood_series(UPSTREAM_PEAKS)
    down = shifted(up, 3)

    def rows(sid, variable, series):
        return [
            Obs(sid, variable, datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc), v, "ina")
            for d, v in series.items()
        ]

    catalog = [
        _station("ina_72", {"ina_level": "72", "ina_q": "921"}),
        _station("ina_77", {"ina_level": "77", "ina_q": "926"}),
        _station("ina_79", {"ina_level": "79", "ina_q": "928"}),
    ]
    with Store(tmp_path / "t.sqlite") as store:
        store.upsert_obs(rows("ina_72", "discharge", up) + rows("ina_77", "discharge", down))
        store.upsert_obs(rows("ina_72", "level", up) + rows("ina_79", "level", down))
        results = run_production_lags(store, catalog)
    assert [(r.upstream, r.downstream) for r in results] == [
        ("ina_72:discharge", "ina_77:discharge"),
        ("ina_72:level", "ina_79:level"),
    ]
    assert all(r.lag_days_median == 3 for r in results)
