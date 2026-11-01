"""Walk the raw archive, dispatch each file to its parser and store the result.

Archive layout: <archive_dir>/<source>/<YYYY-MM-DD>/<HHMMSSZ>_<name>.<ext>.gz,
where date + time is the UTC fetch time. A per-file ledger (keyed by relative
path) makes re-runs cheap; the store's upserts make them idempotent.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from climauy.catalog import Station
from climauy.parsers import ana, cpc, ctm, flag_outliers, glofas, ina
from climauy.store import Obs, Store

log = logging.getLogger(__name__)

# GloFAS archive file name -> grid cell, as written in the catalog's "glofas" source.
GLOFAS_CELLS = {"uruguaiana": "-29.675,-57.075", "salto": "-31.375,-58.025"}

_NAME = re.compile(r"^(\d{6})Z_(.+)\.gz$")
_ANA = re.compile(r"^(\d+)_.+\.xml$")
_INA_OBS = re.compile(r"^obs_\d+_series(\d+)\.json$")
_INA_PRONO = re.compile(r"^cal\d+_series(\d+)\.json$")
_CPC_PARSERS = {"oni.txt": cpc.parse_oni, "roni.txt": cpc.parse_roni, "wksst9120.txt": cpc.parse_weekly}


# flag_outliers looks +-4 readings around each point (window 9).
_HALF = 4


@dataclass
class IngestReport:
    files_seen: int = 0
    files_new: int = 0
    rows: int = 0
    errors: list[str] = field(default_factory=list)
    # Recognised files whose series/station is not in the catalog (not errors, not processed).
    files_skipped: int = 0
    # Files that failed for the first time in this run (retries are only in `errors`).
    files_failed_new: int = 0


class _Skip(Exception):
    """The file is valid but has no place in the catalog."""


def _fetched_utc(day: str, hms: str) -> datetime:
    return datetime.strptime(day + hms, "%Y-%m-%d%H%M%S").replace(tzinfo=timezone.utc)


def _pick(catalog: list[Station], pred) -> Station:
    for s in catalog:
        if pred(s):
            return s
    raise _Skip


def _catalog_hash(catalog: list[Station]) -> str:
    blob = json.dumps([asdict(s) for s in catalog], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _store_flagged(store: Store, batch: list[Obs]) -> int:
    """Upsert a batch after flagging outliers against the stored series.

    Each series of the batch is merged with its stored neighbours (batch wins on
    equal timestamps) and flagged as a whole, so the result does not depend on
    which file arrives first. Stored rows whose quality changes are updated too,
    but only those with a full window (the outermost readings are context only).
    """
    series: dict[tuple, list[Obs]] = {}
    for o in batch:
        series.setdefault((o.station_id, o.variable, o.source), []).append(o)
    out: list[Obs] = []
    for (station, variable, source), rows in series.items():
        by_time = {o.time_utc: o for o in rows}
        times = sorted(by_time)
        stored = store.obs_around(station, variable, source, times[0], times[-1], 2 * _HALF)
        merged = {o.time_utc: o for o in stored}
        merged.update(by_time)
        ordered = [merged[t] for t in sorted(merged)]
        flagged = flag_outliers(ordered)
        pos = {o.time_utc: i for i, o in enumerate(flagged)}
        batch_idx = [pos[t] for t in times]
        lo, hi = min(batch_idx) - _HALF, max(batch_idx) + _HALF
        for i, o in enumerate(flagged):
            if o.time_utc in by_time:
                out.append(replace(by_time[o.time_utc], quality=o.quality))
            elif lo <= i <= hi and o.quality != merged[o.time_utc].quality:
                out.append(o)
    store.upsert_obs(out)
    return len(batch)


def _route(source: str, name: str):
    """Return handler(data, fetched, catalog, store) -> rows for a known file, or None to ignore."""
    if source == "ana":
        m = _ANA.match(name)
        if m:
            def h(data, fetched, catalog, store):
                st = _pick(catalog, lambda s: s.sources.get("ana") == m.group(1))
                return _store_flagged(store, ana.parse_telemetry(data, st.id))
            return h
    elif source == "ina":
        m = _INA_OBS.match(name)
        if m:
            def h(data, fetched, catalog, store):
                sid = m.group(1)
                st = _pick(catalog, lambda s: sid in (s.sources.get("ina_level"), s.sources.get("ina_q")))
                variable = "level" if st.sources.get("ina_level") == sid else "discharge"
                return _store_flagged(store, ina.parse_observations(data, st.id, variable))
            return h
    elif source == "ina_prono":
        m = _INA_PRONO.match(name)
        if m:
            def h(data, fetched, catalog, store):
                st = _pick(catalog, lambda s: m.group(1) in s.forecast_series)
                return store.upsert_forecasts(ina.parse_forecast_run(data, st.id, "level"))
            return h
    elif source == "ctm":
        if name.startswith("caudales_niveles"):
            def h(data, fetched, catalog, store):
                dam, obs = ctm.parse_report(data)
                return store.upsert_dam(dam) + _store_flagged(store, obs)
            return h
    elif source == "glofas":
        cell = GLOFAS_CELLS.get(name.removesuffix(".json"))
        if cell:
            def h(data, fetched, catalog, store):
                st = _pick(catalog, lambda s: s.sources.get("glofas") == cell)
                return store.upsert_forecasts(glofas.parse(data, st.id, fetched))
            return h
    elif source == "cpc":
        parser = _CPC_PARSERS.get(name)
        if parser:
            def h(data, fetched, catalog, store):
                return store.upsert_indices(parser(data.decode("utf-8", errors="replace")))
            return h
    return None  # ina_meta, adme, dinagua, estaciones_uruguay and anything unknown


def ingest_archive(archive_dir: Path, store: Store, catalog: list[Station]) -> IngestReport:
    """Ingest new archive files.

    Archive files are immutable once written, so the relative path alone identifies
    an already handled file: ledgered files are never read again. Ledger statuses:
    processed, ignored (out of scope), skipped (not in the catalog; re-evaluated
    when the catalog changes) and failed (retried every run).
    """
    report = IngestReport()
    cat_hash = _catalog_hash(catalog)
    for path in sorted(archive_dir.glob("*/*/*.gz")):
        rel = path.relative_to(archive_dir)
        source, day = rel.parts[0], rel.parts[1]
        key = rel.as_posix()
        entry = store.ledger_get(key)
        attempts = 0
        if entry:
            status, detail, attempts = entry
            if status == "ignored":
                continue
            if status == "processed":
                report.files_seen += 1
                continue
            if status == "skipped" and detail == cat_hash:
                report.files_seen += 1
                report.files_skipped += 1
                continue
        m = _NAME.match(path.name)
        handler = _route(source, m.group(2).removesuffix(".gz")) if m and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None
        if handler is None:
            store.ledger_set(key, "", "ignored")
            continue
        report.files_seen += 1
        sha = ""
        try:
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            rows = handler(gzip.decompress(raw), _fetched_utc(day, m.group(1)), catalog, store)
        except _Skip:
            report.files_skipped += 1
            store.ledger_set(key, sha, "skipped", cat_hash)
            continue
        except Exception as exc:  # one bad file must not stop the run
            report.errors.append(f"{key}: {type(exc).__name__}: {exc}")
            if entry is None or entry[0] != "failed":
                report.files_failed_new += 1
            store.ledger_set(key, sha, "failed", f"{type(exc).__name__}: {exc}", attempts + 1)
            log.warning("ingest failed for %s: %s", key, exc)
            continue
        report.files_new += 1
        report.rows += rows
        store.ledger_set(key, sha, "processed")
    return report
