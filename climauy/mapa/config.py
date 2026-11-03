"""Hand-edited city configuration for the flood map: content/mapa/<slug>/config.json.

Every number that reaches the page (zero, marks, today's offset, the SINAE check)
carries a Source with a URL and a date; a missing one is a ValueError naming the field.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT_DIR = REPO_ROOT / "content"
MAPA_DIR = CONTENT_DIR / "mapa"

MARK_KINDS = ("oficial", "pico")
PLACE_KINDS = ("lugar", "rio")
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


@dataclass(frozen=True)
class Source:
    url: str
    date: str
    name: str
    note: str = ""


@dataclass(frozen=True)
class Mark:
    label: str
    short: str
    m: float
    kind: str
    source: Source


@dataclass(frozen=True)
class TodayCfg:
    station_id: str
    offset_m: float
    offset_range: tuple[float, float]
    source: Source


@dataclass(frozen=True)
class SinaeCheck:
    m: float
    people: int
    source: Source


@dataclass(frozen=True)
class RiverCfg:
    """How the river seed finds the DTM's hydro-flattened river: cells at most
    flat_tol_m above the median DTM height inside OSM water. Set it per city so
    it reaches every flattened height; note says why."""
    flat_tol_m: float
    note: str


@dataclass(frozen=True)
class PlaceLabel:
    """A name drawn on the plan at a UTM position: kind «lugar» (place name) or
    «rio» (river name, optionally rotated, degrees)."""
    name: str
    x: float
    y: float
    kind: str
    rotate: float = 0.0


@dataclass(frozen=True)
class CityConfig:
    slug: str
    name: str
    frame_utm: tuple[float, float, float, float]
    phone_frame_utm: tuple[float, float, float, float]
    dtm_urls: list[str]
    zero_m: float
    zero_source: Source
    range_m: tuple[float, float]
    river: RiverCfg
    marks: list[Mark]
    today: TodayCfg | None
    sinae_check: SinaeCheck | None
    place_labels: list[PlaceLabel] = field(default_factory=list)


def _get(d: dict, key: str, field: str):
    if not isinstance(d, dict) or key not in d or d[key] is None:
        raise ValueError(f"{field}: missing")
    return d[key]


def _str(d: dict, key: str, field: str) -> str:
    v = _get(d, key, field)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{field}: expected a non-empty string")
    return v


def _num(d: dict, key: str, field: str) -> float:
    v = _get(d, key, field)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{field}: expected a number")
    return float(v)


def _nums(d: dict, key: str, n: int, field: str) -> tuple[float, ...]:
    v = _get(d, key, field)
    if not isinstance(v, list) or len(v) != n or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v):
        raise ValueError(f"{field}: expected {n} numbers")
    return tuple(float(x) for x in v)


def _box(d: dict, key: str, field: str) -> tuple[float, float, float, float]:
    b = _nums(d, key, 4, field)
    if not (b[0] < b[2] and b[1] < b[3]):
        raise ValueError(f"{field}: expected [xmin, ymin, xmax, ymax] with min < max")
    return b  # type: ignore[return-value]


def _source(d: dict, key: str, field: str) -> Source:
    s = _get(d, key, field)
    if not isinstance(s, dict):
        raise ValueError(f"{field}: expected an object")
    url = _str(s, "url", f"{field}.url")
    if not url.startswith("https://") and not url.startswith("http://"):
        raise ValueError(f"{field}.url: expected an http(s) URL")
    date = _str(s, "date", f"{field}.date")
    if not _DATE.match(date):
        raise ValueError(f"{field}.date: expected YYYY, YYYY-MM or YYYY-MM-DD")
    note = s.get("note", "")
    if not isinstance(note, str):
        raise ValueError(f"{field}.note: expected a string")
    return Source(url=url, date=date, name=_str(s, "name", f"{field}.name"), note=note)


def parse_city(raw: dict) -> CityConfig:
    """Validate a config dict; any problem raises ValueError naming the field."""
    if not isinstance(raw, dict):
        raise ValueError("config: expected an object")
    lo, hi = _nums(raw, "range_m", 2, "range_m")
    if lo >= hi:
        raise ValueError("range_m: lo must be below hi")
    rv = _get(raw, "river", "river")
    tol = _num(rv, "flat_tol_m", "river.flat_tol_m")
    if not 0 <= tol <= 1:
        raise ValueError("river.flat_tol_m: expected 0 to 1 m")
    river = RiverCfg(flat_tol_m=tol, note=_str(rv, "note", "river.note"))
    marks_raw = _get(raw, "marks", "marks")
    if not isinstance(marks_raw, list):
        raise ValueError("marks: expected a list")
    marks = []
    for i, mk in enumerate(marks_raw):
        f = f"marks[{i}]"
        kind = _str(mk, "kind", f"{f}.kind")
        if kind not in MARK_KINDS:
            raise ValueError(f"{f}.kind: expected one of {MARK_KINDS}")
        marks.append(Mark(label=_str(mk, "label", f"{f}.label"), short=_str(mk, "short", f"{f}.short"),
                          m=_num(mk, "m", f"{f}.m"), kind=kind, source=_source(mk, "source", f"{f}.source")))
    today = None
    if raw.get("today") is not None:
        t = raw["today"]
        orange = _nums(t, "offset_range", 2, "today.offset_range")
        if orange[0] > orange[1]:
            raise ValueError("today.offset_range: lo must not exceed hi")
        today = TodayCfg(station_id=_str(t, "station_id", "today.station_id"),
                         offset_m=_num(t, "offset_m", "today.offset_m"),
                         offset_range=orange, source=_source(t, "source", "today.source"))  # type: ignore[arg-type]
    sinae = None
    if raw.get("sinae_check") is not None:
        s = raw["sinae_check"]
        people = _get(s, "people", "sinae_check.people")
        if isinstance(people, bool) or not isinstance(people, int) or people < 0:
            raise ValueError("sinae_check.people: expected a non-negative integer")
        sinae = SinaeCheck(m=_num(s, "m", "sinae_check.m"), people=people,
                           source=_source(s, "source", "sinae_check.source"))
    places_raw = raw.get("place_labels") or []
    if not isinstance(places_raw, list):
        raise ValueError("place_labels: expected a list")
    places = []
    for i, pl in enumerate(places_raw):
        f = f"place_labels[{i}]"
        kind = _str(pl, "kind", f"{f}.kind")
        if kind not in PLACE_KINDS:
            raise ValueError(f"{f}.kind: expected one of {PLACE_KINDS}")
        rot = _num(pl, "rotate", f"{f}.rotate") if pl.get("rotate") is not None else 0.0
        places.append(PlaceLabel(name=_str(pl, "name", f"{f}.name"), x=_num(pl, "x", f"{f}.x"),
                                 y=_num(pl, "y", f"{f}.y"), kind=kind, rotate=rot))
    urls = _get(raw, "dtm_urls", "dtm_urls")
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and u.startswith("http") for u in urls):
        raise ValueError("dtm_urls: expected a non-empty list of URLs")
    return CityConfig(
        slug=_str(raw, "slug", "slug"),
        name=_str(raw, "name", "name"),
        frame_utm=_box(raw, "frame_utm", "frame_utm"),
        phone_frame_utm=_box(raw, "phone_frame_utm", "phone_frame_utm"),
        dtm_urls=list(urls),
        zero_m=_num(raw, "zero_m", "zero_m"),
        zero_source=_source(raw, "zero_source", "zero_source"),
        range_m=(lo, hi),
        river=river,
        marks=marks,
        today=today,
        sinae_check=sinae,
        place_labels=places,
    )


def load_city(slug: str, root: Path = MAPA_DIR) -> CityConfig:
    raw = json.loads((Path(root) / slug / "config.json").read_text(encoding="utf-8"))
    cfg = parse_city(raw)
    if cfg.slug != slug:
        raise ValueError(f"slug: {cfg.slug!r} does not match the folder {slug!r}")
    return cfg


def list_cities(root: Path = MAPA_DIR) -> list[str]:
    """Slugs that have both a config and a built pack."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if (p / "config.json").is_file() and (p / "pack.json.gz").is_file())


@dataclass(frozen=True)
class TodayLevel:
    """Today's river height on the city's gauge, estimated from another station."""
    m: float
    station_name: str
    when_utc: datetime


def today_level(store, cfg: CityConfig, now_utc: datetime) -> TodayLevel | None:
    """Latest level of cfg.today's station minus its offset; None without a `today` config,
    an unknown station, no readings, or a reading stale by the home page's station rule."""
    from climauy.analysis.daily import station_readings
    from climauy.analysis.status import stale_after
    from climauy.catalog import load_catalog

    if cfg.today is None:
        return None
    st = next((s for s in load_catalog() if s.id == cfg.today.station_id), None)
    if st is None:
        return None
    readings = station_readings(store, st, "level")
    if not readings or now_utc - readings[-1].time_utc > stale_after(readings):
        return None
    last = readings[-1]
    return TodayLevel(m=round(last.value - cfg.today.offset_m, 2), station_name=st.name, when_utc=last.time_utc)
