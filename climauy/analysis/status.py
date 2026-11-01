"""Current state of each station: latest value, staleness, trend, percentiles."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Sequence

from ..catalog import Station
from ..store import Store
from ..timeutil import local_day
from .daily import BASELINE_START, history_years, percentile_of, station_daily, station_readings

STALE_AFTER = timedelta(hours=36)
MIN_HISTORY_YEARS = 10.0
LEVEL_STABLE_M = 0.10
DISCHARGE_STABLE_FRAC = 0.05


@dataclass(frozen=True)
class StationStatus:
    station_id: str
    variable_used: Literal["discharge", "level"]
    latest_value: float | None
    latest_time_utc: datetime | None
    stale: bool
    stale_since: date | None
    # Level change in metres over 3 days (percent when only discharge exists).
    change_3d: float | None
    trend: Literal["sube", "baja", "estable"] | None
    percentile_today: float | None
    percentile_3d_ago: float | None
    alert_percentile: float | None
    level_m: float | None
    above_alert: bool | None
    above_evac: bool | None
    history_years: float


def _is_stale(latest: datetime | None, now_utc: datetime) -> bool:
    return latest is None or now_utc - latest > STALE_AFTER


def _series_stale(store: Store, station: Station, variable: str, now_utc: datetime) -> bool:
    readings = station_readings(store, station, variable)
    return _is_stale(readings[-1].time_utc if readings else None, now_utc)


def _percentiles(daily: dict[date, float], latest_day: date) -> tuple[float | None, float | None]:
    """Percentiles of the latest day and of 3 days before it.

    Both compare against the same reference: daily means from 1990-01-01 up to
    but excluding the day 3 days before the latest day, so neither value is
    ranked against itself or its own last three days.
    """
    ref_end = latest_day - timedelta(days=3)
    ref = [v for d, v in daily.items() if BASELINE_START <= d < ref_end]
    if not ref:
        return None, None
    today = daily.get(latest_day)
    ago = daily.get(ref_end)
    return (
        percentile_of(today, ref) if today is not None else None,
        percentile_of(ago, ref) if ago is not None else None,
    )


def _trend(change: float, stable_band: float) -> Literal["sube", "baja", "estable"]:
    # Round to absorb float noise at the band edge (0.1 m exactly is stable).
    if abs(round(change, 6)) <= stable_band:
        return "estable"
    return "sube" if change > 0 else "baja"


def _station_status(store: Store, station: Station, now_utc: datetime) -> StationStatus:
    discharge_daily = station_daily(store, station, "discharge")
    level_daily = station_daily(store, station, "level")
    use_q = history_years(discharge_daily) >= MIN_HISTORY_YEARS
    if use_q and _series_stale(store, station, "discharge", now_utc) and not _series_stale(
        store, station, "level", now_utc
    ) and history_years(level_daily) >= MIN_HISTORY_YEARS:
        # Discharge stopped reporting but the gauge still reads: fall back to
        # level so the station is not shown as dead while it is live.
        use_q = False
    variable: Literal["discharge", "level"] = "discharge" if use_q else "level"
    daily = discharge_daily if use_q else level_daily

    readings = station_readings(store, station, variable)
    latest = readings[-1] if readings else None
    latest_time = latest.time_utc if latest else None
    stale = _is_stale(latest_time, now_utc)
    years = history_years(daily)

    p_today = p_ago = None
    if latest is not None and not stale and years >= MIN_HISTORY_YEARS:
        p_today, p_ago = _percentiles(daily, local_day(latest.time_utc))

    # Level: always the level series of the station's own family (INA when
    # available, because the alert levels are INA's).
    level_reads = station_readings(store, station, "level")
    level_latest = level_reads[-1] if level_reads else None
    level_fresh = level_latest is not None and not _is_stale(level_latest.time_utc, now_utc)
    level_m = level_latest.value if level_fresh else None
    has_cota = "ina_level" in station.sources
    above_alert = above_evac = None
    if level_m is not None and has_cota:
        if station.alert_m is not None:
            above_alert = level_m >= station.alert_m
        if station.evac_m is not None:
            above_evac = level_m >= station.evac_m

    # Trend: fresh level data first, else fresh discharge (percent change).
    # Latest local day mean against the day mean 3 days earlier.
    change = trend = None
    if level_fresh:
        d = local_day(level_latest.time_utc)
        now_v, old_v = level_daily.get(d), level_daily.get(d - timedelta(days=3))
        if now_v is not None and old_v is not None:
            change = now_v - old_v
            trend = _trend(change, LEVEL_STABLE_M)
    elif variable == "discharge" and latest is not None and not stale:
        d = local_day(latest.time_utc)
        now_v, old_v = discharge_daily.get(d), discharge_daily.get(d - timedelta(days=3))
        if now_v is not None and old_v:
            change = 100.0 * (now_v - old_v) / abs(old_v)
            trend = _trend(change / 100.0, DISCHARGE_STABLE_FRAC)

    alert_pct = None
    if station.alert_m is not None and history_years(level_daily) >= MIN_HISTORY_YEARS:
        alert_pct = percentile_of(station.alert_m, [v for d, v in level_daily.items() if d >= BASELINE_START])

    return StationStatus(
        station_id=station.id,
        variable_used=variable,
        latest_value=latest.value if latest else None,
        latest_time_utc=latest_time,
        stale=stale,
        stale_since=local_day(latest_time) if (stale and latest_time) else None,
        change_3d=change,
        trend=trend,
        percentile_today=p_today,
        percentile_3d_ago=p_ago,
        alert_percentile=alert_pct,
        level_m=level_m,
        above_alert=above_alert,
        above_evac=above_evac,
        history_years=years,
    )


def compute_status(store: Store, catalog: Sequence[Station], now_utc: datetime) -> list[StationStatus]:
    return [_station_status(store, s, now_utc) for s in catalog]
