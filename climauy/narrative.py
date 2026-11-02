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


_COUNTRY_WORD = {"AR": "argentino", "BR": "brasileño", "UY": "uruguayo"}
_NO_DATA_H1 = "No hay datos al día del río frente a las ciudades uruguayas."


def _fresh(ss: StationStatus | None) -> bool:
    return ss is not None and not ss.stale and ss.latest_value is not None


def _height_word(max_pct: float | None) -> str:
    if max_pct is None:
        return ""
    if max_pct >= 90:
        return "viene alto"
    if max_pct >= 75:
        return "está por encima de lo normal"
    if max_pct >= 25:
        return "está en valores normales"
    return "está bajo"


def river_headline(statuses: Sequence[StationStatus], catalog: Sequence[Station]) -> tuple[str, str]:
    """(h1, sub) for the home: the main river in front of the Uruguayan cities. Pure."""
    by_id = {s.station_id: s for s in statuses}
    main = sorted((st for st in catalog if st.river == "Uruguay"), key=lambda s: s.order)
    fresh = [(st, by_id[st.id]) for st in main if _fresh(by_id.get(st.id))]
    cities = [(st, ss) for st, ss in fresh if st.represents and st.alert_m is not None and ss.above_alert is not None]
    if not cities:
        return _NO_DATA_H1, ""

    pcts = [ss.percentile_today for _, ss in cities if ss.percentile_today is not None]
    height = _height_word(max(pcts) if pcts else None)
    river = f"El río {height}" if height else "El río"

    alerted = [st.represents for st, ss in cities if ss.above_alert is True]
    rising = [(st, ss) for st, ss in cities if ss.trend == "sube"]
    falling = [st.represents for st, ss in cities if ss.trend == "baja"]
    if alerted:
        h1 = f"El río pasó la cota de alerta frente a {_join(alerted)}."
    elif rising:
        rise = max((ss.level_change_3d_m for _, ss in rising if ss.level_change_3d_m is not None), default=None)
        if rise is None:
            speed = "sube"
        elif rise < 0.5:
            speed = "sube despacio"
        elif rise >= 1.0:
            speed = "sube rápido"
        else:
            speed = "sube"
        h1 = f"{river} y {speed} frente a {_join([st.represents for st, _ in rising])}."
    elif falling:
        h1 = f"{river} y baja frente a {_join(falling)}."
    elif any(ss.trend == "estable" for _, ss in cities):
        steady = [st.represents for st, ss in cities if ss.trend == "estable"]
        h1 = f"{river} y se mantiene estable frente a {_join(steady)}."
    elif not height:
        h1 = (
            f"El río frente a {_join([st.represents for st, _ in cities])} tiene datos al día, "
            "pero no alcanzan para compararlos con su historia."
        )
    else:
        h1 = f"{river}."

    sentences = []
    if not alerted:
        sentences.append("Ninguna regla de las que miden enfrente llegó a su cota de alerta.")

    first_city_order = min(st.order for st, _ in cities)
    city_ids = {st.id for st, _ in cities}
    upstream = [ss for st, ss in fresh if st.order < first_city_order and ss.trend is not None]
    up_rising = sum(1 for ss in upstream if ss.trend == "sube")
    up_falling = sum(1 for ss in upstream if ss.trend == "baja")
    says_falls = up_falling > up_rising
    if says_falls:
        sentences.append("Río arriba ya baja.")
    elif up_rising > up_falling:
        sentences.append("Río arriba sigue subiendo.")

    over = [(st, ss) for st, ss in fresh if st.id not in city_ids and ss.above_alert is True]
    if len(over) == 1:
        st, ss = over[0]
        side = _COUNTRY_WORD.get(st.country)
        text = f"La única sobre alerta es {st.name}, del lado {side}" if side else (
            f"La única sobre alerta es {st.name}, del otro lado"
        )
        tail = {
            "baja": "también baja" if says_falls else "baja",
            "sube": "sigue subiendo",
            "estable": "está estable",
        }.get(ss.trend)
        if tail:
            text += f", y {tail}"
        sentences.append(text + ".")
    elif over:
        sentences.append(f"Sobre la alerta: {_join([st.name for st, _ in over])}.")
    return h1, " ".join(sentences)


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
