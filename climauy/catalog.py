"""Station catalog for the Uruguay River basin, ordered upstream to downstream."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("stations.json")


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
