"""History backfill from 1990: INA one call per series, ANA in 5-year chunks.

ANA chunks that ended more than 2 years ago are recorded in the store and skipped on later
runs: ANA keeps consolidating recent data, so a younger chunk (and every INA series, which
is open-ended) is fetched again on every run. Failures of one series or chunk are logged and
counted, never fatal. Daily consolidated data is stored without outlier flagging.
"""
from __future__ import annotations

import logging
import time
import urllib.request
from datetime import date, timedelta
from typing import Callable

from climauy.catalog import Station
from climauy.parsers import ana, ina
from climauy.store import Store

log = logging.getLogger(__name__)

INA_URL = (
    "https://alerta.ina.gob.ar/a5/obs/puntual/observaciones"
    "?tipo=puntual&series_id={series}&timestart={start:%Y-%m-%d}&timeend={end:%Y-%m-%d}"
)
ANA_URL = (
    "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/HidroSerieHistorica"
    "?codEstacao={code}&dataInicio={start:%d/%m/%Y}&dataFim={end:%d/%m/%Y}&tipoDados={tipo}&nivelConsistencia="
)
# tipoDados: 1 = cota (cm), 2 = rainfall, 3 = discharge. (2 returns "no data" for level stations.)
_ANA_TIPO = {"discharge": 3, "level": 1}
CHUNK_YEARS = 5
FREEZE_AFTER_YEARS = 2  # ANA consolidation lag: younger chunks may still change
FAILURES_KEY = "_failures"


def real_fetch(url: str, pause: float = 0.3, timeout: float = 120.0) -> bytes:
    """Polite HTTP GET used by the CLI: sleeps before every call."""
    time.sleep(pause)
    req = urllib.request.Request(url, headers={"User-Agent": "clima-uy-backfill"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


def _ana_chunks(since: date, today: date):
    """Yield (start, end, final) 5-year chunks; the last one is clipped to today.

    A chunk is final once it ended more than FREEZE_AFTER_YEARS before today.
    """
    year = since.year
    cutoff = _years_before(today, FREEZE_AFTER_YEARS)
    while date(year, 1, 1) <= today:
        start = max(since, date(year, 1, 1))
        full_end = date(year + CHUNK_YEARS - 1, 12, 31)
        yield start, min(full_end, today), full_end < cutoff
        year += CHUNK_YEARS


def backfill(
    store: Store,
    catalog: list[Station],
    fetch: Callable[[str], bytes],
    since: date = date(1990, 1, 1),
    today: date | None = None,
) -> dict[str, int]:
    """Backfill daily history; returns rows written per "station_id/variable".

    The special key "_failures" counts series or chunks that failed (logged, not raised).
    """
    today = today or date.today()
    rows: dict[str, int] = {}
    failures = 0

    def add(station_id: str, variable: str, n: int) -> None:
        key = f"{station_id}/{variable}"
        rows[key] = rows.get(key, 0) + n

    for st in catalog:
        for key, variable in (("ina_level", "level"), ("ina_q", "discharge")):
            series = st.sources.get(key)
            if not series:
                continue
            url = INA_URL.format(series=series, start=since, end=today + timedelta(days=1))
            try:
                obs = ina.parse_observations(fetch(url), st.id, variable)
                store.upsert_obs(obs)
            except Exception as exc:
                failures += 1
                log.warning("INA backfill failed for %s series %s: %s", st.id, series, exc)
                continue
            add(st.id, variable, len(obs))

        code = st.sources.get("ana")
        if not code:
            continue
        for variable, tipo in _ANA_TIPO.items():
            for start, end, final in _ana_chunks(since, today):
                # A mark on a chunk that is not final yet (older rule) does not skip it.
                if final and store.chunk_done("ana", code, variable, start):
                    continue
                url = ANA_URL.format(code=code, start=start, end=end, tipo=tipo)
                try:
                    obs = ana.parse_history(fetch(url), st.id, variable)
                    store.upsert_obs(obs)
                except Exception as exc:
                    failures += 1
                    log.warning("ANA backfill failed for %s %s from %s: %s", code, variable, start, exc)
                    continue
                add(st.id, variable, len(obs))
                if final:
                    store.chunk_mark("ana", code, variable, start)
    rows[FAILURES_KEY] = failures
    return rows
