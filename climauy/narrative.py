"""Deterministic Spanish phrases summarizing the river state and ENSO indices. Pure functions."""
from __future__ import annotations

from typing import Sequence

from .analysis.status import StationStatus
from .catalog import Station
from .fmt import is_record, is_season, pct, record_text, season_key, season_label, signed, since_year
from .store import IndexValue

HIGH_PERCENTILE = 90
MAX_LISTED = 4


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
        top_pct = top_ss.percentile_today
        if top_pct >= HIGH_PERCENTILE:
            since = since_year(top_ss.history_start_year)
            how_high = record_text(since) if is_record(top_pct) else f"más que el {pct(top_pct)}% de los días desde {since}"
            parts.append(f"El río está más alto en {_display(top_st)}: {how_high}.")
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
        parts.append(f"1 estación alcanzó o superó su cota de alerta: {alerted[0]}.")
    elif alerted:
        parts.append(f"{len(alerted)} estaciones alcanzaron o superaron su cota de alerta: {_join(alerted)}.")
    return " ".join(parts)


def enso_summary(indices: Sequence[IndexValue]) -> str:
    """Sentence on the latest RONI and the ONI of the same period; "" without RONI."""
    # Entries with unknown season codes (or malformed periods) are filtered out.
    ronis = [i for i in indices if i.name == "RONI" and is_season(i.period)]
    if not ronis:
        return ""
    latest = max(ronis, key=lambda i: season_key(i.period))
    if latest.value >= 0.5:
        lead = "El Niño sigue activo:"
    elif latest.value <= -0.5:
        lead = "Hay condiciones de La Niña:"
    else:
        lead = "El Pacífico está en condiciones neutrales:"
    text = (
        f"{lead} el índice oficial de la NOAA (RONI) está en {signed(latest.value)} "
        f"para {season_label(latest.period)}"
    )
    oni = next((i for i in indices if i.name == "ONI" and i.period == latest.period), None)
    if oni is not None:
        text += f"; el índice tradicional (ONI) marca {signed(oni.value)}"
    return text + "."
