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
  window, discharge on a tie. Kept only with data on 80% of the window days;
  the stations with history that miss it are listed by name in `skipped`.
  Stations without such a series at all are neither kept nor listed.
- Cell: percentile_of(day mean, daily means from BASELINE_START up to the
  window start, exclusive). A day without data is None.
- Peak: a day whose value is strictly the highest within PEAK_HALF_WIDTH days
  either side (a tie is no peak; missing days do not count), at percentile
  PEAK_MIN_PERCENTILE or more, and not among the first or last PEAK_EDGE_DAYS.
"""
from __future__ import annotations

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
PEAK_EDGE_DAYS = 2
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
    skipped: list[str]  # names of the stations with history but too few days in the window
    stations_total: int  # main-river stations (twin gauge excluded): the «de m» of the page
    km_max: float  # km of the last main-river station, the drawing's right edge
    uy_km: float | None  # km of the station that stands for UY_CITY, where the Uruguayan bank begins


def _peaks(values: Sequence[float | None], percentiles: Sequence[float | None]) -> list[int]:
    n = len(values)
    out = []
    for i in range(PEAK_EDGE_DAYS, n - PEAK_EDGE_DAYS):
        v, p = values[i], percentiles[i]
        if v is None or p is None or p < PEAK_MIN_PERCENTILE:
            continue
        near = [x for x in values[max(0, i - PEAK_HALF_WIDTH):i + PEAK_HALF_WIDTH + 1] if x is not None]
        if max(near) == v and near.count(v) == 1:
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
    pcts = [None if v is None else percentile_of(v, ref) for v in values]
    return MatrixRow(st.id, st.name, st.represents, km, variable, values, pcts, _peaks(values, pcts)), True


def river_matrix(store: Store, catalog: Sequence[Station], end_day: date, days: int = WINDOW_DAYS) -> RiverMatrix:
    """Station x day percentiles of the main river for the `days` local days ending on `end_day`."""
    window = [end_day - timedelta(days=days - 1 - i) for i in range(days)]
    km = river_km(catalog)
    main = [s for s in sorted(catalog, key=lambda s: s.order) if s.id in km]
    rows, skipped = [], []
    for st in main:
        row, eligible = _row(store, st, km[st.id], window)
        if row is not None:
            rows.append(row)
        elif eligible:
            skipped.append(st.name)
    uy = next((s for s in main if s.represents == UY_CITY), None)
    return RiverMatrix(
        days=window, rows=rows, skipped=skipped, stations_total=len(main),
        km_max=max(km.values(), default=0.0), uy_km=km[uy.id] if uy else None,
    )
