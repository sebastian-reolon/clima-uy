from datetime import date, datetime, timedelta, timezone

import pytest

from climauy.analysis.crecida import RiverMatrix, floor_tenth, river_matrix
from climauy.catalog import Station, river_km
from climauy.store import Obs, Store

END = date(2026, 11, 2)
START = END - timedelta(days=59)  # 4/9/2026, first day of the 60-day window
HIST_START = START - timedelta(days=11 * 366)  # 11 years of history before the window


def st(sid, order, lat, represents=None, sources=None, river="Uruguay"):
    return Station(
        id=sid, name=f"Estación {sid}", country="AR", river=river, lat=lat, lon=-57.0, river_km=None,
        km_source=None, sources=sources if sources is not None else {"ina_level": "1"},
        represents=represents, alert_m=None, evac_m=None, cota_source=None, order=order, tributary_of=None,
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.sqlite") as s:
        yield s


def put(store, sid, first, last, fn, variable="level", source="ina", skip=()):
    """One reading per local day (15:00Z is noon in Montevideo) from first to last inclusive."""
    rows, d = [], first
    while d <= last:
        if d not in skip:
            rows.append(Obs(sid, variable, datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc),
                            float(fn(d)), source))
        d += timedelta(days=1)
    store.upsert_obs(rows)


def history(d: date) -> float:
    """Values 0..99 spread evenly, so a value v ranks at about percentile v + 1."""
    return (d - HIST_START).days % 100


def put_station(store, sid, window_fn, variable="level", source="ina", skip=()):
    put(store, sid, HIST_START, START - timedelta(days=1), history, variable, source)
    put(store, sid, START, END, window_fn, variable, source, skip)


def idx(d: date) -> int:
    return (d - START).days


def test_empty_store(store):
    cat = [st("a", 1, -30.0), st("b", 2, -31.0)]
    m = river_matrix(store, cat, END)
    assert isinstance(m, RiverMatrix)
    assert m.days[0] == START and m.days[-1] == END and len(m.days) == 60
    assert m.rows == [] and m.skipped_gaps == []
    assert m.skipped_history == ["Estación a", "Estación b"]
    assert m.stations_total == 2
    assert m.km_max == pytest.approx(max(river_km(cat).values()))
    assert m.uy_km is None


def test_window_length_parameter(store):
    m = river_matrix(store, [st("a", 1, -30.0)], END, days=7)
    assert m.days == [END - timedelta(days=6 - i) for i in range(7)]


def test_rows_follow_river_order_with_km_city_and_uy_start(store):
    cat = [st("b", 2, -31.0, represents="Bella Unión"), st("a", 1, -30.0),
           st("t", 3, -32.0, river="Cuareim"), st("c", 4, -33.0, represents="Salto")]
    for sid in ("a", "b", "c", "t"):
        put_station(store, sid, lambda d: 10)
    m = river_matrix(store, cat, END)
    km = river_km(cat)
    assert [r.station_id for r in m.rows] == ["a", "b", "c"]  # tributaries stay out
    assert [r.km for r in m.rows] == [km["a"], km["b"], km["c"]]
    assert [r.city for r in m.rows] == [None, "Bella Unión", "Salto"]
    assert m.rows[0].name == "Estación a" and m.rows[0].variable == "level"
    assert m.uy_km == km["b"]
    assert m.stations_total == 3


def test_twin_gauge_is_left_out_like_the_profile(store):
    cat = [st("arriba", 1, -30.0), st("abajo", 2, -30.0), st("c", 3, -31.0)]
    for sid in ("arriba", "abajo", "c"):
        put_station(store, sid, lambda d: 10)
    m = river_matrix(store, cat, END)
    assert [r.station_id for r in m.rows] == ["arriba", "c"]
    assert m.stations_total == 2


def test_percentile_reference_excludes_the_window(store):
    # The window climbs above every past value: against the past alone every day is percentile 100.
    # Ranked against the window too, the early days would rank lower.
    put_station(store, "a", lambda d: 200 + idx(d))
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.percentiles == [100.0] * 60
    assert row.values[0] == 200.0 and row.values[-1] == 259.0


def test_percentile_is_the_weak_share_of_past_days(store):
    put_station(store, "a", lambda d: 49)
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    past = [history(HIST_START + timedelta(days=i)) for i in range((START - HIST_START).days)]
    assert row.percentiles[0] == floor_tenth(100.0 * sum(1 for v in past if v <= 49) / len(past))


def test_reference_starts_in_1990(store):
    # Readings before 1990 are not history: 1989 at 1000 would otherwise make every window day low.
    put(store, "a", date(1989, 1, 1), date(1989, 12, 31), lambda d: 1000)
    put_station(store, "a", lambda d: 150)
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.percentiles[0] == 100.0


def test_missing_day_is_blank(store):
    gap = START + timedelta(days=10)
    put_station(store, "a", lambda d: 10, skip={gap})
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.values[10] is None and row.percentiles[10] is None
    assert row.values[9] == 10.0 and row.percentiles[9] is not None


def test_coverage_filter_at_80_percent(store):
    window = [START + timedelta(days=i) for i in range(60)]
    put_station(store, "ok", lambda d: 10, skip=set(window[:12]))  # 48 of 60 days: kept
    put_station(store, "low", lambda d: 10, skip=set(window[:13]))  # 47 of 60: skipped
    cat = [st("ok", 1, -30.0), st("low", 2, -31.0)]
    m = river_matrix(store, cat, END)
    assert [r.station_id for r in m.rows] == ["ok"]
    assert m.skipped_gaps == ["Estación low"] and m.skipped_history == []


def test_station_without_ten_years_is_listed_apart(store):
    put(store, "young", START - timedelta(days=9 * 365), END, lambda d: 10)
    m = river_matrix(store, [st("young", 1, -30.0)], END)
    assert m.rows == [] and m.skipped_gaps == []
    assert m.skipped_history == ["Estación young"]
    assert m.stations_total == 1


def test_kept_and_both_skipped_groups_add_up_to_the_total(store):
    window = [START + timedelta(days=i) for i in range(60)]
    put_station(store, "ok", lambda d: 10)
    put_station(store, "gaps", lambda d: 10, skip=set(window[:20]))
    put(store, "young", START - timedelta(days=365), END, lambda d: 10)
    cat = [st("ok", 1, -30.0), st("gaps", 2, -31.0), st("young", 3, -32.0), st("none", 4, -33.0, sources={})]
    m = river_matrix(store, cat, END)
    assert m.skipped_gaps == ["Estación gaps"]
    assert m.skipped_history == ["Estación young", "Estación none"]
    assert len(m.rows) + len(m.skipped_gaps) + len(m.skipped_history) == m.stations_total == 4


def test_discharge_wins_a_tie_in_coverage(store):
    sources = {"ina_level": "1", "ina_q": "2"}
    put_station(store, "a", lambda d: 10, variable="level")
    put_station(store, "a", lambda d: 10, variable="discharge")
    row = river_matrix(store, [st("a", 1, -30.0, sources=sources)], END).rows[0]
    assert row.variable == "discharge"


def test_more_window_days_beat_discharge(store):
    sources = {"ina_level": "1", "ina_q": "2"}
    put_station(store, "a", lambda d: 10, variable="level")
    put_station(store, "a", lambda d: 10, variable="discharge", skip={START})
    row = river_matrix(store, [st("a", 1, -30.0, sources=sources)], END).rows[0]
    assert row.variable == "level"


def test_short_discharge_history_falls_back_to_level(store):
    sources = {"ina_level": "1", "ina_q": "2"}
    put_station(store, "a", lambda d: 10, variable="level")
    put(store, "a", START - timedelta(days=365), END, lambda d: 10, variable="discharge")
    row = river_matrix(store, [st("a", 1, -30.0, sources=sources)], END).rows[0]
    assert row.variable == "level"


def test_family_is_not_mixed(store):
    # An INA station reads its INA family only: ANA readings of the same gauge are ignored.
    put_station(store, "a", lambda d: 10)
    put(store, "a", START, END, lambda d: 500, source="ana")
    row = river_matrix(store, [st("a", 1, -30.0, sources={"ina_level": "1", "ana": "9"})], END).rows[0]
    assert set(row.values) == {10.0}


def _spikes(spikes: dict[int, float], base: float = 10):
    return lambda d: spikes.get(idx(d), base)


def test_peak_is_the_highest_day_within_five_days(store):
    put_station(store, "a", _spikes({30: 95, 27: 60, 36: 96}))
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    # 30 is highest from 25 to 35; 36 is highest from 31 to 41. 27 sits within 5 days of a higher one.
    assert row.peaks == [30, 36]


def test_peak_needs_percentile_90(store):
    put_station(store, "a", _spikes({30: 80}))
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.percentiles[30] < 90
    assert row.peaks == []


def test_tied_highest_days_are_not_peaks(store):
    put_station(store, "a", _spikes({30: 95, 33: 95}))
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.peaks == []


def test_tie_outside_five_days_does_not_count(store):
    put_station(store, "a", _spikes({30: 95, 36: 95}))
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.peaks == [30, 36]


def test_peaks_need_five_days_either_side_inside_the_window(store):
    put_station(store, "a", _spikes({4: 95, 55: 95}))
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.peaks == []
    put_station(store, "b", _spikes({5: 95, 54: 95}))
    row = river_matrix(store, [st("b", 1, -30.0)], END).rows[0]
    assert row.peaks == [5, 54]


def test_peak_needs_data_on_eight_of_its_ten_neighbours(store):
    two = {START + timedelta(days=d) for d in (26, 33)}
    put_station(store, "a", _spikes({30: 95}), skip=two)
    assert river_matrix(store, [st("a", 1, -30.0)], END).rows[0].peaks == [30]
    three = two | {START + timedelta(days=28)}
    put_station(store, "b", _spikes({30: 95}), skip=three)
    assert river_matrix(store, [st("b", 1, -30.0)], END).rows[0].peaks == []


def test_peak_ignores_missing_neighbours(store):
    put_station(store, "a", _spikes({30: 95}), skip={START + timedelta(days=31)})
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    assert row.peaks == [30]


def test_floor_tenth_never_rounds_up():
    assert floor_tenth(89.96) == 89.9
    assert floor_tenth(100 * 899 / 1000) == 89.9  # an exact tenth survives float noise
    assert floor_tenth(100 * 2249 / 2500) == 89.9
    assert floor_tenth(99.99) == 99.9
    assert floor_tenth(100.0) == 100.0
    assert floor_tenth(0.0) == 0.0


def test_percentiles_are_truncated_to_a_tenth(store):
    put_station(store, "a", lambda d: 49)
    row = river_matrix(store, [st("a", 1, -30.0)], END).rows[0]
    past = [history(HIST_START + timedelta(days=i)) for i in range((START - HIST_START).days)]
    exact = 100.0 * sum(1 for v in past if v <= 49) / len(past)
    assert exact != round(exact, 1)
    assert row.percentiles[0] == floor_tenth(exact)


def test_last_day_is_marked_partial_when_asked(store):
    assert river_matrix(store, [st("a", 1, -30.0)], END).partial_last is False
    assert river_matrix(store, [st("a", 1, -30.0)], END, partial_last=True).partial_last is True
