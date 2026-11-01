"""Deterministic Spanish phrases summarizing the river state and ENSO indices. Pure functions."""
from __future__ import annotations

from typing import Sequence

from .analysis.status import StationStatus
from .catalog import Station
from .store import IndexValue

HIGH_PERCENTILE = 90
MAX_LISTED = 4

# 3-month ONI/RONI codes -> (Spanish span, center month number). CPC labels each
# season with the year of its CENTER month: "2026-DJF" is Dec 2025 to Feb 2026 and
# "2026-NDJ" is Nov 2026 to Jan 2027.
_SEASONS = {
    "DJF": ("diciembre-febrero", 1),
    "JFM": ("enero-marzo", 2),
    "FMA": ("febrero-abril", 3),
    "MAM": ("marzo-mayo", 4),
    "AMJ": ("abril-junio", 5),
    "MJJ": ("mayo-julio", 6),
    "JJA": ("junio-agosto", 7),
    "JAS": ("julio-septiembre", 8),
    "ASO": ("agosto-octubre", 9),
    "SON": ("septiembre-noviembre", 10),
    "OND": ("octubre-diciembre", 11),
    "NDJ": ("noviembre-enero", 12),
}
_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"]


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
    """One to three sentences on the river; "" if there is nothing to say.

    Without any fresh percentile, sentence 1 is omitted (no claim about usual values).
    """
    by_id = {s.station_id: s for s in statuses if not s.stale}
    # Downstream order; stations missing from either side are skipped.
    rows = [(st, by_id[st.id]) for st in sorted(catalog, key=lambda s: s.order) if st.id in by_id]
    ranked = [(st, ss) for st, ss in rows if ss.percentile_today is not None]

    parts = []
    if ranked:
        top_st, top_ss = max(ranked, key=lambda r: r[1].percentile_today)
        pct = top_ss.percentile_today
        if pct >= HIGH_PERCENTILE:
            # Round half up, capped at 99 so it never reads "más que el 100%".
            shown = min(99, int(pct + 0.5))
            parts.append(f"El río está más alto en {_display(top_st)}: más que el {shown}% de los días desde 1990.")
        else:
            parts.append("El río está dentro de valores habituales en todo el recorrido.")

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
    # round + 0.0 turns a tiny negative (-0.001) into +0,00 instead of -0,00.
    return f"{round(value, 2) + 0.0:+.2f}".replace(".", ",")


def _period_key(period: str) -> tuple[int, int]:
    year, code = period.split("-", 1)
    return int(year), _SEASONS[code][1]


def _period_label(period: str) -> str:
    year_s, code = period.split("-", 1)
    year, center = int(year_s), _SEASONS[code][1]
    if center == 1:
        return f"diciembre de {year - 1} a febrero de {year}"
    if center == 12:
        return f"noviembre de {year} a enero de {year + 1}"
    return f"{_SEASONS[code][0]} de {year}"


def enso_summary(indices: Sequence[IndexValue]) -> str:
    """Sentence on the latest RONI and the ONI of the same period; "" without RONI."""
    # Entries with unknown season codes (or malformed periods) are filtered out.
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
