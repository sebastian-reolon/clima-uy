"""Parser for the CTM Salto Grande daily hydrology report (one-page PDF)."""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from pypdf import PdfReader

from climauy.catalog import load_catalog
from climauy.parsers import to_float
from climauy.store import DamDay, Obs

log = logging.getLogger(__name__)

# The report values are read at 07:00 Argentina/Brasília time (UTC-3).
_OBS_HOUR_UTC = 10

# CTM spelling -> catalog spelling, where they differ.
_ALIASES = {"salto grande": "salto grande arriba"}
# Rows that are not stations: inflow total and the dam release gauges of the table.
_IGNORED_ROWS = {"aporte"}
# Reservoir levels with no catalog entry (Federación): skipped, with a log line.

_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def _station_ids() -> dict[str, str]:
    """Normalized catalog name -> station id, for the Argentine INA stations."""
    return {_norm(s.name): s.id for s in load_catalog() if s.id.startswith("ina_")}


def _blocks(text: str) -> dict[str, list[str]]:
    """Split the page text into {title: lines} by the table title lines."""
    titles = {
        "vol": "Volumenes Hora 07:00",
        "daily": "Caudales Medios Diarios",
        "q": "Caudales Hora 07:00",
        "release": "Salto Grande - Erogado",
        "level": "Niveles Hora 07:00",
    }
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        line = line.strip()
        key = next((k for k, t in titles.items() if line.startswith(t)), None)
        if key:
            current = out.setdefault(key, [])
        elif line.startswith("Powered by") or line.startswith("Pagina"):
            current = None
        elif current is not None:
            current.append(line)
    return out


def _dates(lines: list[str]) -> list[date]:
    for line in lines:
        found = _DATE.findall(line)
        if found:
            return [datetime.strptime(d, "%d/%m/%Y").date() for d in found]
    return []


def _rows(lines: list[str], n: int) -> list[tuple[str, list[float | None]]]:
    """Rows 'Name v1 .. vn' with exactly n numeric values."""
    rows = []
    for line in lines:
        m = re.fullmatch(r"(.+?)\s+((?:-?\d+(?:[.,]\d+)?\s*){%d})" % n, line)
        if m:
            rows.append((m.group(1).strip(), [to_float(v) for v in m.group(2).split()]))
    return rows


def parse_text(text: str) -> tuple[list[DamDay], list[Obs]]:
    blocks = _blocks(text)
    dams: list[DamDay] = []
    obs: list[Obs] = []

    # Dam releases: the "7 a 7" row holds QTurb QVert QTotal per date.
    rel = blocks.get("release", [])
    rel_dates = _dates(rel)
    vols = {name: vals for name, vals in _rows(blocks.get("vol", []), len(rel_dates))}
    for line in rel:
        m = re.fullmatch(r"7 a 7((?:\s+-?\d+(?:[.,]\d+)?){%d})" % (3 * len(rel_dates)), line)
        if not m:
            continue
        nums = [to_float(v) for v in m.group(1).split()]
        for i, d in enumerate(rel_dates):
            q_turb, q_vert, q_total = nums[3 * i: 3 * i + 3]
            if None in (q_turb, q_vert, q_total):
                continue
            dams.append(DamDay(
                date_local=d, q_turb=q_turb, q_vert=q_vert, q_total=q_total,
                vol_machadinho_pct=vols.get("Machadinho", [None] * len(rel_dates))[i],
                vol_ita_pct=vols.get("Itá", [None] * len(rel_dates))[i],
            ))

    ids = _station_ids()
    for key, variable in (("q", "discharge"), ("level", "level")):
        lines = blocks.get(key, [])
        dates = _dates(lines)
        for name, vals in _rows(lines, len(dates)):
            n = _norm(name)
            if n in _IGNORED_ROWS:
                continue
            sid = ids.get(_ALIASES.get(n, n))
            if sid is None:
                log.warning("CTM station %r has no catalog id, skipped", name)
                continue
            for d, v in zip(dates, vals):
                if v is None:
                    continue
                t = datetime(d.year, d.month, d.day, _OBS_HOUR_UTC, tzinfo=timezone.utc)
                obs.append(Obs(sid, variable, t, v, "ctm"))
    return dams, obs


def parse_report(pdf: bytes) -> tuple[list[DamDay], list[Obs]]:
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    return parse_text(text)
