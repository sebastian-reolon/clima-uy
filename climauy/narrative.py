"""Deterministic Spanish phrases summarizing the river state and ENSO indices. Pure functions."""
from __future__ import annotations

from typing import Sequence

from .analysis.status import StationStatus
from .catalog import Station
from .store import IndexValue

HIGH_PERCENTILE = 90
MAX_LISTED = 4

# 3-month ONI/RONI codes -> (Spanish span, month number of the LAST month of the span).
# The period year is read as the year of the last month, so DJF 2026 is
# diciembre 2025 a febrero 2026 and NDJ 2026 is noviembre 2025 a enero 2026.
_SEASONS = {
    "DJF": ("diciembre-febrero", 2),
    "JFM": ("enero-marzo", 3),
    "FMA": ("febrero-abril", 4),
    "MAM": ("marzo-mayo", 5),
    "AMJ": ("abril-junio", 6),
    "MJJ": ("mayo-julio", 7),
    "JJA": ("junio-agosto", 8),
    "JAS": ("julio-septiembre", 9),
    "ASO": ("agosto-octubre", 10),
    "SON": ("septiembre-noviembre", 11),
    "OND": ("octubre-diciembre", 12),
    "NDJ": ("noviembre-enero", 1),
}


def _join(items: list[str]) -> str:
    if len(items) <= MAX_LISTED:
        shown, extra = items, 0
    else:
        shown, extra = items[:MAX_LISTED], len(items) - MAX_LISTED
    if extra:
        return ", ".join(shown) + f" y {extra} más"
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " y " + shown[-1]


def _display(station: Station) -> str:
    if station.represents:
        return f"{station.represents} (medido en {station.name})"
    return station.name


def river_summary(statuses: Sequence[StationStatus], catalog: Sequence[Station]) -> str:
    """One to three sentences on the river; "" if no fresh station has a percentile."""
    by_id = {s.station_id: s for s in statuses if not s.stale}
    # Downstream order; stations missing from either side are skipped.
    rows = [(st, by_id[st.id]) for st in sorted(catalog, key=lambda s: s.order) if st.id in by_id]
    ranked = [(st, ss) for st, ss in rows if ss.percentile_today is not None]
    if not ranked:
        return ""

    top_st, top_ss = max(ranked, key=lambda r: r[1].percentile_today)
    pct = top_ss.percentile_today
    if pct >= HIGH_PERCENTILE:
        parts = [f"El río está más alto en {_display(top_st)}: más que el {int(pct + 0.5)}% de los días desde 1990."]
    else:
        parts = ["El río está dentro de valores habituales en todo el recorrido."]

    rising = [_display(st) for st, ss in rows if ss.trend == "sube"]
    falling = [_display(st) for st, ss in rows if ss.trend == "baja"]
    halves = []
    if rising:
        halves.append(f"Sube en {_join(rising)}")
    if falling:
        halves.append(f"baja en {_join(falling)}" if rising else f"Baja en {_join(falling)}")
    if halves:
        parts.append("; ".join(halves) + ".")

    alerted = [_display(st) for st, ss in rows if ss.above_alert is True]
    if len(alerted) == 1:
        parts.append(f"1 estación supera su cota de alerta: {alerted[0]}.")
    elif alerted:
        parts.append(f"{len(alerted)} estaciones superan su cota de alerta: {_join(alerted)}.")
    return " ".join(parts)


def _signed(value: float) -> str:
    return f"{value:+.2f}".replace(".", ",")


def _period_key(period: str) -> tuple[int, int]:
    year, code = period.split("-", 1)
    return int(year), _SEASONS[code][1]


def _period_label(period: str) -> str:
    year, code = period.split("-", 1)
    return f"{_SEASONS[code][0]} de {year}"


def enso_summary(indices: Sequence[IndexValue]) -> str:
    """Sentence on the latest RONI and the ONI of the same period; "" without RONI."""
    ronis = [i for i in indices if i.name == "RONI" and i.period.split("-", 1)[-1] in _SEASONS]
    if not ronis:
        return ""
    latest = max(ronis, key=lambda i: _period_key(i.period))
    if latest.value >= 0.5:
        lead = "El Niño sigue activo:"
    elif latest.value <= -0.5:
        lead = "Hay condiciones de La Niña:"
    else:
        lead = "El Pacífico está en condiciones neutrales:"
    text = (
        f"{lead} el índice oficial de la NOAA (RONI) está en {_signed(latest.value)} "
        f"para {_period_label(latest.period)}"
    )
    oni = next((i for i in indices if i.name == "ONI" and i.period == latest.period), None)
    if oni is not None:
        text += f"; el índice tradicional (ONI) marca {_signed(oni.value)}"
    return text + "."
