"""Forecast verification against observed daily means."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from ..catalog import Station, load_catalog
from ..store import Store
from ..timeutil import local_day
from .daily import station_daily
from .events import _station

MIN_RUNS = 14
MIN_LEAD1_N = 10

# (issuer, forecast station, variable) -> observation station.
# Concordia's own discharge series stopped, so its GloFAS cell is checked
# against Salto Grande downstream (INA series 927).
VERIFICATION_TARGETS: dict[tuple[str, str, str], str] = {
    ("ina", "ina_65", "level"): "ina_65",
    ("ina", "ina_68", "level"): "ina_68",
    ("ina", "ina_72", "level"): "ina_72",
    ("glofas", "ana_77150000", "discharge"): "ana_77150000",
    ("glofas", "ina_79", "discharge"): "ina_78",
}
_MAIN_QUALIFIER = {"ina": "main", "glofas": "median"}


@dataclass(frozen=True)
class LeadStats:
    n: int
    mae: float
    bias: float  # mean of (forecast - observed)
    coverage: float | None  # INA only: share of observed inside [inferior, superior]


@dataclass(frozen=True)
class VerifyResult:
    n_runs: int
    by_lead: dict[int, LeadStats]
    enough: bool


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def verify_forecasts(
    store: Store, issuer: str, station_id: str, variable: str,
    obs_station_id: str | None = None, catalog: Sequence[Station] | None = None,
) -> VerifyResult:
    """Compare each run's daily forecast with the observed daily mean of the valid local day.

    Forecast values of one run on one local day are averaged. Days without an
    observation and leads <= 0 are skipped.
    """
    stations = catalog if catalog is not None else load_catalog()
    observed = station_daily(store, _station(stations, obs_station_id or station_id), variable)
    main = _MAIN_QUALIFIER[issuer]
    # (issued, valid local day) -> qualifier -> values
    cells: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for f in store.forecasts(issuer, station_id):
        if f.variable == variable and f.qualifier in (main, "inferior", "superior"):
            cells[(f.issued_utc, local_day(f.valid_utc))][f.qualifier].append(f.value)

    errors: dict[int, list[float]] = defaultdict(list)
    hits: dict[int, list[bool]] = defaultdict(list)
    runs = set()
    for (issued, day), quals in cells.items():
        lead = (day - local_day(issued)).days
        obs = observed.get(day)
        if lead <= 0 or obs is None or main not in quals:
            continue
        runs.add(issued)
        errors[lead].append(_mean(quals[main]) - obs)
        if issuer == "ina" and "inferior" in quals and "superior" in quals:
            hits[lead].append(_mean(quals["inferior"]) <= obs <= _mean(quals["superior"]))

    by_lead = {
        lead: LeadStats(
            n=len(errs),
            mae=_mean([abs(e) for e in errs]),
            bias=_mean(errs),
            coverage=_mean([1.0 if h else 0.0 for h in hits[lead]]) if hits.get(lead) else None,
        )
        for lead, errs in sorted(errors.items())
    }
    enough = len(runs) >= MIN_RUNS and by_lead.get(1, LeadStats(0, 0, 0, None)).n >= MIN_LEAD1_N
    return VerifyResult(len(runs), by_lead, enough)


def verify_production(store: Store, catalog: Sequence[Station] | None = None) -> dict[tuple[str, str, str], VerifyResult]:
    """Verify every production forecast series against its mapped observation station."""
    return {
        key: verify_forecasts(store, key[0], key[1], key[2], obs_station_id=obs, catalog=catalog)
        for key, obs in VERIFICATION_TARGETS.items()
    }
