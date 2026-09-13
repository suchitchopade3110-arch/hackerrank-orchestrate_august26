r"""ledger/precedence.py: base exclusions and linked_event_id chain
resolution (design.md \S6, AGENTS.md \S6.3)."""

from __future__ import annotations

from datetime import date

from ledger.precedence import apply_base_exclusions, resolve_linked_chains
from tests.helpers import make_event


def test_pending_debit_survives_and_is_reserved():
    """The spec excludes pending CREDITS only -- a generic 'drop anything
    pending' filter would inflate every balance in the dataset."""
    events = [make_event(event_id="e1", direction="debit", status="pending")]
    log: list[dict] = []
    survivors = apply_base_exclusions(events, log)
    assert len(survivors) == 1
    assert survivors[0].event_id == "e1"
    assert log == []


def test_pending_credit_is_dropped():
    events = [make_event(event_id="e1", direction="credit", status="pending")]
    log: list[dict] = []
    survivors = apply_base_exclusions(events, log)
    assert survivors == []
    assert log[0]["reason"] == "pending_credit"


def test_failed_and_cancelled_are_dropped():
    events = [
        make_event(event_id="e1", status="failed"),
        make_event(event_id="e2", status="cancelled"),
    ]
    log: list[dict] = []
    survivors = apply_base_exclusions(events, log)
    assert survivors == []
    reasons = {e["reason"] for e in log}
    assert reasons == {"failed", "cancelled"}


def test_unrealized_and_non_cash_are_dropped():
    events = [
        make_event(event_id="e1", status="unrealized"),
        make_event(event_id="e2", direction="non_cash"),
    ]
    log: list[dict] = []
    survivors = apply_base_exclusions(events, log)
    assert survivors == []
    reasons = {e["reason"] for e in log}
    assert reasons == {"unrealized_investment", "non_cash"}


def test_settled_debit_and_credit_both_survive():
    events = [
        make_event(event_id="e1", direction="debit", status="settled"),
        make_event(event_id="e2", direction="credit", status="settled"),
    ]
    log: list[dict] = []
    survivors = apply_base_exclusions(events, log)
    assert {e.event_id for e in survivors} == {"e1", "e2"}


def test_refund_nets_against_its_linked_debit():
    debit = make_event(event_id="e1", direction="debit", amount_home="500", category="shopping")
    refund = make_event(
        event_id="e2", event_type="refund", direction="credit",
        amount_home="150", linked_event_id="e1", category="shopping",
    )
    log: list[dict] = []
    out = resolve_linked_chains([debit, refund], log)
    assert len(out) == 1
    assert out[0].event_id == "e1"
    assert out[0].amount_home == 350  # 500 - 150
    assert log[0]["reason"] == "netted_refund_into_linked_debit"


def test_refund_larger_than_debit_floors_at_zero_not_negative():
    debit = make_event(event_id="e1", direction="debit", amount_home="100")
    refund = make_event(
        event_id="e2", event_type="refund", direction="credit",
        amount_home="150", linked_event_id="e1",
    )
    out = resolve_linked_chains([debit, refund], [])
    assert out[0].amount_home == 0


def test_linked_chain_collapses_to_settled_over_pending():
    pending = make_event(
        event_id="e1", status="pending", settlement_date=date(2024, 1, 1),
    )
    settled = make_event(
        event_id="e2", status="settled", linked_event_id="e1",
        settlement_date=date(2024, 1, 5),
    )
    log: list[dict] = []
    out = resolve_linked_chains([pending, settled], log)
    assert len(out) == 1
    assert out[0].event_id == "e2"
    assert any(e["reason"] == "superseded_by_linked_event_id" for e in log)


def test_duplicate_style_chain_keeps_the_later_settled_member():
    # Two settled updates of the same underlying fact (e.g. an amended
    # amount) -- the later one (by settlement_date) wins.
    first = make_event(event_id="e1", status="settled", settlement_date=date(2024, 1, 1), amount_home="100")
    second = make_event(
        event_id="e2", status="settled", linked_event_id="e1",
        settlement_date=date(2024, 1, 10), amount_home="120",
    )
    out = resolve_linked_chains([first, second], [])
    assert len(out) == 1
    assert out[0].event_id == "e2"
    assert out[0].amount_home == 120
