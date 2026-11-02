"""Station catalog for the Uruguay River basin, ordered upstream to downstream."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

CATALOG_PATH = Path(__file__).with_name("stations.json")
MAIN_RIVER = "Uruguay"
_EARTH_KM = 6371.0


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    country: str
    river: str
    lat: float
    lon: float
    river_km: float | None
    km_source: str | None
    # Keys: "ana" | "ina_level" | "ina_q" | "glofas" (GloFAS value is "lat,lon").
    sources: dict[str, str]
    represents: str | None
    alert_m: float | None
    evac_m: float | None
    cota_source: str | None
    order: int
    tributary_of: str | None
    # INA forecast series ids attached to this station.
    forecast_series: list[str] = field(default_factory=list)


def load_catalog(path: Path = CATALOG_PATH) -> list[Station]:
    """Load the catalog, sorted by `order` (downstream)."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    return sorted((Station(**r) for r in rows), key=lambda s: s.order)


def great_circle_km(a: Station, b: Station) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(h))


def river_km(catalog: Sequence[Station]) -> dict[str, float]:
    """Cumulative great-circle km of each main-river station from the first one, in catalogue order (Ruling 5).

    A station at the very same point as the previous one is a twin gauge
    (Salto Grande Arriba and Abajo, upstream and downstream of the dam): only
    the first, Arriba, is kept.
    """
    km: dict[str, float] = {}
    acc = 0.0
    prev: Station | None = None
    for st in sorted(catalog, key=lambda s: s.order):
        if st.river != MAIN_RIVER:
            continue
        if prev is not None:
            step = great_circle_km(prev, st)
            if step < 1e-6:
                continue
            acc += step
        km[st.id] = acc
        prev = st
    return km
