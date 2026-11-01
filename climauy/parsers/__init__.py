"""Parsers for raw source files, plus the shared outlier flagging."""
from __future__ import annotations

import math
import re
from dataclasses import replace
from statistics import median

from climauy.store import Obs

# Series with this median spacing or coarser (daily gauges) are never flagged: a 9-reading
# window then spans over a week and real flood limbs would look like outliers.
MIN_FLAGGABLE_GAP_H = 6.0
# Absolute tolerance floor for water levels in metres (+0.5 m is +100% at a 0.5 m stage).
LEVEL_ABS_TOL_M = 0.5

# Readings that are placeholders for "no data" in the source feeds.
FILL_VALUES = (-999.0, -9999.0)


def to_float(text: str | float | int | None) -> float | None:
    """Parse a source value to a finite float, or None for empty/fill/garbage.

    Accepts a comma decimal separator ('7,56'). Returns None for blanks,
    non-finite numbers and the -999 style placeholders.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        v = float(text)
    else:
        s = text.strip().replace(",", ".")
        if not s or not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", s):
            return None
        v = float(s)
    if not math.isfinite(v) or v in FILL_VALUES:
        return None
    return v


def flag_outliers(obs: list[Obs], window: int = 9, rel_tol: float = 0.5) -> list[Obs]:
    """Mark readings far from the rolling median of their own series as suspect.

    A series is every reading sharing (station_id, variable, source). The median
    is taken over a centered window of `window` readings (truncated at the ends,
    the point itself included: a lone spike cannot move the median of 9). A
    reading is suspect when |value - median| exceeds rel_tol * |median|; for
    levels the tolerance is at least 0.5 m. Skipped: rain (intermittent, a zero
    median says nothing), series shorter than 3 readings, series whose median
    spacing is 6 h or more (daily gauges) and, except for levels, medians of
    zero. The input order is preserved.
    """
    groups: dict[tuple, list[int]] = {}
    for i, o in enumerate(obs):
        groups.setdefault((o.station_id, o.variable, o.source), []).append(i)

    out = list(obs)
    half = window // 2
    for (_, variable, _), idxs in groups.items():
        if variable == "rain" or len(idxs) < 3:
            continue
        idxs = sorted(idxs, key=lambda i: obs[i].time_utc)
        gaps = [(obs[b].time_utc - obs[a].time_utc).total_seconds() / 3600 for a, b in zip(idxs, idxs[1:])]
        if median(gaps) >= MIN_FLAGGABLE_GAP_H:
            continue
        vals = [obs[i].value for i in idxs]
        for pos, i in enumerate(idxs):
            lo, hi = max(0, pos - half), min(len(vals), pos + half + 1)
            m = median(vals[lo:hi])
            tol = max(rel_tol * abs(m), LEVEL_ABS_TOL_M) if variable == "level" else rel_tol * abs(m)
            if (m != 0 or variable == "level") and abs(vals[pos] - m) > tol:
                out[i] = replace(obs[i], quality="suspect")
    return out
