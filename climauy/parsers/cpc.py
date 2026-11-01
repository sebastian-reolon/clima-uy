"""Parsers for NOAA CPC ENSO index text files (ONI, RONI, weekly Niño SST)."""
from __future__ import annotations

import re
from datetime import datetime

from climauy.parsers import to_float
from climauy.store import IndexValue

# CPC placeholder for a missing value.
_FILL = -99.9
_SEASON = re.compile(r"[A-Z]{3}")


def _seasonal(text: str, name: str, value_col: int) -> list[IndexValue]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) <= value_col or not _SEASON.fullmatch(parts[0]) or not parts[1].isdigit():
            continue
        v = to_float(parts[value_col])
        if v is None or v == _FILL:
            continue
        out.append(IndexValue(name, f"{parts[1]}-{parts[0]}", v))
    return out


def parse_oni(text: str) -> list[IndexValue]:
    """oni.ascii.txt: 'SEAS YR TOTAL ANOM'; the index is the anomaly."""
    return _seasonal(text, "ONI", 3)


def parse_roni(text: str) -> list[IndexValue]:
    """RONI.ascii.txt: 'SEAS YR ANOM'."""
    return _seasonal(text, "RONI", 2)


def parse_weekly(text: str) -> list[IndexValue]:
    """wksst9120.for: 'DDMONYYYY' then SST and anomaly for Nino 1+2, 3, 3.4, 4.

    Values have one decimal and are glued together when the anomaly is negative
    ('26.5-0.2'), so they are matched by shape rather than split on spaces.
    Returns the Nino 3.4 anomaly, period = ISO date of the week.
    """
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d{2}[A-Z]{3}\d{4})\s+(.*)", line)
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%d%b%Y").date()
        except ValueError:
            continue
        nums = re.findall(r"-?\d+\.\d", m.group(2))
        if len(nums) != 8:
            continue
        v = to_float(nums[5])
        if v is None or v == _FILL:
            continue
        out.append(IndexValue("NINO34_WEEKLY", day.isoformat(), v))
    return out
