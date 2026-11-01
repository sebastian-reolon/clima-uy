"""Walk the raw archive, dispatch each file to its parser and store the result.

Archive layout: <archive_dir>/<source>/<YYYY-MM-DD>/<HHMMSSZ>_<name>.<ext>.gz,
where date + time is the UTC fetch time. A (relative path, sha256 of the gz
bytes) ledger makes re-runs cheap; the store's upserts make them idempotent.
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from climauy.catalog import Station
from climauy.parsers import ana, cpc, ctm, flag_outliers, glofas, ina
from climauy.store import Store

log = logging.getLogger(__name__)

# GloFAS archive file name -> grid cell, as written in the catalog's "glofas" source.
GLOFAS_CELLS = {"uruguaiana": "-29.675,-57.075", "salto": "-31.375,-58.025"}

_NAME = re.compile(r"^(\d{6})Z_(.+)\.gz$")
_ANA = re.compile(r"^(\d+)_.+\.xml$")
_INA_OBS = re.compile(r"^obs_\d+_series(\d+)\.json$")
_INA_PRONO = re.compile(r"^cal\d+_series(\d+)\.json$")
_CPC_PARSERS = {"oni.txt": cpc.parse_oni, "roni.txt": cpc.parse_roni, "wksst9120.txt": cpc.parse_weekly}


@dataclass
class IngestReport:
    files_seen: int = 0
    files_new: int = 0
    rows: int = 0
    errors: list[str] = field(default_factory=list)
    # Recognised files whose series/station is not in the catalog (not errors, not marked processed).
    files_skipped: int = 0


class _Skip(Exception):
    """The file is valid but has no place in the catalog."""


def _fetched_utc(day: str, hms: str) -> datetime:
    return datetime.strptime(day + hms, "%Y-%m-%d%H%M%S").replace(tzinfo=timezone.utc)


def _pick(catalog: list[Station], pred) -> Station:
    for s in catalog:
        if pred(s):
            return s
    raise _Skip


def _handle(source: str, name: str, data: bytes, fetched: datetime, catalog: list[Station], store: Store) -> int | None:
    """Parse and store one file. Returns rows written, or None when the file is ignored."""
    if source == "ana":
        m = _ANA.match(name)
        if not m:
            return None
        st = _pick(catalog, lambda s: s.sources.get("ana") == m.group(1))
        return store.upsert_obs(flag_outliers(ana.parse_telemetry(data, st.id)))
    if source == "ina":
        m = _INA_OBS.match(name)
        if not m:
            return None  # e.g. estaciones_uruguay.json
        sid = m.group(1)
        st = _pick(catalog, lambda s: sid in (s.sources.get("ina_level"), s.sources.get("ina_q")))
        variable = "level" if st.sources.get("ina_level") == sid else "discharge"
        return store.upsert_obs(flag_outliers(ina.parse_observations(data, st.id, variable)))
    if source == "ina_prono":
        m = _INA_PRONO.match(name)
        if not m:
            return None
        st = _pick(catalog, lambda s: m.group(1) in s.forecast_series)
        return store.upsert_forecasts(ina.parse_forecast_run(data, st.id, "level"))
    if source == "ctm":
        if not name.startswith("caudales_niveles"):
            return None
        dam, obs = ctm.parse_report(data)
        return store.upsert_dam(dam) + store.upsert_obs(flag_outliers(obs))
    if source == "glofas":
        cell = GLOFAS_CELLS.get(name.removesuffix(".json"))
        if cell is None:
            return None
        st = _pick(catalog, lambda s: s.sources.get("glofas") == cell)
        return store.upsert_forecasts(glofas.parse(data, st.id, fetched))
    if source == "cpc":
        parser = _CPC_PARSERS.get(name)
        if parser is None:
            return None
        return store.upsert_indices(parser(data.decode("utf-8", errors="replace")))
    return None  # ina_meta, adme, dinagua and anything unknown


def ingest_archive(archive_dir: Path, store: Store, catalog: list[Station]) -> IngestReport:
    report = IngestReport()
    for path in sorted(archive_dir.glob("*/*/*.gz")):
        rel = path.relative_to(archive_dir)
        source, day = rel.parts[0], rel.parts[1]
        m = _NAME.match(path.name)
        if not m or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        key = rel.as_posix()
        if store.is_file_processed(key, sha):
            report.files_seen += 1
            continue
        try:
            data = gzip.decompress(raw)
            rows = _handle(source, m.group(2).removesuffix(".gz"), data, _fetched_utc(day, m.group(1)), catalog, store)
        except _Skip:
            report.files_seen += 1
            report.files_skipped += 1
            continue
        except Exception as exc:  # one bad file must not stop the run
            report.files_seen += 1
            report.errors.append(f"{key}: {type(exc).__name__}: {exc}")
            log.warning("ingest failed for %s: %s", key, exc)
            continue
        if rows is None:
            continue
        report.files_seen += 1
        report.files_new += 1
        report.rows += rows
        store.mark_file_processed(key, sha)
    return report
