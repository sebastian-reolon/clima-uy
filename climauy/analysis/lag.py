"""Travel time (lag) from an upstream gauge to a downstream one, with held-out validation.

Method:
1. Flood events are the peaks of the downstream series above its 95th
   percentile. For each one, the upstream peak is searched in the 15 days up
   to and including the downstream peak day. It is accepted only when it is
   a local maximum strictly inside that window (lag 1 to 14 days): a maximum
   on the edge means upstream was still rising at the downstream peak, or
   falling through the whole window, so the event is skipped and counted.
2. For each fixed held-out window, the lag L is the median of the events
   whose peak lies outside the window, rounded to whole days. A linear fit
   down(t) = a + b*up(t-L) is made on the days outside the window, and inside
   it the predicted peak is the argmax of the prediction. The fit never uses
   an upstream predictor from inside the window. A lag of 0 cannot be
   evaluated (persistence would be the observation itself). Persistence uses
   down(t-L) as the forecast of down(t); both errors are computed on the same
   days.
3. The lag is "validated" only when, in all three held-out windows, the peak
   is hit within 1 day and the model beats persistence, and there are at
   least 3 events to estimate from. Thresholds are fixed, never tuned.

Daily series are dict[date, float] and may have gaps: every pair (t, t-L)
counts only when both days exist.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import numpy as np

from ..catalog import Station
from ..store import Store
from .daily import station_daily

HELD_OUT_WINDOWS: dict[str, tuple[date, date]] = {
    "2009": (date(2009, 11, 1), date(2010, 1, 31)),
    "2015": (date(2015, 12, 1), date(2016, 1, 31)),
    "2023": (date(2023, 10, 1), date(2023, 12, 31)),
}
SEARCH_DAYS = 15
MAX_PEAK_ERROR_DAYS = 1
MIN_EVENTS = 3
MIN_OTHER_EVENTS = 2

# (upstream station, downstream station, variable); the first one is the main pair.
PRODUCTION_PAIRS = [
    ("ina_72", "ina_77", "discharge"),  # Paso de los Libres -> Salto Grande arriba (INA 921 -> 926)
    ("ina_72", "ina_79", "level"),  # Paso de los Libres -> Concordia
]


@dataclass(frozen=True)
class HoldoutResult:
    event: str
    lag_days: int  # whole-day lag estimated from the other events
    predicted_peak: date
    observed_peak: date
    peak_error_days: int  # absolute value
    mae_model: float
    mae_persistence: float


@dataclass(frozen=True)
class LagResult:
    upstream: str  # "<station_id>:<variable>"
    downstream: str
    lag_days_median: float | None
    lag_days_range: tuple[float, float] | None
    per_event: dict[str, float]  # ISO date of the downstream peak -> lag in days
    holdout: dict[str, HoldoutResult]
    validated: bool
    reason: str  # public sentence, rioplatense Spanish
    events_skipped: int = 0  # downstream peaks whose upstream peak sat on the window edge


def find_flood_events(
    series: dict[date, float], quantile: float = 0.95, min_separation_days: int = 30
) -> list[date]:
    """Peak days of the episodes above the series' quantile, at least min_separation_days apart.

    Consecutive days strictly above the threshold form one episode (a missing
    day ends it) and the episode's event day is its maximum. When two events
    are closer than the separation, the larger peak is kept.
    """
    if not series:
        return []
    threshold = float(np.quantile(list(series.values()), quantile))
    peaks: list[date] = []
    episode: list[date] = []
    for d in sorted(series):
        if series[d] > threshold:
            if episode and (d - episode[-1]).days != 1:
                peaks.append(max(episode, key=lambda x: series[x]))
                episode = []
            episode.append(d)
        elif episode:
            peaks.append(max(episode, key=lambda x: series[x]))
            episode = []
    if episode:
        peaks.append(max(episode, key=lambda x: series[x]))

    kept: list[date] = []
    for p in sorted(peaks, key=lambda x: (-series[x], x)):  # largest first, earlier wins ties
        if all(abs((p - k).days) >= min_separation_days for k in kept):
            kept.append(p)
    return sorted(kept)


def _event_lags(up: dict[date, float], down: dict[date, float]) -> tuple[dict[date, int], int]:
    """Lag per downstream peak, plus how many peaks were skipped."""
    lags: dict[date, int] = {}
    skipped = 0
    for peak in find_flood_events(down):
        window = [peak - timedelta(days=k) for k in range(SEARCH_DAYS + 1)]
        present = [d for d in window if d in up]
        if not present:
            skipped += 1
            continue
        up_peak = max(present, key=lambda d: (up[d], d))  # ties: the later (shorter lag)
        lag = (peak - up_peak).days
        neighbours = [up[d] for d in (up_peak - timedelta(days=1), up_peak + timedelta(days=1)) if d in up]
        if not 0 < lag < SEARCH_DAYS or any(up[up_peak] < v for v in neighbours):
            skipped += 1
            continue
        lags[peak] = lag
    return lags, skipped


def _in(d: date, window: tuple[date, date]) -> bool:
    return window[0] <= d <= window[1]


def _holdout(
    name: str,
    window: tuple[date, date],
    up: dict[date, float],
    down: dict[date, float],
    lags: dict[date, int],
) -> HoldoutResult | str:
    """HoldoutResult, or a Spanish failure sentence."""
    others = [lag for peak, lag in lags.items() if not _in(peak, window)]
    if len(others) < MIN_OTHER_EVENTS:
        return f"en {name} no alcanzan las otras crecidas para estimar el rezago"
    # Python rounds .5 to even; a half-day median has no better whole-day choice.
    lag = int(round(statistics.median(others)))
    if lag == 0:
        return f"en {name} el rezago estimado es 0 días y no se puede evaluar"
    shift = timedelta(days=lag)

    fit = [
        (up[t - shift], v)
        for t, v in down.items()
        if not _in(t, window) and not _in(t - shift, window) and t - shift in up
    ]
    xs = [x for x, _ in fit]
    if len(fit) < 2 or min(xs) == max(xs):
        return f"en {name} no hay datos suficientes para ajustar el modelo"
    b, a = np.polyfit(xs, [y for _, y in fit], 1)

    inside = sorted(t for t in down if _in(t, window))
    predicted = {t: a + b * up[t - shift] for t in inside if t - shift in up}
    scored = [t for t in predicted if t - shift in down]
    if not scored:
        return f"en {name} no hay datos para comparar"
    predicted_peak = max(predicted, key=lambda t: (predicted[t], -t.toordinal()))
    observed_peak = max(inside, key=lambda t: (down[t], -t.toordinal()))
    return HoldoutResult(
        event=name,
        lag_days=lag,
        predicted_peak=predicted_peak,
        observed_peak=observed_peak,
        peak_error_days=abs((predicted_peak - observed_peak).days),
        mae_model=float(np.mean([abs(predicted[t] - down[t]) for t in scored])),
        mae_persistence=float(np.mean([abs(down[t - shift] - down[t]) for t in scored])),
    )


def _days(n: int) -> str:
    return f"{n} día" if n == 1 else f"{n} días"


def _join_years(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def analyze_lag(
    up: dict[date, float],
    down: dict[date, float],
    held_out: dict[str, tuple[date, date]],
    upstream: str = "",
    downstream: str = "",
) -> LagResult:
    lags, skipped = _event_lags(up, down)
    values = list(lags.values())

    # Failures in order of importance; only the first one goes into the public sentence.
    failures: list[str] = []
    if len(values) < MIN_EVENTS:
        n = len(values)
        failures.append(
            "no hay crecidas para estimar el rezago" if n == 0
            else f"solo hay {n} crecida{'s' if n != 1 else ''} para estimar el rezago"
        )
    if held_out != HELD_OUT_WINDOWS:
        failures.append("no se evaluaron las tres crecidas de referencia")
    holdout: dict[str, HoldoutResult] = {}
    for name, window in held_out.items():
        res = _holdout(name, window, up, down, lags)
        if isinstance(res, str):
            failures.append(res)
            continue
        holdout[name] = res
        if res.peak_error_days > MAX_PEAK_ERROR_DAYS:
            failures.append(f"en {name} el pronóstico le erró al pico por {_days(res.peak_error_days)}")
        if not res.mae_model < res.mae_persistence:
            failures.append(
                f"en {name} el pronóstico anduvo peor que suponer que el río sigue igual "
                f"que hace {_days(res.lag_days)}"
            )

    validated = not failures and set(holdout) == set(HELD_OUT_WINDOWS)
    if validated:
        reason = (
            f"Validado: en {_join_years(list(holdout))} el pico se anticipó con "
            f"{_days(MAX_PEAK_ERROR_DAYS)} de error o menos y mejor que suponer que el río sigue igual."
        )
    else:
        reason = f"No validado: {failures[0] if failures else 'no se evaluaron las tres crecidas de referencia'}."

    return LagResult(
        upstream=upstream,
        downstream=downstream,
        lag_days_median=float(statistics.median(values)) if values else None,
        lag_days_range=(float(min(values)), float(max(values))) if values else None,
        per_event={peak.isoformat(): float(lag) for peak, lag in sorted(lags.items())},
        holdout=holdout,
        validated=validated,
        reason=reason,
        events_skipped=skipped,
    )


def run_production_lags(store: Store, catalog: Sequence[Station]) -> list[LagResult]:
    """Lag analysis for the production pairs, main pair first."""
    by_id = {s.id: s for s in catalog}
    out = []
    for up_id, down_id, variable in PRODUCTION_PAIRS:
        up = station_daily(store, by_id[up_id], variable)
        down = station_daily(store, by_id[down_id], variable)
        out.append(
            analyze_lag(up, down, HELD_OUT_WINDOWS, f"{up_id}:{variable}", f"{down_id}:{variable}")
        )
    return out
