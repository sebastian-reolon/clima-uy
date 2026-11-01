"""Parser for GloFAS river discharge forecasts (Open-Meteo flood API, JSON)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from climauy.parsers import to_float
from climauy.store import Forecast

_QUALIFIERS = {
    "median": "river_discharge_median",
    "p25": "river_discharge_p25",
    "p75": "river_discharge_p75",
    "min": "river_discharge_min",
    "max": "river_discharge_max",
}


def parse(js: bytes, station_id: str, fetched_utc: datetime) -> list[Forecast]:
    """Forecasts for days strictly after the UTC day of `fetched_utc`.

    The API also returns past days (and ensemble members, ignored here).
    """
    daily = json.loads(js.decode("utf-8"))["daily"]
    f = fetched_utc.astimezone(timezone.utc)
    issued = f.replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[Forecast] = []
    for i, day in enumerate(daily["time"]):
        valid = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        if valid <= issued:
            continue
        for qualifier, key in _QUALIFIERS.items():
            series = daily.get(key)
            v = to_float(series[i]) if series and i < len(series) else None
            if v is not None:
                out.append(Forecast("glofas", station_id, "discharge", issued, valid, qualifier, v))
    return out
