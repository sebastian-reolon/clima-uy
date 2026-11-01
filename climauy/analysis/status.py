"""Current state of each station: latest value, staleness, trend, percentiles."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Sequence

from ..catalog import Station
from ..store import Store
from ..timeutil import local_day
from .daily import BASELINE_START, family_daily, family_for, history_years, percentile_of, station_readings

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
    # Latest local day mean minus the mean 3 days earlier; None if stale or a day is missing.
    level_change_3d_m: float | None
    discharge_change_3d_pct: float | None
    trend: Literal["sube", "baja", "estable"] | None
    trend_basis: Literal["level", "discharge"] | None
    percentile_today: float | None
    percentile_3d_ago: float | None
    alert_percentile: float | None
    level_m: float | None
    above_alert: bool | None
    above_evac: bool | None
    history_years: float


def _is_stale(latest: datetime | None, now_utc: datetime) -> bool:
    return latest is None or now_utc - latest > STALE_AFTER


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


def _change(daily: dict[date, float], latest: datetime | None, stale: bool, pct: bool) -> float | None:
    """3-day change of daily means ending on the latest reading's local day."""
    if latest is None or stale:
        return None
    d = local_day(latest)
    now_v, old_v = daily.get(d), daily.get(d - timedelta(days=3))
    if now_v is None or old_v is None:
        return None
    if pct:
        return 100.0 * (now_v - old_v) / abs(old_v) if old_v else None
    return now_v - old_v


def _trend(change: float, stable_band: float) -> Literal["sube", "baja", "estable"]:
    # "Menos de" is read as: a change of exactly the band (0.10 m, 5%) is still
    # estable. Rounding absorbs float noise at the edge.
    if abs(round(change, 6)) <= stable_band:
        return "estable"
    return "sube" if change > 0 else "baja"


def _station_status(store: Store, station: Station, now_utc: datetime) -> StationStatus:
    # Each (station, variable) family's readings are loaded once.
    q_reads = station_readings(store, station, "discharge")
    l_reads = station_readings(store, station, "level")
    discharge_daily = family_daily(q_reads, family_for(station, "discharge"))
    level_daily = family_daily(l_reads, family_for(station, "level"))

    q_latest = q_reads[-1] if q_reads else None
    l_latest = l_reads[-1] if l_reads else None
    q_stale = _is_stale(q_latest.time_utc if q_latest else None, now_utc)
    l_stale = _is_stale(l_latest.time_utc if l_latest else None, now_utc)

    use_q = history_years(discharge_daily) >= MIN_HISTORY_YEARS
    if use_q and q_stale and not l_stale and history_years(level_daily) >= MIN_HISTORY_YEARS:
        # Discharge stopped reporting but the gauge still reads: fall back to
        # level so the station is not shown as dead while it is live.
        use_q = False
    variable: Literal["discharge", "level"] = "discharge" if use_q else "level"
    daily = discharge_daily if use_q else level_daily
    latest = q_latest if use_q else l_latest
    stale = q_stale if use_q else l_stale
    latest_time = latest.time_utc if latest else None
    years = history_years(daily)

    p_today = p_ago = None
    if latest is not None and not stale and years >= MIN_HISTORY_YEARS:
        p_today, p_ago = _percentiles(daily, local_day(latest.time_utc))

    # Level: own family's level series (INA when available, because the alert
    # levels are INA's); cotas only apply to INA-datum series.
    level_m = l_latest.value if (l_latest is not None and not l_stale) else None
    has_cota = "ina_level" in station.sources
    above_alert = above_evac = None
    if level_m is not None and has_cota:
        if station.alert_m is not None:
            above_alert = level_m >= station.alert_m
        if station.evac_m is not None:
            above_evac = level_m >= station.evac_m

    level_change = _change(level_daily, l_latest.time_utc if l_latest else None, l_stale, pct=False)
    q_change = _change(discharge_daily, q_latest.time_utc if q_latest else None, q_stale, pct=True)
    trend = basis = None
    if level_change is not None:
        trend, basis = _trend(level_change, LEVEL_STABLE_M), "level"
    elif q_change is not None:
        trend, basis = _trend(q_change / 100.0, DISCHARGE_STABLE_FRAC), "discharge"

    alert_pct = None
    if has_cota and station.alert_m is not None and history_years(level_daily) >= MIN_HISTORY_YEARS:
        alert_pct = percentile_of(station.alert_m, [v for d, v in level_daily.items() if d >= BASELINE_START])

    return StationStatus(
        station_id=station.id,
        variable_used=variable,
        latest_value=latest.value if latest else None,
        latest_time_utc=latest_time,
        stale=stale,
        stale_since=local_day(latest_time) if (stale and latest_time) else None,
        level_change_3d_m=level_change,
        discharge_change_3d_pct=q_change,
        trend=trend,
        trend_basis=basis,
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
