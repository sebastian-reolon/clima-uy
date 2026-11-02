"""The river day by day: one row per main-river station, one column per local day.

Feeds the /rio/crecida/ page (a km x day diagram) and its CSV. Each cell is
that day's mean ranked against the station's own history before the window,
so a flood travelling downstream shows as a dark diagonal band.

Rulings (docs plan 2026-11-02, clima-uy crecida):
- Window: `days` local days ending on `end_day` (the build's local day).
- Stations: the main river in `river_km` order (the twin gauge below Salto
  Grande is left out, as in the profile). Each uses its discharge or level
  series, built like status.py (one source family, never mixed), among those
  with at least MIN_HISTORY_YEARS of history: the one with more days in the
  window, discharge on a tie. Kept only with data on 80% of the window days.
  The rest are named in two groups: without such a series
  (`skipped_history`) and with one but too many gaps (`skipped_gaps`).
- Cell: percentile_of(day mean, daily means from BASELINE_START up to the
  window start, exclusive), truncated to a tenth (never rounded up). A day
  without data is None. The build's window ends today, a partial day.
- Peak: a day whose value is strictly higher than every other day within
  PEAK_HALF_WIDTH days either side, at percentile PEAK_MIN_PERCENTILE or
  more, with those days all inside the window and data on at least
  PEAK_MIN_NEIGHBOURS of them, so «más alto que los cinco días anteriores y
  los cinco siguientes» is true as written.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Sequence

from ..catalog import Station, river_km
from ..store import Store
from .daily import BASELINE_START, family_daily, family_for, history_years, percentile_of, station_readings
from .status import MIN_HISTORY_YEARS

WINDOW_DAYS = 60
MIN_COVERAGE = 0.8
PEAK_HALF_WIDTH = 5
PEAK_MIN_PERCENTILE = 90.0
PEAK_MIN_NEIGHBOURS = 8  # of the 2 * PEAK_HALF_WIDTH days around it
# Where the river starts to have a Uruguayan bank (same city as the home profile's shading).
UY_CITY = "Bella Unión"

Variable = Literal["discharge", "level"]


@dataclass(frozen=True)
class MatrixRow:
    station_id: str
    name: str
    city: str | None  # the Uruguayan city the station stands for, if any
    km: float  # river_km from the first main-river station
    variable: Variable
    values: list[float | None]  # day mean in the variable's unit, None without data
    percentiles: list[float | None]
    peaks: list[int]  # indexes into RiverMatrix.days


@dataclass(frozen=True)
class RiverMatrix:
    days: list[date]  # oldest first
    rows: list[MatrixRow]  # downstream order
    skipped_history: list[str]  # names of the stations without MIN_HISTORY_YEARS of history
    skipped_gaps: list[str]  # names of the stations with history but too few days in the window
    stations_total: int  # main-river stations (twin gauge excluded): rows plus both skipped groups
    km_max: float  # km of the last main-river station, the drawing's right edge
    uy_km: float | None  # km of the station that stands for UY_CITY, where the Uruguayan bank begins
    partial_last: bool = False  # the last day is still in progress (the build's own day)


def floor_tenth(p: float) -> float:
    """Truncate a percentile to one decimal: 89,96 is 89,9, never 90. The epsilon keeps an exact tenth."""
    return math.floor(p * 10 + 1e-9) / 10


def _peaks(values: Sequence[float | None], percentiles: Sequence[float | None]) -> list[int]:
    n, h = len(values), PEAK_HALF_WIDTH
    out = []
    for i in range(h, n - h):
        v, p = values[i], percentiles[i]
        if v is None or p is None or p < PEAK_MIN_PERCENTILE:
            continue
        around = [x for x in values[i - h:i] + values[i + 1:i + h + 1] if x is not None]
        if len(around) >= PEAK_MIN_NEIGHBOURS and all(x < v for x in around):
            out.append(i)
    return out


def _row(store: Store, st: Station, km: float, days: list[date]) -> tuple[MatrixRow | None, bool]:
    """(row, eligible): row is None when the station is left out; eligible says whether it had a series."""
    options: list[tuple[dict[date, float], Variable]] = []
    for variable in ("discharge", "level"):
        daily = family_daily(station_readings(store, st, variable), family_for(st, variable))
        if history_years(daily) >= MIN_HISTORY_YEARS:
            options.append((daily, variable))
    if not options:
        return None, False

    def covered(daily: dict[date, float]) -> int:
        return sum(1 for d in days if d in daily)

    daily, variable = max(options, key=lambda o: (covered(o[0]), o[1] == "discharge"))
    ref = [v for d, v in daily.items() if BASELINE_START <= d < days[0]]
    if not ref or covered(daily) < MIN_COVERAGE * len(days):
        return None, True
    values = [daily.get(d) for d in days]
    pcts = [None if v is None else floor_tenth(percentile_of(v, ref)) for v in values]
    return MatrixRow(st.id, st.name, st.represents, km, variable, values, pcts, _peaks(values, pcts)), True


def river_matrix(store: Store, catalog: Sequence[Station], end_day: date, days: int = WINDOW_DAYS,
                 partial_last: bool = False) -> RiverMatrix:
    """Station x day percentiles of the main river for the `days` local days ending on `end_day`.

    `partial_last` says the last day is still in progress (the build passes True: it ends on today).
    """
    window = [end_day - timedelta(days=days - 1 - i) for i in range(days)]
    km = river_km(catalog)
    main = [s for s in sorted(catalog, key=lambda s: s.order) if s.id in km]
    rows, no_history, gaps = [], [], []
    for st in main:
        row, eligible = _row(store, st, km[st.id], window)
        if row is not None:
            rows.append(row)
        else:
            (gaps if eligible else no_history).append(st.name)
    uy = next((s for s in main if s.represents == UY_CITY), None)
    return RiverMatrix(
        days=window, rows=rows, skipped_history=no_history, skipped_gaps=gaps, stations_total=len(main),
        km_max=max(km.values(), default=0.0), uy_km=km[uy.id] if uy else None, partial_last=partial_last,
    )
