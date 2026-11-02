from datetime import date, datetime, timezone

from climauy import fmt


def test_pct_floor_and_tenths_near_top():
    assert fmt.pct(94.7) == "94"
    assert fmt.pct(98.99) == "98"
    assert fmt.pct(99.0) == "99,0"
    assert fmt.pct(99.56) == "99,5"
    assert fmt.pct(99.999) == "99,9"
    assert fmt.pct(100.0) == "100"


def test_numbers_use_decimal_comma():
    assert fmt.num(12197.0) == "12.197"
    assert fmt.num(7.66) == "7,66"
    assert fmt.num(1.5, 1) == "1,5"
    assert fmt.meters(7.5) == "7,5 m"
    assert fmt.meters(11.0) == "11 m"
    assert fmt.signed(0.254) == "+0,25"
    assert fmt.signed(-0.001) == "+0,00"


def test_dates_in_spanish():
    assert fmt.day_long(date(2026, 11, 1)) == "1 de noviembre de 2026"
    assert fmt.day_numeric(date(2026, 11, 1)) == "1/11/2026"
    assert fmt.day_short(date(2026, 11, 1)) == "1/11"


def test_hour_in_montevideo():
    # 21:05Z is 18:05 in Montevideo (UTC-3).
    assert fmt.hour_local(datetime(2026, 11, 1, 21, 5, tzinfo=timezone.utc)) == "18:05"


def test_enso_periods():
    assert fmt.season_key("2026-JAS") < fmt.season_key("2026-ASO") < fmt.season_key("2027-DJF")
    assert fmt.season_label("2026-JAS") == "julio-septiembre de 2026"
    assert fmt.season_label("2026-NDJ") == "noviembre de 2026 a enero de 2027"
    assert fmt.season_label("2026-DJF") == "diciembre de 2025 a febrero de 2026"
    assert fmt.is_season("2026-OND") and not fmt.is_season("2026-XYZ") and not fmt.is_season("bad")
