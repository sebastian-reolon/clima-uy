"""Daily aggregates and per-station series selection.

Datum rule: for each (station, variable) ONE source family is used for the
whole series, never a mix. INA and ANA gauges reference different datums
(about 0.4 m apart at Paso de los Libres), so combining them would create
fake jumps.

- INA family {"ina", "ctm"}: used when the station has an INA source for the
  variable. Per local day "ina" readings win, "ctm" fills days without any.
- ANA family {"ana_hist", "ana"}: otherwise. Per local day "ana_hist" wins,
  "ana" telemetry fills days without any.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Sequence

from ..catalog import Station
from ..store import Obs, Store
from ..timeutil import local_day

INA_FAMILY = ("ina", "ctm")  # in order of preference
ANA_FAMILY = ("ana_hist", "ana")
BASELINE_START = date(1990, 1, 1)
_INA_KEY = {"level": "ina_level", "discharge": "ina_q"}


def daily_means(obs: Sequence[Obs]) -> dict[date, float]:
    """Mean per America/Montevideo local day, ignoring suspect readings."""
    sums: dict[date, list[float]] = defaultdict(list)
    for o in obs:
        if o.quality != "ok":
            continue
        sums[local_day(o.time_utc)].append(o.value)
    return {d: sum(v) / len(v) for d, v in sums.items()}


def percentile_of(value: float, history: Sequence[float]) -> float:
    """Percentile (0-100), 'weak' method: share of history values <= value."""
    if not history:
        raise ValueError("empty history")
    return 100.0 * sum(1 for h in history if h <= value) / len(history)


def family_for(station: Station, variable: str) -> tuple[str, ...]:
    """Source family for this station and variable (never mixed)."""
    return INA_FAMILY if _INA_KEY[variable] in station.sources else ANA_FAMILY


def station_readings(store: Store, station: Station, variable: str) -> list[Obs]:
    """Non-suspect raw readings of the chosen family, ordered by time."""
    family = family_for(station, variable)
    rows = store.obs(station.id, variable)
    return [o for o in rows if o.source in family]


def family_daily(readings: Sequence[Obs], family: Sequence[str]) -> dict[date, float]:
    """Daily means from already-loaded readings, higher-priority source winning per day."""
    out: dict[date, float] = {}
    for src in reversed(family):  # lowest priority first, overwritten by better sources
        out.update(daily_means([o for o in readings if o.source == src]))
    return out


def station_daily(store: Store, station: Station, variable: str) -> dict[date, float]:
    return family_daily(station_readings(store, station, variable), family_for(station, variable))


def history_years(daily: dict[date, float]) -> float:
    """Coverage in years: distinct local days with a daily mean since 1990, over 365.25.

    Gaps do not count, so a 15-year span with 8 years of days is 8 years.
    """
    return sum(1 for d in daily if d >= BASELINE_START) / 365.25
