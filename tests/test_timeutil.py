from datetime import date, datetime, timezone

from climauy.timeutil import local_day, parse_brasilia


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_local_day_boundary():
    assert local_day(utc(2026, 11, 1, 2, 59)) == date(2026, 10, 31)
    assert local_day(utc(2026, 11, 1, 3, 0)) == date(2026, 11, 1)


def test_parse_brasilia():
    t = parse_brasilia("2026-11-01 23:30:00")
    assert t == utc(2026, 11, 2, 2, 30)
    assert t.utcoffset().total_seconds() == 0
    assert local_day(t) == date(2026, 11, 1)
