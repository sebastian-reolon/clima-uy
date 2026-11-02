"""Seasonal series and past-flood comparison (Oct 1 to Mar 31 seasons)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from ..catalog import Station, load_catalog
from ..store import Store
from .daily import station_daily

SEASONS: dict[str, int] = {"2009-10": 2009, "2015-16": 2015, "2023-24": 2023, "2026-27": 2026}


@dataclass(frozen=True)
class FloodSummary:
    peak_value: float
    peak_date: date
    # Days whose daily mean level reached or exceeded the official alert level
    # (>=, as the station status); None when
    # the station has no INA level series or no alert level.
    days_above_alert: int | None
    # Variable the summary is based on ("level" or "discharge").
    variable: str


def _station(catalog: Sequence[Station] | None, station_id: str) -> Station:
    for s in catalog if catalog is not None else load_catalog():
        if s.id == station_id:
            return s
    raise KeyError(station_id)


def _season_days(daily: dict[date, float], season_start: date) -> list[tuple[date, float]]:
    end = date(season_start.year + 1, 3, 31)
    return sorted((d, v) for d, v in daily.items() if season_start <= d <= end)


def season_series(
    store: Store, station_id: str, variable: str, season_start: date,
    catalog: Sequence[Station] | None = None,
) -> list[tuple[int, float]]:
    """(days since season_start, daily mean) from season_start through Mar 31 of the next year."""
    daily = station_daily(store, _station(catalog, station_id), variable)
    return [((d - season_start).days, v) for d, v in _season_days(daily, season_start)]


def compare_floods(
    store: Store, station_ids: list[str], catalog: Sequence[Station] | None = None,
) -> dict[str, dict[str, FloodSummary]]:
    out: dict[str, dict[str, FloodSummary]] = {}
    for sid in station_ids:
        station = _station(catalog, sid)
        level = station_daily(store, station, "level")
        variable, daily = "level", level
        if not level and "ina_level" not in station.sources:
            discharge = station_daily(store, station, "discharge")
            if discharge:
                variable, daily = "discharge", discharge
        has_alert = variable == "level" and "ina_level" in station.sources and station.alert_m is not None
        seasons: dict[str, FloodSummary] = {}
        for name, year in SEASONS.items():
            days = _season_days(daily, date(year, 10, 1))
            if not days:
                continue
            peak_date, peak = max(days, key=lambda dv: (dv[1], -dv[0].toordinal()))
            above = sum(1 for _, v in days if v >= station.alert_m) if has_alert else None
            seasons[name] = FloodSummary(peak, peak_date, above, variable)
        out[sid] = seasons
    return out
