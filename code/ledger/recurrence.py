r"""Cadence detection and occurrences() -- the only place the calendar is
walked (design.md \S5). A series is anchored on its last observed occurrence
and stepped forward by an explicit interval_kind, so phase is never inferred
at forecast time (design.md \S3 decision 2).

Cadence 28-31 days is treated as calendar-month stepping (decision 3), which
preserves day-of-month instead of drifting after a few cycles.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median
from typing import Literal

from dateutil.relativedelta import relativedelta

IntervalKind = Literal["once", "days", "calendar_month"]

MONTH_LIKE_DAYS = range(28, 32)  # 28..31 inclusive
# Tolerance for treating gaps as "the same cadence" despite calendar noise
# (e.g. a 30-day month vs a 31-day month for a monthly bill).
DAY_GAP_TOLERANCE = 3


MIN_OCCURRENCES_FOR_CADENCE = 3


def detect_cadence(dates: list[date]) -> tuple[IntervalKind, int] | None:
    """Given a settled series' occurrence dates (any order), return
    (interval_kind, interval_n) if history supports a STRICT periodic
    cadence, else None.

    Requires at least MIN_OCCURRENCES_FOR_CADENCE observed occurrences with
    a consistent gap -- two points can't distinguish a genuine cadence from
    coincidence, so a 2-occurrence group is never treated as periodic (it
    may still qualify for the average-rate fallback below).
    """
    if len(dates) < MIN_OCCURRENCES_FOR_CADENCE:
        return None

    ordered = sorted(dates)
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:])]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None

    med = median(gaps)
    if all(abs(g - med) <= DAY_GAP_TOLERANCE for g in gaps):
        if 28 <= med <= 31:
            return "calendar_month", 1
        return "days", max(1, round(med))

    # A single irregular gap (a late or early payment one cycle, e.g. a
    # delayed payroll run) shouldn't kill an otherwise-clean monthly series:
    # if at least 70% of the gaps still land in the month-like band, call it
    # calendar_month anyway rather than dropping real recurring history over
    # one outlier.
    month_like = [g for g in gaps if 27 <= g <= 33]
    if len(month_like) >= max(2, math.ceil(0.7 * len(gaps))):
        return "calendar_month", 1

    return None


def average_daily_rate(dates: list[date], amounts: list, as_of: date) -> tuple:
    """Fallback for a series with enough history (>= MIN_OCCURRENCES_FOR_CADENCE)
    but no consistent calendar cadence -- real day-to-day essential spend
    (groceries, transport, dining) that recurs frequently but not on a fixed
    date. Forecast conservatively as a smoothed daily drip: total historical
    spend over the observed span, divided into a per-day amount, stepped
    forward from as_of at interval_kind='days', interval_n=1.

    Returns (per_day_amount, span_days) so the caller can build a
    CanonicalEvent with anchor_date=as_of, interval_kind='days', interval_n=1.
    """
    ordered = sorted(dates)
    span_days = max(1, (ordered[-1] - ordered[0]).days)
    total = sum(amounts, type(amounts[0])(0)) if amounts else 0
    per_day = total / span_days
    return per_day, span_days


def occurrences(
    anchor_date: date,
    interval_kind: IntervalKind,
    interval_n: int | None,
    start: date,
    end: date,
) -> list[date]:
    """Occurrence dates strictly after anchor_date, stepped forward from it,
    inclusive of [start, end]. anchor_date itself is never returned (it is
    the *last observed* occurrence, already in the past relative to the
    forecast window in every call site)."""
    if interval_kind == "once":
        return [anchor_date] if start <= anchor_date <= end else []

    if interval_kind == "calendar_month":
        step = interval_n or 1
        out = []
        cursor = anchor_date
        for _ in range(500):  # hard cap; 90-day horizon needs far fewer steps
            cursor = cursor + relativedelta(months=step)
            if cursor > end:
                break
            if cursor >= start:
                out.append(cursor)
        return out

    if interval_kind == "days":
        step = interval_n or 1
        out = []
        cursor = anchor_date
        for _ in range(10000):
            cursor = cursor + timedelta(days=step)
            if cursor > end:
                break
            if cursor >= start:
                out.append(cursor)
        return out

    raise ValueError(f"unknown interval_kind: {interval_kind}")
