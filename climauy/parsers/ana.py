"""Parsers for ANA (Brazil) telemetry and monthly historical series (XML, namespace http://MRCS/)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from climauy.parsers import to_float
from climauy.store import Obs
from climauy.timeutil import BRASILIA, parse_brasilia

_TELEMETRY_FIELDS = (("Vazao", "discharge", 1.0), ("Nivel", "level", 0.01), ("Chuva", "rain", 1.0))
_HISTORY_PREFIX = {"discharge": ("Vazao", 1.0), "level": ("Cota", 0.01)}


def _rows(xml: bytes, row_tag: str):
    """Yield dicts of child-tag -> text for each <row_tag> element, ignoring namespaces."""
    root = ET.fromstring(xml)
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == row_tag:
            yield {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in el}


def parse_telemetry(xml: bytes, station_id: str) -> list[Obs]:
    """Telemetry rows (Brasilia time). Nivel is in cm and is returned in metres."""
    out: list[Obs] = []
    for row in _rows(xml, "DadosHidrometereologicos"):
        try:
            t = parse_brasilia(row["DataHora"])
        except (KeyError, ValueError):
            continue
        for tag, variable, scale in _TELEMETRY_FIELDS:
            v = to_float(row.get(tag))
            if v is not None:
                out.append(Obs(station_id, variable, t, v * scale, "ana"))
    return out


def parse_history(xml: bytes, station_id: str, variable: str) -> list[Obs]:
    """Monthly rows with one column per day (Vazao01..31 in m3/s, Cota01..31 in cm).

    Each day becomes an Obs at 00:00 Brasilia with source "ana_hist" (distinct from
    15-minute telemetry, which uses "ana"). When a month appears with both
    NivelConsistencia 1 (raw) and 2 (consisted), the consisted row wins.
    """
    prefix, scale = _HISTORY_PREFIX[variable]
    best: dict[tuple[int, int], tuple[int, dict]] = {}
    for row in _rows(xml, "SerieHistorica"):
        try:
            month = datetime.strptime(row["DataHora"].strip(), "%Y-%m-%d %H:%M:%S")
            level = int(row.get("NivelConsistencia", "1").strip())
        except (KeyError, ValueError):
            continue
        key = (month.year, month.month)
        if key not in best or level > best[key][0]:
            best[key] = (level, row)

    out: list[Obs] = []
    for (year, mon), (_, row) in sorted(best.items()):
        for day in range(1, 32):
            try:
                local = datetime(year, mon, day, tzinfo=BRASILIA)
            except ValueError:
                break  # past the end of this month
            v = to_float(row.get(f"{prefix}{day:02d}"))
            if v is not None:
                out.append(Obs(station_id, variable, local.astimezone(timezone.utc), v * scale, "ana_hist"))
    return out
