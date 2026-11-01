"""Time helpers: everything is stored in UTC, local days use America/Montevideo."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MONTEVIDEO = ZoneInfo("America/Montevideo")
BRASILIA = timezone(timedelta(hours=-3))  # fixed UTC-3, no DST


def local_day(t_utc: datetime) -> date:
    """Calendar day in America/Montevideo for a timezone-aware datetime."""
    if t_utc.tzinfo is None:
        raise ValueError("local_day needs a timezone-aware datetime")
    return t_utc.astimezone(MONTEVIDEO).date()


def parse_brasilia(s: str) -> datetime:
    """Parse 'YYYY-MM-DD HH:MM:SS' given in Brasilia time (UTC-3) into aware UTC."""
    naive = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=BRASILIA).astimezone(timezone.utc)
