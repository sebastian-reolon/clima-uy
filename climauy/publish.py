"""Data exports, the daily snapshot and the commit on the gh-pages working tree.

Exported times are UTC ('Z'); days and months are America/Montevideo, so a
reading at 02:00Z on the 1st belongs to the last day of the previous month.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import os
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
# Sources whose readings, and issuers whose forecasts, are republished in full
# (daily, raw and forecast files), always with attribution; see
# docs/licencias.md. ANA, GloFAS and NOAA allow it in writing. INA,
# Prefectura and CTM publish no reuse terms: republishing their public data
# is the project's criterion (owner's decision), withdrawn on request by
# removing the source here. CPC indices are always exported.
FULL_SERIES_SOURCES = frozenset({"ana", "ana_hist", "ina", "ctm"})
FULL_FORECAST_ISSUERS = frozenset({"ina", "glofas"})
_FINAL_KEY = "month_final:"  # + "raw:YYYY-MM" / "forecasts:YYYY-MM"
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


def series_exported(store: Store, station: Station, variable: str) -> bool:
    """True when this station and variable get a daily CSV (used by export and the Datos page)."""
    sources = [s for s in family_for(station, variable) if s in FULL_SERIES_SOURCES]
    return bool(sources) and store.has_obs(station.id, variable, sources)


def _daily_rows(store: Store, station: Station, variable: str, today: date) -> list[list]:
    """Per local day the mean and count of the winning source (same choice as family_daily),
    among the sources republished in full. The local day in progress is left out: its
    mean would change on every run."""
    family = [s for s in family_for(station, variable) if s in FULL_SERIES_SOURCES]
    by_day: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for o in station_readings(store, station, variable):
        if o.source in family and local_day(o.time_utc) < today:
            by_day[local_day(o.time_utc)][o.source].append(o.value)
    rows = []
    for d in sorted(by_day):
        src = next(s for s in family if s in by_day[d])
        values = by_day[d][src]
        rows.append([d.isoformat(), round(sum(values) / len(values), 4), len(values)])
    return rows


def month_starts(today: date) -> list[date]:
    m, out = date(ARCHIVE_START.year, ARCHIVE_START.month, 1), []
    while m <= today:
        out.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    return out


def _month_due(store: Store, key: str, path: Path, is_current: bool) -> bool:
    """Whether a monthly file is (re)written now: always for the current month, once more for a closed one."""
    return is_current or not path.exists() or store.meta_get(_FINAL_KEY + key) is None


def apply_marks(store: Store, marks: Iterable[str]) -> None:
    """Record closed months as final; call only once their files are published."""
    for key in marks:
        store.meta_set(_FINAL_KEY + key, "1")


def _local_midnight_utc(d: date) -> datetime:
    return datetime.combine(d, time(0), MONTEVIDEO).astimezone(timezone.utc)


def _month_bounds(m: date) -> tuple[datetime, datetime]:
    start = max(m, ARCHIVE_START)
    nxt = (m + timedelta(days=32)).replace(day=1)
    return _local_midnight_utc(start), _local_midnight_utc(nxt)


def _latest(store: Store, catalog: Sequence[Station], now_utc: datetime) -> dict:
    """latest.json: run time, today's sentence, each station's status.

    last_data_utc is the newest reading among the stations that are up to date
    (the page's "Último dato del río"), or null when none is; the freshness
    alert reads it. Each station carries latest_time_utc and latest_source.
    """
    statuses = compute_status(store, catalog, now_utc)
    by_id = {s.id: s for s in catalog}
    stations = [{"name": by_id[s.station_id].name, **dataclasses.asdict(s)} for s in statuses]
    fresh = [s.latest_time_utc for s in statuses if not s.stale and s.latest_time_utc is not None]
    return {"generated_utc": _utc(now_utc), "last_data_utc": _utc(max(fresh)) if fresh else None,
            "summary": river_summary(statuses, catalog), "stations": stations}


def export_data(store: Store, catalog: Sequence[Station], out_dir: Path, now_utc: datetime, full: bool,
                marks: list[str] | None = None) -> list[Path]:
    """Write the data files under out_dir/data; returns the files written this run.

    Always: data/rio/stations.csv and data/rio/latest.json. With full also the
    daily series (without the local day in progress), the monthly raw and
    forecast files and the ENSO indices. Monthly files are rewritten while
    their month is the current local month and one final time on the first
    full run after it closes (so the last day is complete); after that they
    are frozen. A missing file is always written.

    The "closed month is final" marks are appended to `marks` for the caller
    to apply with apply_marks once the files are published; with marks=None
    they are applied right away.
    """
    pending: list[str] = []
    rio = out_dir / "data" / "rio"
    written = [_write_csv(rio / "stations.csv", STATION_COLUMNS, _station_rows(catalog))]
    latest = rio / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    tmp = latest.with_suffix(".json.tmp")  # atomic: the freshness check may read it at any time
    tmp.write_text(json.dumps(_latest(store, catalog, now_utc), ensure_ascii=False, indent=1,
                              default=_json_default) + "\n", encoding="utf-8")
    tmp.replace(latest)
    written.append(latest)
    if not full:
        return written

    for st in catalog:
        for variable in VARIABLES:
            # Same predicate as the Datos page, so every listed link has a file.
            if series_exported(store, st, variable):
                written.append(_write_csv(rio / "daily" / f"{st.id}_{variable}.csv", DAILY_COLUMNS,
                                          _daily_rows(store, st, variable, local_day(now_utc))))
    # A series no longer exported (licence change, station removed) leaves no file behind.
    daily_now = set(written)
    for stale in (rio / "daily").glob("*.csv"):
        if stale not in daily_now:
            stale.unlink()

    today = local_day(now_utc)
    current = date(today.year, today.month, 1)
    for m in month_starts(today):
        key = f"{m.year}-{m.month:02d}"
        raw, fc = rio / "raw" / f"{key}.csv", rio / "forecasts" / f"{key}.csv"
        start, end = _month_bounds(m)
        if _month_due(store, f"raw:{key}", raw, m == current):
            obs = [o for o in store.obs_between(start, end) if o.source in FULL_SERIES_SOURCES]
            written.append(_write_csv(raw, RAW_COLUMNS, (
                [o.station_id, o.variable, _utc(o.time_utc), o.value, o.source, o.quality] for o in obs)))
            if m != current:
                pending.append(f"raw:{key}")
        if _month_due(store, f"forecasts:{key}", fc, m == current):
            written.append(_write_csv(fc, FORECAST_COLUMNS, (
                [f.issuer, f.station_id, f.variable, _utc(f.issued_utc), _utc(f.valid_utc), f.qualifier, f.value]
                for f in store.forecasts_issued_between(start, end) if f.issuer in FULL_FORECAST_ISSUERS)))
            if m != current:
                pending.append(f"forecasts:{key}")

    written.append(_write_csv(out_dir / "data" / "enso" / "indices.csv", INDEX_COLUMNS, (
        [i.name, i.period, i.value] for name in INDEX_NAMES for i in store.indices(name))))
    if marks is None:
        apply_marks(store, pending)
    else:
        marks.extend(pending)
    return written


# ---------------------------------------------------------------- snapshot

SNAPSHOT_PAGES = ("index.html", "rio", "nino")
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
    """Copy the home page, the river pages, the El Niño page and today's data into snapshots/<day>/.

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


# ---------------------------------------------------------------- infra scan

# Strings that must never be published. Built from pieces so this file does
# not match itself when the repository is scanned.
_FORBIDDEN = [
    re.compile(re.escape("/ro" + "ot/")),
    re.compile("het" + "zner", re.I),
    re.compile(re.escape("@gm" + "ail"), re.I),
    re.compile("tele" + "gram", re.I),
    re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?!\d|\.\d)"),
]


def find_infra(paths: Iterable[Path]) -> list[str]:
    """'path:line: match' for every forbidden string in the given files (binary files decoded leniently)."""
    hits = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for pat in _FORBIDDEN:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path}:{line}: {m.group(0)!r}")
    return hits


def scan_tree(root: Path) -> list[str]:
    """find_infra over every file under root, skipping .git."""
    return find_infra(p for p in sorted(root.rglob("*")) if ".git" not in p.relative_to(root).parts)


# ---------------------------------------------------------------- git

class PublishError(RuntimeError):
    pass


# Inherited variables that could point git at another repository, or override
# the commit identity given with -c user.name / user.email.
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                 "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")


def _git(pages_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    return subprocess.run(["git", *args], cwd=pages_dir, env=env, check=check, capture_output=True, text=True)


def commit_and_push(pages_dir: Path, message: str) -> bool:
    """Stage everything in the gh-pages working tree; commit and push if anything changed.

    Refuses unless pages_dir is the top of its own repository with gh-pages
    checked out, and unless CLIMAUY_GIT_NAME and CLIMAUY_GIT_EMAIL are set
    (the commit author and committer come only from them: inherited
    GIT_AUTHOR_* / GIT_COMMITTER_* names and emails are dropped). Returns False, committing
    nothing, when there are no changes. Git errors raise.
    """
    name, email = os.environ.get("CLIMAUY_GIT_NAME", ""), os.environ.get("CLIMAUY_GIT_EMAIL", "")
    if not name or not email:
        raise PublishError("CLIMAUY_GIT_NAME and CLIMAUY_GIT_EMAIL must be set to commit the pages")
    top = _git(pages_dir, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve() != pages_dir.resolve():
        raise PublishError(f"{pages_dir} is not the top of its own git repository (top is {top})")
    branch = _git(pages_dir, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if branch != "gh-pages":
        raise PublishError(f"{pages_dir} has {branch!r} checked out, expected 'gh-pages'")
    _git(pages_dir, "add", "-A")
    if _git(pages_dir, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    _git(pages_dir, "-c", f"user.name={name}", "-c", f"user.email={email}", "-c", "user.useConfigOnly=true",
         "commit", "-q", "-m", message)
    _git(pages_dir, "push", "-q", "origin", "HEAD:gh-pages")
    return True
