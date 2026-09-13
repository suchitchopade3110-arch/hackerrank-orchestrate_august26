r"""engine/rank.py: the lexicographic key from design.md \S8 -- a fee-
bearing installment plan never beats a safe full payment when both are
eligible (criterion 3, lowest total paid)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from engine.candidates import Candidate
from engine.rank import rank_candidates


def _candidate(method, total_paid, payments, changes=(), option_id=None, completes=True) -> Candidate:
    return Candidate(
        method=method,
        payments=payments,
        changes=changes,
        option_id=option_id,
        total_paid=Decimal(total_paid),
        total_reduction=Decimal(0),
        completes_by_deadline=completes,
        tail_breach=False,
        feasible=True,
    )


def test_fee_bearing_installments_never_beats_a_safe_full_payment():
    full = _candidate(
        "full_payment", "1000", ((date(2024, 1, 1), Decimal("1000")),),
    )
    installments = _candidate(
        "installments", "1100",  # includes a financing fee
        ((date(2024, 1, 1), Decimal("550")), (date(2024, 2, 1), Decimal("550"))),
        option_id="payment_option_1",
    )
    result = rank_candidates([full, installments])
    assert result.selected.method == "full_payment"
    assert result.decided_by == "lowest_total_paid"


def test_deadline_completion_beats_everything_else():
    misses_deadline = _candidate(
        "full_payment", "500", ((date(2024, 3, 1), Decimal("500")),), completes=False,
    )
    meets_deadline_more_expensive = _candidate(
        "installments", "600",
        ((date(2024, 1, 1), Decimal("300")), (date(2024, 2, 1), Decimal("300"))),
        option_id="payment_option_1", completes=True,
    )
    result = rank_candidates([misses_deadline, meets_deadline_more_expensive])
    assert result.selected.method == "installments"
    assert result.decided_by == "completes_by_deadline"


def test_fewer_changes_beats_lower_total_paid():
    no_changes = _candidate("full_payment", "1000", ((date(2024, 1, 1), Decimal("1000")),))
    with_change = _candidate(
        "full_payment", "900", ((date(2024, 1, 1), Decimal("900")),),
    )
    # simulate a change by giving with_change a non-empty changes tuple via object surgery
    import dataclasses

    with_change = dataclasses.replace(with_change, changes=("fake_change",))
    result = rank_candidates([no_changes, with_change])
    assert result.selected is no_changes
    assert result.decided_by == "fewest_changes"


def test_lowest_option_id_is_the_final_documented_tiebreak():
    a = _candidate(
        "installments", "1000",
        ((date(2024, 1, 1), Decimal("500")), (date(2024, 2, 1), Decimal("500"))),
        option_id="payment_option_2",
    )
    b = _candidate(
        "installments", "1000",
        ((date(2024, 1, 1), Decimal("500")), (date(2024, 2, 1), Decimal("500"))),
        option_id="payment_option_1",
    )
    result = rank_candidates([a, b])
    assert result.selected.option_id == "payment_option_1"
    assert result.decided_by == "lowest_option_id"


def test_no_feasible_candidate_falls_back_to_not_recommended():
    infeasible = Candidate(
        method="full_payment", payments=((date(2024, 1, 1), Decimal("1000")),),
        changes=(), option_id=None, total_paid=Decimal("1000"), total_reduction=Decimal(0),
        completes_by_deadline=True, tail_breach=False, feasible=False,
    )
    fallback = _candidate("not_recommended", "0", ())
    result = rank_candidates([infeasible, fallback])
    assert result.selected.method == "not_recommended"
    assert result.decided_by == "no_feasible_candidate"
