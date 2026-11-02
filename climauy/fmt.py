"""Text formatting shared by the narrative, the charts and the pages (rioplatense Spanish)."""
from __future__ import annotations

import math
from datetime import date, datetime

from .timeutil import MONTEVIDEO

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]

# 3-month ONI/RONI codes -> (Spanish span, center month number). CPC labels each
# season with the year of its CENTER month: "2026-DJF" is Dec 2025 to Feb 2026 and
# "2026-NDJ" is Nov 2026 to Jan 2027.
SEASONS = {
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


def pct(p: float) -> str:
    """Percentile as text, never rounded up: 94,7 -> "94"; 99,56 -> "99,5"; 100 only when exactly 100.

    Rounding would turn 99,5 into "100", a record that is not one.
    """
    if p >= 100.0:
        return "100"
    if p >= 99.0:
        tenths = min(math.floor(p * 10 + 1e-9), 999)
        return f"{tenths // 10},{tenths % 10}"
    return str(math.floor(p + 1e-9))


RECORD_TEXT = "el valor más alto desde 1990"


def is_record(p: float) -> bool:
    """Percentile 100: higher than or equal to every day since 1990. Sentences then say RECORD_TEXT."""
    return p >= 100.0


def num(v: float, decimals: int | None = None) -> str:
    """Uruguayan number: decimal comma, dot for thousands (2 decimals under 100 by default)."""
    if decimals is None:
        decimals = 0 if abs(v) >= 100 else 2
    s = f"{v:,.{decimals}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def meters(v: float) -> str:
    """Meters with as many decimals as the value needs (7,5 m; 11 m; 7,66 m)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",") + " m"


def signed(v: float) -> str:
    """Two decimals with an explicit sign; a tiny negative reads +0,00, not -0,00."""
    return f"{round(v, 2) + 0.0:+.2f}".replace(".", ",")


def days(n: float) -> str:
    """Whole days: "1 día", "3 días"."""
    n = int(round(n))
    return f"{n} día" if n == 1 else f"{n} días"


def day_long(d: date) -> str:
    return f"{d.day} de {MONTHS[d.month - 1]} de {d.year}"


def day_numeric(d: date) -> str:
    return f"{d.day}/{d.month}/{d.year}"


def day_short(d: date) -> str:
    return f"{d.day}/{d.month}"


def hour_local(t_utc: datetime) -> str:
    """Clock time in America/Montevideo, 24 h."""
    return t_utc.astimezone(MONTEVIDEO).strftime("%H:%M")


def is_season(period: str) -> bool:
    year, _, code = period.partition("-")
    return year.isdigit() and code in SEASONS


def season_key(period: str) -> tuple[int, int]:
    """Chronological sort key of a CPC period like "2026-JAS"."""
    year, code = period.split("-", 1)
    return int(year), SEASONS[code][1]


def season_label(period: str) -> str:
    year_s, code = period.split("-", 1)
    year, center = int(year_s), SEASONS[code][1]
    if center == 1:
        return f"diciembre de {year - 1} a febrero de {year}"
    if center == 12:
        return f"noviembre de {year} a enero de {year + 1}"
    return f"{SEASONS[code][0]} de {year}"
