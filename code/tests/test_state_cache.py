r"""Two requests from the same user on different dates must produce
different balance curves -- the lookahead-leak test (design.md \S4:
UserFinancialState is keyed (user_id, request_date), never user_id alone,
because a settlement dated between the two request_dates is future for
one and already-past for the other)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from engine.forecast import build_balance_curve
from ledger.canonical import build_canonical_events
from tests.helpers import make_event, make_profile


def test_same_user_different_request_dates_give_different_curves():
    profile = make_profile(current_available_balance="10000", minimum_balance_to_keep="0")
    # A one-time future debit that falls strictly between two request dates.
    events = [
        make_event(event_id="e1", direction="debit", amount_home="2000", settlement_date=date(2024, 6, 15)),
    ]

    early_request_date = date(2024, 6, 1)   # the debit is still in the future
    late_request_date = date(2024, 6, 20)   # the debit has already happened

    canon_early = build_canonical_events(events, profile, early_request_date, [])
    canon_late = build_canonical_events(events, profile, late_request_date, [])

    curve_early = build_balance_curve(canon_early, early_request_date, profile.current_available_balance)
    curve_late = build_balance_curve(canon_late, late_request_date, profile.current_available_balance)

    # The early request still sees the debit land inside its window --
    # balance dips by 2000 on 2024-06-15.
    assert curve_early.balances[date(2024, 6, 15)] == Decimal("8000")

    # The late request's window starts AFTER the debit already settled --
    # it is baked into current_available_balance, not re-applied, so the
    # late curve never dips (a lookahead leak would double-count it).
    assert all(v == Decimal("10000") for v in curve_late.balances.values())


def test_past_events_are_not_reapplied_to_the_curve():
    profile = make_profile(current_available_balance="5000", minimum_balance_to_keep="0")
    past_event = make_event(event_id="e1", direction="debit", amount_home="1000", settlement_date=date(2024, 1, 1))
    request_date = date(2024, 3, 1)

    canon = build_canonical_events([past_event], profile, request_date, [])
    assert canon == []  # single past occurrence, not recurring -- informational only

    curve = build_balance_curve(canon, request_date, profile.current_available_balance)
    assert all(v == Decimal("5000") for v in curve.balances.values())
