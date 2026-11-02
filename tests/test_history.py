from datetime import date, datetime, timezone

import pytest

from climauy.catalog import Station
from climauy.history import FAILURES_KEY, backfill
from climauy.store import Store

TODAY = date(2026, 11, 1)


def _station(sid, **sources):
    return Station(sid, sid, "UY", "r", 0.0, 0.0, None, None, sources, None, None, None, None, 0, None)


def _ana_xml(month="2015-12-01 00:00:00", prefix="Vazao"):
    cells = f"<{prefix}01>10,5</{prefix}01>"
    return (
        '<DataTable xmlns="http://MRCS/"><DocumentElement xmlns=""><SerieHistorica>'
        f"<NivelConsistencia>2</NivelConsistencia><DataHora>{month}</DataHora>{cells}"
        "</SerieHistorica></DocumentElement></DataTable>"
    ).encode()


INA_JSON = b'[{"timestart":"1990-01-01T03:00:00.000Z","valor":2.5},{"timestart":"1990-01-02T03:00:00.000Z","valor":2.6}]'


class Fake:
    def __init__(self, ana=None, ina=INA_JSON, fail=()):
        self.calls, self.ana, self.ina, self.fail = [], ana or _ana_xml(), ina, fail

    def __call__(self, url):
        self.calls.append(url)
        if any(f in url for f in self.fail):
            raise OSError("boom")
        return self.ina if "alerta.ina" in url else self.ana


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.sqlite") as s:
        yield s


def test_backfill_ina_one_call_per_series(store):
    fetch = Fake()
    res = backfill(store, [_station("ina_72", ina_level="72", ina_q="921")], fetch, today=TODAY)
    assert len(fetch.calls) == 2
    assert "series_id=72&timestart=1990-01-01&timeend=2026-11-02" in fetch.calls[0]
    assert "series_id=921" in fetch.calls[1]
    assert res["ina_72/level"] == 2 and res["ina_72/discharge"] == 2
    got = store.obs("ina_72", "level")
    assert got[0].time_utc == datetime(1990, 1, 1, 3, tzinfo=timezone.utc) and got[0].source == "ina"


def test_backfill_ana_chunks_5y(store):
    fetch = Fake()
    backfill(store, [_station("ana_1", ana="123")], fetch, today=TODAY)
    q = [u for u in fetch.calls if "tipoDados=3" in u]
    assert len(q) == 8 and len(fetch.calls) == 16
    assert "dataInicio=01/01/1990&dataFim=31/12/1994" in q[0]
    assert "dataInicio=01/01/2025&dataFim=01/11/2026" in q[-1]
    assert "codEstacao=123" in q[0] and "tipoDados=1" in fetch.calls[8]
    assert store.obs("ana_1", "discharge")[0].source == "ana_hist"


def test_backfill_resumes(store):
    cat = [_station("ana_1", ana="123")]
    backfill(store, cat, Fake(), today=TODAY)
    again = Fake()
    backfill(store, cat, again, today=TODAY)
    # Chunks ending less than 2 years ago are fetched again (ANA keeps consolidating them):
    # 2020-2024 ends 31/12/2024, under 2 years before 1/11/2026; plus the open 2025 chunk.
    assert len(again.calls) == 4
    assert sorted({u.split("dataInicio=")[1][:10] for u in again.calls}) == ["01/01/2020", "01/01/2025"]


def test_ina_series_always_refetched(store):
    cat = [_station("ina_1", ina_level="1")]
    backfill(store, cat, Fake(), today=TODAY)
    again = Fake()
    backfill(store, cat, again, today=TODAY)
    assert len(again.calls) == 1
    assert len(store.obs("ina_1", "level")) == 2  # idempotent upsert


def test_failures_are_counted_not_fatal(store):
    cat = [_station("ana_1", ana="123"), _station("ina_2", ina_level="2")]
    res = backfill(store, cat, Fake(fail=("tipoDados=1", "series_id=2")), today=TODAY)
    assert res[FAILURES_KEY] == 9
    assert res["ana_1/discharge"] == 8
    # failed chunks are not marked complete
    assert not store.chunk_done("ana", "123", "level", date(1990, 1, 1))
    assert store.chunk_done("ana", "123", "discharge", date(1990, 1, 1))


def test_chunk_freezes_only_two_years_after_it_ends(store):
    cat = [_station("ana_1", ana="123")]
    # 31/12/2024 + 2 years = 31/12/2026: still open that day, frozen the day after.
    backfill(store, cat, Fake(), today=date(2026, 12, 31))
    assert not store.chunk_done("ana", "123", "discharge", date(2020, 1, 1))
    assert store.chunk_done("ana", "123", "discharge", date(2015, 1, 1))
    backfill(store, cat, Fake(), today=date(2027, 1, 1))
    assert store.chunk_done("ana", "123", "discharge", date(2020, 1, 1))


def test_old_mark_on_a_recent_chunk_is_ignored(store):
    # A chunk marked complete under the old rule (ended in the past) is fetched until it is 2 years old.
    store.chunk_mark("ana", "123", "discharge", date(2020, 1, 1))
    again = Fake()
    backfill(store, [_station("ana_1", ana="123")], again, today=TODAY)
    assert any("tipoDados=3" in u and "dataInicio=01/01/2020" in u for u in again.calls)
