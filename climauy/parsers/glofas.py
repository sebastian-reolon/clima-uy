"""Parser for GloFAS river discharge forecasts (Open-Meteo flood API, JSON)."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone

from climauy.parsers import to_float
from climauy.store import Forecast

# Local midnight in Montevideo (UTC-3, no DST) as a UTC time of day.
LOCAL_MIDNIGHT_UTC = time(3, 0, tzinfo=timezone.utc)

_QUALIFIERS = {
    "median": "river_discharge_median",
    "p25": "river_discharge_p25",
    "p75": "river_discharge_p75",
    "min": "river_discharge_min",
    "max": "river_discharge_max",
}


def parse(js: bytes, station_id: str, fetched_utc: datetime) -> list[Forecast]:
    """Forecasts for the days after the Montevideo day of `fetched_utc`.

    GloFAS publishes no run time, so the issue stamp is the fetch time. Each
    daily value is stamped at local midnight (03:00Z; Montevideo has no DST),
    like INA's daily readings, so local_day(valid) is the API's own date. The
    API also returns past days (and ensemble members, ignored here).
    """
    daily = json.loads(js.decode("utf-8"))["daily"]
    # A naive datetime is taken as UTC, never as local time.
    issued = fetched_utc.replace(tzinfo=timezone.utc) if fetched_utc.tzinfo is None else fetched_utc.astimezone(timezone.utc)
    out: list[Forecast] = []
    for i, day in enumerate(daily["time"]):
        valid = datetime.combine(date.fromisoformat(day), LOCAL_MIDNIGHT_UTC)
        # 03:00Z of day D <= issued exactly when D is the issue's local day or earlier.
        if valid <= issued:
            continue
        for qualifier, key in _QUALIFIERS.items():
            series = daily.get(key)
            v = to_float(series[i]) if series and i < len(series) else None
            if v is not None:
                out.append(Forecast("glofas", station_id, "discharge", issued, valid, qualifier, v))
    return out
