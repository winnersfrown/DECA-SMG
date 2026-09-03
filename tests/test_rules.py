"""The competition calendar and constants must be exactly right."""

import datetime as dt

from decasmg import rules


def test_competition_window_matches_guidelines():
    assert rules.COMPETITION_START == dt.datetime(2026, 9, 8, 9, 30)
    assert rules.COMPETITION_END == dt.datetime(2026, 12, 4, 16, 0)
    assert rules.DIVERSIFICATION_DEADLINE == dt.datetime(2026, 10, 23, 16, 0)
    assert rules.STUDENT_NAME_DEADLINE == dt.datetime(2026, 10, 16, 16, 0)
    # Dates in the guidelines are given with weekday names; verify they agree.
    assert rules.COMPETITION_START.strftime("%A") == "Tuesday"
    assert rules.COMPETITION_END.strftime("%A") == "Friday"
    assert rules.DIVERSIFICATION_DEADLINE.strftime("%A") == "Friday"
    assert rules.STUDENT_NAME_DEADLINE.strftime("%A") == "Friday"


def test_position_cap_is_twenty_percent_times_one_point_five():
    assert rules.MAX_POSITION_PCT == 0.30
    assert rules.STARTING_CASH == 100_000.00
    assert rules.MARGIN_BORROW_RATE == 0.50
    assert rules.TRANSACTION_FEE == 5.00
    assert rules.MIN_NET_COST_PER_ASSET_CLASS == 10_000.00


def test_thanksgiving_is_the_only_closure_in_window():
    days = rules.competition_trading_days()
    assert days[0] == dt.date(2026, 9, 8)
    assert days[-1] == dt.date(2026, 12, 4)
    assert dt.date(2026, 11, 26) not in days       # Thanksgiving
    assert dt.date(2026, 11, 27) in days           # early close, still a trading day
    assert len(days) == 63


def test_orders_price_at_the_correct_close():
    # During market hours -> that day's close.
    assert rules.pricing_day(dt.datetime(2026, 11, 20, 15, 0)) == dt.date(2026, 11, 20)
    # After the close on a Friday -> the following Monday.
    assert rules.pricing_day(dt.datetime(2026, 11, 20, 17, 0)) == dt.date(2026, 11, 23)
    # After the close the day before Thanksgiving -> skips the holiday.
    assert rules.pricing_day(dt.datetime(2026, 11, 25, 17, 0)) == dt.date(2026, 11, 27)
    # On a weekend -> the next trading day.
    assert rules.pricing_day(dt.datetime(2026, 11, 21, 11, 0)) == dt.date(2026, 11, 23)


def test_trading_days_remaining_excludes_a_finished_session():
    # Before the close on the final day, that day still counts.
    assert rules.trading_days_remaining(dt.datetime(2026, 12, 4, 10, 0)) == 1
    # After the competition ends, nothing remains.
    assert rules.trading_days_remaining(dt.datetime(2026, 12, 4, 16, 0)) == 0
    assert rules.trading_days_remaining(dt.datetime(2026, 12, 7, 10, 0)) == 0


def test_one_business_day_restore_skips_weekends_and_holidays():
    # Friday + 1 business day -> Monday.
    assert rules.add_business_days(dt.date(2026, 11, 20), 1) == dt.date(2026, 11, 23)
    # Wednesday before Thanksgiving + 1 -> Friday, skipping the holiday.
    assert rules.add_business_days(dt.date(2026, 11, 25), 1) == dt.date(2026, 11, 27)
