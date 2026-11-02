from datetime import date, datetime, timedelta, timezone

import pytest

from climauy.store import DamDay, Forecast, IndexValue, Obs, Store

T0 = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.db") as s:
        yield s


def mk(i, value=1.0, source="ina", quality="ok", station="s1"):
    return Obs(station, "level", T0 + timedelta(minutes=15 * i), value, source, quality)


def test_upsert_is_idempotent(store):
    rows = [mk(i, float(i)) for i in range(3)]
    store.upsert_obs(rows)
    store.upsert_obs(rows)
    got = store.obs("s1", "level")
    assert len(got) == 3
    assert got == rows
    assert got[0].time_utc.tzinfo is not None


def test_upsert_replaces_value_on_same_key(store):
    store.upsert_obs([mk(0, 1.0)])
    store.upsert_obs([mk(0, 9.5)])
    got = store.obs("s1", "level")
    assert [o.value for o in got] == [9.5]


def test_obs_key_includes_source(store):
    store.upsert_obs([mk(0, 1.0, source="ana"), mk(0, 2.0, source="ina")])
    assert len(store.obs("s1", "level")) == 2


def test_suspect_excluded_by_default(store):
    store.upsert_obs([mk(0), mk(1, 421.0, quality="suspect")])
    assert len(store.obs("s1", "level")) == 1
    assert len(store.obs("s1", "level", include_suspect=True)) == 2


def test_obs_range_and_latest(store):
    store.upsert_obs([mk(i) for i in range(5)])
    got = store.obs("s1", "level", start_utc=mk(1).time_utc, end_utc=mk(3).time_utc)
    assert [o.time_utc for o in got] == [mk(1).time_utc, mk(2).time_utc, mk(3).time_utc]
    assert store.latest_obs_time("s1", "level") == mk(4).time_utc
    assert store.latest_obs_time("nope", "level") is None


def test_latest_obs_time_ignores_suspect(store):
    store.upsert_obs([mk(0), mk(1, quality="suspect")])
    assert store.latest_obs_time("s1", "level") == mk(0).time_utc


def test_naive_datetime_rejected(store):
    with pytest.raises(ValueError):
        store.upsert_obs([Obs("s1", "level", datetime(2026, 1, 1), 1.0, "ina")])


def fc(qualifier="main", value=1.0, issued=T0, valid=None, station="s1", issuer="ina"):
    return Forecast(issuer, station, "level", issued, valid or issued + timedelta(days=1), qualifier, value)


def test_forecast_key(store):
    store.upsert_forecasts([fc("main"), fc("inferior"), fc("superior")])
    store.upsert_forecasts([fc("main", 5.0)])
    got = store.forecasts("ina")
    assert len(got) == 3
    assert {f.qualifier: f.value for f in got}["main"] == 5.0
    # different issue time is a distinct row
    store.upsert_forecasts([fc("main", issued=T0 + timedelta(days=1))])
    assert len(store.forecasts("ina")) == 4


def test_forecasts_filter(store):
    store.upsert_forecasts([fc(station="a"), fc(station="b"), fc("median", issuer="glofas")])
    assert len(store.forecasts("ina")) == 2
    assert [f.station_id for f in store.forecasts("ina", station_id="a")] == ["a"]
    assert len(store.forecasts("glofas")) == 1


def test_dam_and_indices(store):
    d = DamDay(date(2026, 10, 31), 1.0, 2.0, 3.0, 55.5, None)
    assert store.upsert_dam([d]) == 1
    store.upsert_dam([d])
    assert store.dam_days() == [d]
    i = IndexValue("ONI", "2026-SON", 0.8)
    store.upsert_indices([i])
    store.upsert_indices([IndexValue("ONI", "2026-SON", 0.9)])
    assert store.indices("ONI") == [IndexValue("ONI", "2026-SON", 0.9)]


def test_file_ledger(store):
    assert store.ledger_get("a/b.gz") is None
    store.ledger_set("a/b.gz", "abc", "failed", "boom", 1)
    assert store.ledger_get("a/b.gz") == ("failed", "boom", 1)
    store.ledger_set("a/b.gz", "abc", "processed")
    assert store.ledger_get("a/b.gz") == ("processed", "", 0)


def test_obs_around_returns_padding_per_series(store):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [Obs("s", "level", t0 + timedelta(hours=i), float(i), "ina") for i in range(10)]
    rows.append(Obs("s", "level", t0 + timedelta(hours=5), 99.0, "ana"))
    store.upsert_obs(rows)
    got = store.obs_around("s", "level", "ina", t0 + timedelta(hours=4), t0 + timedelta(hours=5), 2)
    assert [o.value for o in got] == [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def test_persists_and_wal(tmp_path):
    p = tmp_path / "p.db"
    with Store(p) as s:
        s.upsert_obs([mk(0)])
        mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    with Store(p) as s:
        assert len(s.obs("s1", "level")) == 1


def test_store_creates_parent_dir_and_sets_pragmas(tmp_path):
    path = tmp_path / "new" / "dir" / "t.db"
    with Store(path) as s:
        assert path.exists()
        assert s._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert s._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert s._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 60000


def test_forget_forecast_source_only_touches_that_issuer(store):
    issued = T0
    store.upsert_forecasts([
        Forecast("glofas", "s1", "discharge", issued, issued + timedelta(days=1), "median", 1.0),
        Forecast("ina", "s1", "level", issued, issued + timedelta(days=1), "main", 2.0),
    ])
    store.ledger_set("glofas/2026-11-01/000000Z_salto.json.gz", "a", "processed")
    store.ledger_set("ina/2026-11-01/000000Z_obs_1_series1.json.gz", "b", "processed")
    store.ledger_set("glofasx/2026-11-01/000000Z_x.gz", "c", "ignored")
    files, rows = store.forget_forecast_source("glofas", "glofas")
    assert (files, rows) == (1, 1)
    assert store.forecasts("glofas") == [] and len(store.forecasts("ina")) == 1
    assert store.ledger_get("glofas/2026-11-01/000000Z_salto.json.gz") is None
    assert store.ledger_get("ina/2026-11-01/000000Z_obs_1_series1.json.gz") is not None
    assert store.ledger_get("glofasx/2026-11-01/000000Z_x.gz") is not None
