r"""engine/reductions.py: closed-form reduce_to clamped at
minimum_allowed_amount; infeasible rather than degenerate to `stop` when
the clamp blocks the deficit (roadmap doc \S0 finding 4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from engine.reductions import propose_reduce_to, propose_stop
from ledger.canonical import CanonicalEvent


def _flexible_event(amount: str, minimum_allowed: str | None, anchor: date = date(2024, 1, 1)) -> CanonicalEvent:
    return CanonicalEvent(
        event_id="event_1_series",
        raw_event_id="event_1",
        user_id="user_1",
        kind="recurring_flexible",
        category="dining",
        description="Weekend dining",
        direction="debit",
        amount=Decimal(amount),
        flexible=True,
        flexibility="reducible",
        minimum_allowed_amount=Decimal(minimum_allowed) if minimum_allowed else None,
        anchor_date=anchor,
        interval_kind="calendar_month",
        interval_n=1,
        provenance=("event_1",),
    )


def test_reduce_to_covers_the_deficit_exactly_when_unclamped():
    event = _flexible_event("1000", minimum_allowed=None)
    request_date = date(2024, 1, 15)
    binding_date = date(2024, 3, 15)  # 2 occurrences inside the window: Feb 1, Mar 1
    deficit = Decimal("200")

    change = propose_reduce_to(event, request_date, binding_date, deficit)
    assert change is not None
    # k=2 occurrences, required_cut = ceil(200/2, 0.01) = 100 -> new amount 900
    assert change.new_amount == Decimal("900")
    assert change.cash_freed_by_binding_date == Decimal("200")


def test_reduce_to_clamps_at_minimum_allowed_amount():
    event = _flexible_event("1000", minimum_allowed="950")
    request_date = date(2024, 1, 15)
    binding_date = date(2024, 2, 15)  # k=1 occurrence: Feb 1
    deficit = Decimal("500")  # would need new_amount=500, but clamp floors at 950

    change = propose_reduce_to(event, request_date, binding_date, deficit)
    # clamped: new_amount = max(950, 1000-500) = 950, freed = 1000-950 = 50 < 500 deficit
    assert change is None  # infeasible, NOT degraded to stop


def test_reduce_to_feasible_right_at_the_clamp_boundary():
    event = _flexible_event("1000", minimum_allowed="900")
    request_date = date(2024, 1, 15)
    binding_date = date(2024, 2, 15)  # k=1
    deficit = Decimal("100")  # exactly reachable: 1000-100=900, clamp allows down to 900

    change = propose_reduce_to(event, request_date, binding_date, deficit)
    assert change is not None
    assert change.new_amount == Decimal("900")
    assert change.cash_freed_by_binding_date == Decimal("100")


def test_reduce_to_returns_none_when_no_occurrences_before_binding_date():
    event = _flexible_event("1000", minimum_allowed=None, anchor=date(2024, 6, 1))
    request_date = date(2024, 1, 15)
    binding_date = date(2024, 1, 20)  # no occurrence of this monthly event yet
    change = propose_reduce_to(event, request_date, binding_date, Decimal("50"))
    assert change is None


def test_propose_stop_frees_full_amount_times_occurrence_count():
    event = _flexible_event("300", minimum_allowed=None)
    request_date = date(2024, 1, 15)
    binding_date = date(2024, 4, 15)  # 3 occurrences: Feb 1, Mar 1, Apr 1
    change = propose_stop(event, request_date, binding_date)
    assert change is not None
    assert change.kind == "stop"
    assert change.new_amount is None
    assert change.cash_freed_by_binding_date == Decimal("900")
