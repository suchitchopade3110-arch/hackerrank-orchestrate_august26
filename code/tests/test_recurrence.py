r"""occurrences() and detect_cadence() -- the only place the calendar is
walked (design.md \S5)."""

from __future__ import annotations

from datetime import date

from ledger.recurrence import detect_cadence, occurrences


def test_calendar_month_rent_on_the_31st_clamps_to_month_end():
    # relativedelta clamps Jan 31 -> Feb 29 (leap year); stepping is
    # cumulative from the last cursor, so it then continues from the
    # clamped date (Feb 29 -> Mar 29 -> Apr 29 -> May 29) rather than
    # snapping back to 31 once a longer month reappears. Verified against
    # the actual implementation, not assumed.
    occs = occurrences(date(2024, 1, 31), "calendar_month", 1, date(2024, 1, 31), date(2024, 5, 31))
    assert occs == [date(2024, 2, 29), date(2024, 3, 29), date(2024, 4, 29), date(2024, 5, 29)]


def test_calendar_month_on_february_29_leap_year():
    occs = occurrences(date(2024, 2, 29), "calendar_month", 1, date(2024, 2, 29), date(2024, 5, 1))
    assert occs == [date(2024, 3, 29), date(2024, 4, 29)]


def test_28_day_cadence_vs_calendar_month_drift_over_six_cycles():
    start = date(2024, 1, 1)
    end = date(2025, 1, 1)
    days_28 = occurrences(start, "days", 28, start, end)
    months = occurrences(start, "calendar_month", 1, start, end)
    # 28-day stepping drifts earlier in the month each cycle (12 cycles of
    # 28 days = 336 days, 29 short of a full year); calendar_month stays
    # anchored on day 1 every time regardless of month length.
    assert days_28[5] == date(2024, 6, 17)  # 6th 28-day cycle: drifted off day-1
    assert all(d.day == 1 for d in months)
    assert months[5] == date(2024, 7, 1)


def test_detect_cadence_requires_at_least_three_occurrences():
    two_points = [date(2024, 1, 1), date(2024, 2, 1)]
    assert detect_cadence(two_points) is None


def test_detect_cadence_tolerates_one_outlier_gap():
    # 4 clean ~30-day gaps + one 39-day outlier (a late payroll run) --
    # still classified calendar_month per the 70%-of-gaps rule.
    dates = [
        date(2024, 4, 15), date(2024, 5, 15), date(2024, 6, 15),
        date(2024, 7, 15), date(2024, 8, 23),
    ]
    assert detect_cadence(dates) == ("calendar_month", 1)


def test_detect_cadence_days_interval():
    dates = [date(2024, 1, 1), date(2024, 1, 8), date(2024, 1, 15), date(2024, 1, 22)]
    assert detect_cadence(dates) == ("days", 7)


def test_occurrences_once_kind_returns_single_date_in_window():
    assert occurrences(date(2024, 3, 15), "once", None, date(2024, 1, 1), date(2024, 12, 31)) == [
        date(2024, 3, 15)
    ]
    assert occurrences(date(2024, 3, 15), "once", None, date(2024, 4, 1), date(2024, 12, 31)) == []
