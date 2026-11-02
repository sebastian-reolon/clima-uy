"""Data exports, the daily snapshot and the commit on the gh-pages working tree.

Exported times are UTC ('Z'); days and months are America/Montevideo, so a
reading at 02:00Z on the 1st belongs to the last day of the previous month.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .analysis.daily import family_for, station_readings
from .analysis.status import compute_status
from .catalog import Station
from .narrative import river_summary
from .store import Store
from .timeutil import MONTEVIDEO, local_day

# First local day of the raw archive: monthly raw and forecast files start here.
ARCHIVE_START = date(2026, 11, 1)
# Full exports run once per local day, on the first run at or after this local time.
FULL_EXPORT_AFTER = time(0, 30)
_FULL_KEY = "last_full_export_day"
# Sources whose readings are republished in full (daily and raw files). The
# licence review (docs/licencias.md) decides this set; removing a source here
# keeps it in latest.json (derived states) but drops its series files.
FULL_SERIES_SOURCES = frozenset({"ana", "ana_hist", "ina", "ctm"})
INDEX_NAMES = ("RONI", "ONI", "NINO34_WEEKLY")
VARIABLES = ("level", "discharge")

STATION_COLUMNS = ["id", "name", "country", "river", "lat", "lon", "river_km", "km_source", "sources",
                   "represents", "alert_m", "evac_m", "cota_source", "tributary_of", "order"]
DAILY_COLUMNS = ["date_local", "value", "n_readings"]
RAW_COLUMNS = ["station_id", "variable", "time_utc", "value", "source", "quality"]
FORECAST_COLUMNS = ["issuer", "station_id", "variable", "issued_utc", "valid_utc", "qualifier", "value"]
INDEX_COLUMNS = ["name", "period", "value"]


def _utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell(v) -> str:
    return "" if v is None else str(v)


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows([_cell(v) for v in r] for r in rows)
    tmp.replace(path)
    return path


def _json_default(v):
    if isinstance(v, datetime):
        return _utc(v)
    if isinstance(v, date):
        return v.isoformat()
    raise TypeError(f"not serializable: {type(v).__name__}")


# ---------------------------------------------------------------- full-export schedule

def full_export_due(store: Store, now_utc: datetime) -> bool:
    """True on the first run at or after 00:30 Montevideo of a day without a full export.

    A store that never exported is always due, so the first run publishes every file.
    """
    last = store.meta_get(_FULL_KEY)
    if last is None:
        return True
    local = now_utc.astimezone(MONTEVIDEO)
    return local.time() >= FULL_EXPORT_AFTER and date.fromisoformat(last) < local.date()


def mark_full_export(store: Store, now_utc: datetime) -> None:
    store.meta_set(_FULL_KEY, local_day(now_utc).isoformat())


# ---------------------------------------------------------------- exports

def _station_rows(catalog: Sequence[Station]) -> list[list]:
    rows = []
    for st in catalog:
        sources = ";".join(f"{k}:{v}" for k, v in sorted(st.sources.items()))
        rows.append([st.id, st.name, st.country, st.river, st.lat, st.lon, st.river_km, st.km_source, sources,
                     st.represents, st.alert_m, st.evac_m, st.cota_source, st.tributary_of, st.order])
    return rows


def _daily_rows(store: Store, station: Station, variable: str) -> list[list]:
    """Per local day the mean and count of the winning source (same choice as family_daily)."""
    family = family_for(station, variable)
    by_day: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for o in station_readings(store, station, variable):
        by_day[local_day(o.time_utc)][o.source].append(o.value)
    rows = []
    for d in sorted(by_day):
        src = next(s for s in family if s in by_day[d])
        values = by_day[d][src]
        rows.append([d.isoformat(), round(sum(values) / len(values), 4), len(values)])
    return rows


def _month_starts(today: date) -> list[date]:
    m, out = date(ARCHIVE_START.year, ARCHIVE_START.month, 1), []
    while m <= today:
        out.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    return out


def _local_midnight_utc(d: date) -> datetime:
    return datetime.combine(d, time(0), MONTEVIDEO).astimezone(timezone.utc)


def _month_bounds(m: date) -> tuple[datetime, datetime]:
    start = max(m, ARCHIVE_START)
    nxt = (m + timedelta(days=32)).replace(day=1)
    return _local_midnight_utc(start), _local_midnight_utc(nxt)


def _latest(store: Store, catalog: Sequence[Station], now_utc: datetime) -> dict:
    statuses = compute_status(store, catalog, now_utc)
    by_id = {s.id: s for s in catalog}
    stations = [{"name": by_id[s.station_id].name, **dataclasses.asdict(s)} for s in statuses]
    return {"generated_utc": _utc(now_utc), "summary": river_summary(statuses, catalog), "stations": stations}


def export_data(store: Store, catalog: Sequence[Station], out_dir: Path, now_utc: datetime, full: bool) -> list[Path]:
    """Write the data files under out_dir/data; returns the files written this run.

    Always: data/rio/stations.csv and data/rio/latest.json. With full also the
    daily series, the monthly raw and forecast files and the ENSO indices.
    Monthly files of months before the current local month are append-only:
    once written they are never rewritten.
    """
    rio = out_dir / "data" / "rio"
    written = [_write_csv(rio / "stations.csv", STATION_COLUMNS, _station_rows(catalog))]
    latest = rio / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(_latest(store, catalog, now_utc), ensure_ascii=False, indent=1,
                                 default=_json_default) + "\n", encoding="utf-8")
    written.append(latest)
    if not full:
        return written

    for st in catalog:
        for variable in VARIABLES:
            # Same predicate as the Datos page, so every listed link has a file.
            if store.latest_obs_time(st.id, variable) is None:
                continue
            rows = _daily_rows(store, st, variable) if set(family_for(st, variable)) & FULL_SERIES_SOURCES else []
            written.append(_write_csv(rio / "daily" / f"{st.id}_{variable}.csv", DAILY_COLUMNS, rows))

    today = local_day(now_utc)
    current = date(today.year, today.month, 1)
    for m in _month_starts(today):
        key = f"{m.year}-{m.month:02d}"
        raw, fc = rio / "raw" / f"{key}.csv", rio / "forecasts" / f"{key}.csv"
        start, end = _month_bounds(m)
        if m == current or not raw.exists():
            obs = [o for o in store.obs_between(start, end) if o.source in FULL_SERIES_SOURCES]
            written.append(_write_csv(raw, RAW_COLUMNS, (
                [o.station_id, o.variable, _utc(o.time_utc), o.value, o.source, o.quality] for o in obs)))
        if m == current or not fc.exists():
            written.append(_write_csv(fc, FORECAST_COLUMNS, (
                [f.issuer, f.station_id, f.variable, _utc(f.issued_utc), _utc(f.valid_utc), f.qualifier, f.value]
                for f in store.forecasts_issued_between(start, end))))

    written.append(_write_csv(out_dir / "data" / "enso" / "indices.csv", INDEX_COLUMNS, (
        [i.name, i.period, i.value] for name in INDEX_NAMES for i in store.indices(name))))
    return written


# ---------------------------------------------------------------- snapshot

SNAPSHOT_PAGES = ("index.html", "rio")
SNAPSHOT_DATA = ("data/rio/latest.json", "data/rio/stations.csv")
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _rewrite_links(html: str, base: str, day: str) -> str:
    """Point site links at this snapshot's own copies.

    Static assets stay shared, links to other snapshots stay, and the Datos
    page (live by nature) is replaced by the list of snapshots.
    """
    pattern = re.compile(r'\b(href|src)="' + re.escape(base) + r'/([^"]*)"')

    def sub(m: re.Match) -> str:
        attr, rest = m.group(1), m.group(2)
        if rest.startswith(("static/", "snapshots/")):
            target = rest
        elif rest.startswith("datos/"):
            target = "snapshots/"
        else:
            target = f"snapshots/{day}/{rest}"
        return f'{attr}="{base}/{target}"'

    return pattern.sub(sub, html)


def write_snapshot(out_dir: Path, day_local: date, base_url: str = "/clima-uy") -> Path:
    """Copy the home page, the river pages and today's data into snapshots/<day>/.

    The same day is overwritten on every run; past days are never touched.
    Also regenerates snapshots/index.html from the day folders on disk.
    """
    from .site.build import render_snapshot_index  # jinja2 is only needed here

    base = base_url.rstrip("/")
    day = day_local.isoformat()
    snaps = out_dir / "snapshots"
    dest = snaps / day
    tmp = snaps / f".{day}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    for rel in SNAPSHOT_PAGES + SNAPSHOT_DATA:
        src = out_dir / rel
        if src.is_dir():
            shutil.copytree(src, tmp / rel)
        else:
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp / rel)
    for page in tmp.rglob("*.html"):
        page.write_text(_rewrite_links(page.read_text(encoding="utf-8"), base, day), encoding="utf-8")
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)

    days = sorted((date.fromisoformat(p.name) for p in snaps.iterdir() if p.is_dir() and _DAY_DIR.match(p.name)),
                  reverse=True)
    render_snapshot_index(out_dir, days, base_url=base_url)
    return dest


# ---------------------------------------------------------------- git

def _git(pages_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=pages_dir, check=True, capture_output=True, text=True)


def commit_and_push(pages_dir: Path, message: str) -> bool:
    """Stage everything in the gh-pages working tree; commit and push if anything changed.

    Returns False (and commits nothing) when there are no changes. Git errors raise.
    """
    _git(pages_dir, "add", "-A")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=pages_dir).returncode == 0:
        return False
    _git(pages_dir, "commit", "-q", "-m", message)
    _git(pages_dir, "push", "-q", "origin", "gh-pages")
    return True
