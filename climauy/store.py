"""SQLite store for observations, forecasts, dam releases and climate indices.

Datetimes are stored as UTC ISO-8601 strings with a trailing 'Z' at second
resolution (fixed width, so string order equals time order) and are returned
as timezone-aware UTC datetimes.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Sequence

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _enc(t: datetime) -> str:
    if t.tzinfo is None:
        raise ValueError("naive datetime: store only timezone-aware datetimes")
    return t.astimezone(timezone.utc).strftime(_FMT)


def _dec(s: str) -> datetime:
    return datetime.strptime(s, _FMT).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Obs:
    station_id: str
    variable: Literal["level", "discharge", "rain"]
    time_utc: datetime
    value: float
    source: str
    quality: Literal["ok", "suspect"] = "ok"


@dataclass(frozen=True)
class Forecast:
    issuer: Literal["ina", "glofas"]
    station_id: str
    variable: str
    issued_utc: datetime
    valid_utc: datetime
    qualifier: str  # ina: main|inferior|superior; glofas: median|p25|p75|min|max|member_NN
    value: float


@dataclass(frozen=True)
class DamDay:
    date_local: date
    q_turb: float
    q_vert: float
    q_total: float
    vol_machadinho_pct: float | None
    vol_ita_pct: float | None


@dataclass(frozen=True)
class IndexValue:
    name: Literal["ONI", "RONI", "NINO34_WEEKLY"]
    period: str
    value: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS obs (
    station_id TEXT NOT NULL,
    variable   TEXT NOT NULL,
    time_utc   TEXT NOT NULL,
    value      REAL NOT NULL,
    source     TEXT NOT NULL,
    quality    TEXT NOT NULL DEFAULT 'ok',
    UNIQUE (station_id, variable, time_utc, source)
);
CREATE INDEX IF NOT EXISTS idx_obs_series ON obs (station_id, variable, time_utc);
CREATE TABLE IF NOT EXISTS forecast (
    issuer     TEXT NOT NULL,
    station_id TEXT NOT NULL,
    variable   TEXT NOT NULL,
    issued_utc TEXT NOT NULL,
    valid_utc  TEXT NOT NULL,
    qualifier  TEXT NOT NULL,
    value      REAL NOT NULL,
    UNIQUE (issuer, station_id, variable, issued_utc, valid_utc, qualifier)
);
CREATE TABLE IF NOT EXISTS dam_day (
    date_local         TEXT PRIMARY KEY,
    q_turb             REAL NOT NULL,
    q_vert             REAL NOT NULL,
    q_total            REAL NOT NULL,
    vol_machadinho_pct REAL,
    vol_ita_pct        REAL
);
CREATE TABLE IF NOT EXISTS climate_index (
    name   TEXT NOT NULL,
    period TEXT NOT NULL,
    value  REAL NOT NULL,
    PRIMARY KEY (name, period)
);
CREATE TABLE IF NOT EXISTS file_ledger (
    path     TEXT PRIMARY KEY,
    sha      TEXT NOT NULL,
    status   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS backfill_chunk (
    source       TEXT NOT NULL,
    series       TEXT NOT NULL,
    variable     TEXT NOT NULL,
    chunk_start  TEXT NOT NULL,
    completed_utc TEXT NOT NULL,
    PRIMARY KEY (source, series, variable, chunk_start)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _write(self, sql: str, rows: Iterable[tuple]) -> int:
        rows = list(rows)  # encode everything first so a bad row aborts before writing
        with self._conn:
            self._conn.executemany(sql, rows)
        return len(rows)

    def upsert_obs(self, rows: list[Obs]) -> int:
        return self._write(
            "INSERT INTO obs (station_id, variable, time_utc, value, source, quality) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (station_id, variable, time_utc, source) "
            "DO UPDATE SET value=excluded.value, quality=excluded.quality",
            ((o.station_id, o.variable, _enc(o.time_utc), o.value, o.source, o.quality) for o in rows),
        )

    def upsert_forecasts(self, rows: list[Forecast]) -> int:
        return self._write(
            "INSERT INTO forecast (issuer, station_id, variable, issued_utc, valid_utc, qualifier, value) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (issuer, station_id, variable, issued_utc, valid_utc, qualifier) "
            "DO UPDATE SET value=excluded.value",
            (
                (f.issuer, f.station_id, f.variable, _enc(f.issued_utc), _enc(f.valid_utc), f.qualifier, f.value)
                for f in rows
            ),
        )

    def upsert_dam(self, rows: list[DamDay]) -> int:
        return self._write(
            "INSERT INTO dam_day (date_local, q_turb, q_vert, q_total, vol_machadinho_pct, vol_ita_pct) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (date_local) DO UPDATE SET q_turb=excluded.q_turb, q_vert=excluded.q_vert, "
            "q_total=excluded.q_total, vol_machadinho_pct=excluded.vol_machadinho_pct, "
            "vol_ita_pct=excluded.vol_ita_pct",
            (
                (d.date_local.isoformat(), d.q_turb, d.q_vert, d.q_total, d.vol_machadinho_pct, d.vol_ita_pct)
                for d in rows
            ),
        )

    def upsert_indices(self, rows: list[IndexValue]) -> int:
        return self._write(
            "INSERT INTO climate_index (name, period, value) VALUES (?,?,?) "
            "ON CONFLICT (name, period) DO UPDATE SET value=excluded.value",
            ((i.name, i.period, i.value) for i in rows),
        )

    def obs(
        self,
        station_id: str,
        variable: str,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        include_suspect: bool = False,
    ) -> list[Obs]:
        """Observations ordered by time; start and end are inclusive."""
        sql = (
            "SELECT station_id, variable, time_utc, value, source, quality FROM obs "
            "WHERE station_id=? AND variable=?"
        )
        args: list = [station_id, variable]
        if start_utc is not None:
            sql += " AND time_utc>=?"
            args.append(_enc(start_utc))
        if end_utc is not None:
            sql += " AND time_utc<=?"
            args.append(_enc(end_utc))
        if not include_suspect:
            sql += " AND quality='ok'"
        sql += " ORDER BY time_utc, source"
        return [Obs(r[0], r[1], _dec(r[2]), r[3], r[4], r[5]) for r in self._conn.execute(sql, args)]

    def forecasts(self, issuer: str, station_id: str | None = None) -> list[Forecast]:
        sql = (
            "SELECT issuer, station_id, variable, issued_utc, valid_utc, qualifier, value "
            "FROM forecast WHERE issuer=?"
        )
        args: list = [issuer]
        if station_id is not None:
            sql += " AND station_id=?"
            args.append(station_id)
        sql += " ORDER BY station_id, variable, issued_utc, valid_utc, qualifier"
        return [Forecast(r[0], r[1], r[2], _dec(r[3]), _dec(r[4]), r[5], r[6]) for r in self._conn.execute(sql, args)]

    def dam_days(self) -> list[DamDay]:
        rows = self._conn.execute("SELECT * FROM dam_day ORDER BY date_local")
        return [DamDay(date.fromisoformat(r[0]), *r[1:]) for r in rows]

    def indices(self, name: str) -> list[IndexValue]:
        rows = self._conn.execute(
            "SELECT name, period, value FROM climate_index WHERE name=? ORDER BY period", (name,)
        )
        return [IndexValue(*r) for r in rows]

    def latest_obs_time(self, station_id: str, variable: str) -> datetime | None:
        """Time of the newest non-suspect observation, or None."""
        row = self._conn.execute(
            "SELECT MAX(time_utc) FROM obs WHERE station_id=? AND variable=? AND quality='ok'",
            (station_id, variable),
        ).fetchone()
        return _dec(row[0]) if row and row[0] else None

    def ledger_get(self, path: str) -> tuple[str, str, int] | None:
        """(status, detail, attempts) for an archive file, or None if never seen."""
        row = self._conn.execute("SELECT status, detail, attempts FROM file_ledger WHERE path=?", (path,)).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def ledger_set(self, path: str, sha: str, status: str, detail: str = "", attempts: int = 0) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO file_ledger (path, sha, status, detail, attempts) VALUES (?,?,?,?,?) "
                "ON CONFLICT (path) DO UPDATE SET sha=excluded.sha, status=excluded.status, "
                "detail=excluded.detail, attempts=excluded.attempts",
                (path, sha, status, detail, attempts),
            )

    def chunk_done(self, source: str, series: str, variable: str, chunk_start: date) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM backfill_chunk WHERE source=? AND series=? AND variable=? AND chunk_start=?",
            (source, series, variable, chunk_start.isoformat()),
        ).fetchone()
        return row is not None

    def chunk_mark(self, source: str, series: str, variable: str, chunk_start: date) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO backfill_chunk (source, series, variable, chunk_start, completed_utc) "
                "VALUES (?,?,?,?,?) ON CONFLICT (source, series, variable, chunk_start) "
                "DO UPDATE SET completed_utc=excluded.completed_utc",
                (source, series, variable, chunk_start.isoformat(), _enc(datetime.now(timezone.utc))),
            )

    def obs_around(
        self, station_id: str, variable: str, source: str, start_utc: datetime, end_utc: datetime, n: int
    ) -> list[Obs]:
        """Stored rows (suspect included) of one series in [start, end] plus the n rows before and after."""
        base = (
            "SELECT station_id, variable, time_utc, value, source, quality FROM obs "
            "WHERE station_id=? AND variable=? AND source=? "
        )
        a = (station_id, variable, source)
        s, e = _enc(start_utc), _enc(end_utc)
        rows = list(self._conn.execute(base + "AND time_utc>=? AND time_utc<=?", (*a, s, e)))
        rows += self._conn.execute(base + "AND time_utc<? ORDER BY time_utc DESC LIMIT ?", (*a, s, n))
        rows += self._conn.execute(base + "AND time_utc>? ORDER BY time_utc LIMIT ?", (*a, e, n))
        rows.sort(key=lambda r: r[2])
        return [Obs(r[0], r[1], _dec(r[2]), r[3], r[4], r[5]) for r in rows]

    def obs_between(self, start_utc: datetime, end_utc: datetime) -> list[Obs]:
        """Every stored row (suspect included) with start <= time < end, ordered for export."""
        rows = self._conn.execute(
            "SELECT station_id, variable, time_utc, value, source, quality FROM obs "
            "WHERE time_utc>=? AND time_utc<? ORDER BY time_utc, station_id, variable, source",
            (_enc(start_utc), _enc(end_utc)),
        )
        return [Obs(r[0], r[1], _dec(r[2]), r[3], r[4], r[5]) for r in rows]

    def forecasts_issued_between(self, start_utc: datetime, end_utc: datetime) -> list[Forecast]:
        """Every forecast value of the runs issued with start <= issued < end."""
        rows = self._conn.execute(
            "SELECT issuer, station_id, variable, issued_utc, valid_utc, qualifier, value FROM forecast "
            "WHERE issued_utc>=? AND issued_utc<? "
            "ORDER BY issued_utc, issuer, station_id, variable, valid_utc, qualifier",
            (_enc(start_utc), _enc(end_utc)),
        )
        return [Forecast(r[0], r[1], r[2], _dec(r[3]), _dec(r[4]), r[5], r[6]) for r in rows]

    def meta_get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def has_obs(self, station_id: str, variable: str, sources: Sequence[str]) -> bool:
        """True if any non-suspect observation of this series comes from one of the sources."""
        if not sources:
            return False
        marks = ",".join("?" * len(sources))
        row = self._conn.execute(
            f"SELECT 1 FROM obs WHERE station_id=? AND variable=? AND quality='ok' AND source IN ({marks}) LIMIT 1",
            (station_id, variable, *sources),
        ).fetchone()
        return row is not None
