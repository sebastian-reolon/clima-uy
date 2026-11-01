"""Parsers for INA (Argentina) observation series and forecast runs (JSON)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from climauy.parsers import to_float
from climauy.store import Forecast, Obs

# 99.99 is a placeholder some INA series use for "no data".
_INA_FILL = 99.99


def _load(js: bytes):
    try:
        text = js.decode("utf-8")
    except UnicodeDecodeError:
        text = js.decode("latin-1")
    return json.loads(text)


def _time(s: str) -> datetime:
    """Parse an ISO timestamp with 'Z' (or an offset) into aware UTC."""
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _value(raw) -> float | None:
    v = to_float(raw)
    return None if v is None or v == _INA_FILL else v


def parse_observations(js: bytes, station_id: str, variable: str) -> list[Obs]:
    out: list[Obs] = []
    for rec in _load(js):
        v = _value(rec.get("valor"))
        if v is None or not rec.get("timestart"):
            continue
        out.append(Obs(station_id, variable, _time(rec["timestart"]), v, "ina"))
    return out


def parse_forecast_run(js: bytes, station_id: str, variable: str) -> list[Forecast]:
    run = _load(js)
    issued = _time(run["forecast_date"])
    out: list[Forecast] = []
    for series in run.get("series", []):
        for p in series.get("pronosticos", []):
            v = _value(p.get("valor"))
            if v is None or not p.get("timestart") or not p.get("qualifier"):
                continue
            out.append(Forecast("ina", station_id, variable, issued, _time(p["timestart"]), p["qualifier"], v))
    return out
