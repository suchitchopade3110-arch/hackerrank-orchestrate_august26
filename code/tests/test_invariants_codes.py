r"""One test per INV code (design.md \S10 / roadmap doc \S0 amendments),
using lightweight synthetic fixtures rather than the full CSV-loaded
pipeline (kept fast, exercises each invariant function directly). INV-16
and INV-17's dedicated fixture rows live in test_invariants.py; this file
fills in the rest of the 20."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datetime import timedelta

from engine.candidates import Candidate
from engine.reductions import SpendingChange
from ledger.canonical import CanonicalEvent
from tests.helpers import make_profile, make_request
from verify import invariants as inv


def _flex_event(event_id="event_9", category="dining", amount="50") -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"{event_id}_series", raw_event_id=event_id, user_id="user_1",
        kind="recurring_flexible", category=category, description="test", direction="debit",
        amount=Decimal(amount), flexible=True, flexibility="reducible",
        minimum_allowed_amount=None, anchor_date=date(2024, 1, 1),
        interval_kind="calendar_month", interval_n=1, provenance=(event_id,),
    )


def _essential_event(event_id="event_8") -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"{event_id}_series", raw_event_id=event_id, user_id="user_1",
        kind="recurring_essential", category="rent", description="rent", direction="debit",
        amount=Decimal("100"), flexible=False, flexibility="fixed",
        minimum_allowed_amount=None, anchor_date=date(2024, 1, 1),
        interval_kind="calendar_month", interval_n=1, provenance=(event_id,),
    )


def _make_ctx(row, selected, profile=None, req=None, amount_safe_baseline=None, earliest_baseline=None, canonical_events=(), option_schedules=()):
    profile = profile or make_profile()
    req = req or make_request()
    if amount_safe_baseline is None:
        amount_safe_baseline = Decimal(row["amount_safe_to_pay"])
    return inv.VerificationContext(
        row=row, profile=profile, req=req, selected=selected,
        amount_safe_baseline=amount_safe_baseline, earliest_baseline=earliest_baseline,
        canonical_events=list(canonical_events), option_schedules=list(option_schedules),
    )


def test_inv01_out_of_range_amount_fails():
    row = {"amount_safe_to_pay": "2000", "affordability_status": "affordable_now", "recommended_payment_method": "full_payment"}
    req = make_request(requested_amount="1000")
    selected = Candidate("full_payment", ((req.request_date, Decimal("1000")),), (), None, Decimal("1000"), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, req=req)
    assert inv.inv_01(ctx).passed is False


def test_inv02_invalid_status_enum_fails():
    row = {"amount_safe_to_pay": "0", "affordability_status": "bogus_status", "recommended_payment_method": "full_payment"}
    selected = Candidate("full_payment", (), (), None, Decimal(0), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected)
    assert inv.inv_02(ctx).passed is False


def test_inv03_affordable_now_requires_earliest_equals_request_date():
    req = make_request(request_date=date(2024, 1, 1))
    row = {
        "amount_safe_to_pay": "1000", "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment", "earliest_date_for_full_payment": "2024-01-05",
    }
    selected = Candidate("full_payment", ((req.request_date, Decimal("1000")),), (), None, Decimal("1000"), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, req=req)
    assert inv.inv_03(ctx).passed is False  # earliest doesn't match request_date


def test_inv04_partial_payment_split_must_sum_to_requested_amount():
    req = make_request(requested_amount="1000", desired_completion_date=date(2024, 3, 1))
    row = {
        "amount_safe_to_pay": "400", "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "partial_payment",
        "payment_plan": "2024-01-01:400|2024-02-01:500",  # 400+500 != 1000
        "earliest_date_for_full_payment": "2024-02-01",
    }
    selected = Candidate(
        "partial_payment",
        ((req.request_date, Decimal("400")), (date(2024, 2, 1), Decimal("500"))),
        (), None, Decimal("900"), Decimal(0), True, False, True,
    )
    ctx = _make_ctx(row, selected, req=req, amount_safe_baseline=Decimal("400"), earliest_baseline=date(2024, 2, 1))
    assert inv.inv_04(ctx).passed is False


def test_inv05_installments_plan_must_match_the_supplied_option():
    from engine.options import OptionSchedule

    option = OptionSchedule(
        payment_option_id="opt_1", request_id="request_1", payment_method="installments",
        payments=((date(2024, 1, 1), Decimal("500")), (date(2024, 2, 1), Decimal("500"))),
        total_payable_amount=Decimal("1000"), financing_fee=Decimal(0), eligible=True, ineligible_reason=None,
    )
    row = {
        "amount_safe_to_pay": "1000", "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "installments",
        "payment_plan": "2024-01-01:999|2024-02-01:500",  # wrong first amount
    }
    selected = Candidate(
        "installments", option.payments, (), "opt_1", Decimal("1000"), Decimal(0), True, False, True,
    )
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("1000"), option_schedules=[option])
    assert inv.inv_05(ctx).passed is False


def test_inv06_wait_requires_single_full_amount_payment():
    profile = make_profile(payment_methods_user_will_consider=frozenset({"full_payment"}))
    req = make_request(requested_amount="1000")
    row = {"amount_safe_to_pay": "0", "affordability_status": "affordable_later", "recommended_payment_method": "wait"}
    selected = Candidate("wait", ((date(2024, 3, 1), Decimal("500")),), (), None, Decimal("500"), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, profile=profile, req=req, amount_safe_baseline=Decimal("0"))
    assert inv.inv_06(ctx).passed is False  # payment amount != requested_amount


def test_inv07_not_recommended_requires_plan_none():
    row = {"amount_safe_to_pay": "0", "affordability_status": "not_affordable", "recommended_payment_method": "not_recommended", "payment_plan": "2024-01-01:100"}
    selected = Candidate("not_recommended", (), (), None, Decimal(0), Decimal(0), False, False, True)
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("0"))
    assert inv.inv_07(ctx).passed is False


def test_inv08_plan_dates_must_be_non_decreasing():
    row = {"amount_safe_to_pay": "100", "affordability_status": "affordable_with_plan", "recommended_payment_method": "partial_payment", "payment_plan": "2024-02-01:100|2024-01-01:100"}
    selected = Candidate("partial_payment", (), (), None, Decimal(0), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("100"))
    assert inv.inv_08(ctx).passed is False


def test_inv09_spending_change_must_target_a_recurring_flexible_event():
    essential = _essential_event()
    change = SpendingChange(kind="stop", event=essential, new_amount=None, cash_freed_by_binding_date=Decimal("100"), occurrences_up_to_binding=())
    row = {"amount_safe_to_pay": "0", "affordability_status": "affordable_with_plan", "recommended_payment_method": "full_payment"}
    selected = Candidate("full_payment", (), (change,), None, Decimal(0), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("0"))
    assert inv.inv_09(ctx).passed is False  # essential (not flexible) event targeted


def test_inv10_at_most_three_changes_and_no_repeats():
    flex = _flex_event()
    changes = tuple(
        SpendingChange(kind="stop", event=flex, new_amount=None, cash_freed_by_binding_date=Decimal("10"), occurrences_up_to_binding=())
        for _ in range(4)
    )
    row = {"amount_safe_to_pay": "0", "affordability_status": "affordable_with_plan", "recommended_payment_method": "full_payment"}
    selected = Candidate("full_payment", (), changes, None, Decimal(0), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("0"))
    assert inv.inv_10(ctx).passed is False  # 4 > 3


def test_inv11_reruns_a_fresh_simulation_and_catches_a_stale_feasible_flag():
    profile = make_profile(current_available_balance="100", minimum_balance_to_keep="0")
    req = make_request(requested_amount="100000")
    # Claims feasible=True but the payment obviously overdraws.
    selected = Candidate("full_payment", ((req.request_date, Decimal("100000")),), (), None, Decimal("100000"), Decimal(0), True, False, True)
    row = {"amount_safe_to_pay": "100000", "affordability_status": "affordable_now", "recommended_payment_method": "full_payment"}
    ctx = _make_ctx(row, selected, profile=profile, req=req, amount_safe_baseline=Decimal("100000"))
    assert inv.inv_11(ctx).passed is False


def test_inv12_ungrounded_numeral_in_explanation_fails():
    req = make_request(requested_amount="1000")
    row = {
        "amount_safe_to_pay": "1000", "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "decision_explanation": "Pay 9999999 today.",  # not a real fact-sheet number
    }
    selected = Candidate("full_payment", ((req.request_date, Decimal("1000")),), (), None, Decimal("1000"), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, req=req, amount_safe_baseline=Decimal("1000"))
    assert inv.inv_12(ctx).passed is False


def test_inv14_amount_formatting_must_match_the_baseline_recomputation():
    row = {"amount_safe_to_pay": "100.5000", "affordability_status": "affordable_now", "recommended_payment_method": "full_payment"}
    selected = Candidate("full_payment", (), (), None, Decimal(0), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("100.5"))
    assert inv.inv_14(ctx).passed is False  # "100.5000" != format_amount_safe_to_pay("100.5") == "100.5"


def test_inv15_affordable_later_requires_earliest_strictly_after_request_date():
    req = make_request(request_date=date(2024, 1, 1))
    row = {"amount_safe_to_pay": "0", "affordability_status": "affordable_later", "recommended_payment_method": "wait", "earliest_date_for_full_payment": "2024-01-01"}
    selected = Candidate("wait", (), (), None, Decimal(0), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, req=req, amount_safe_baseline=Decimal("0"))
    assert inv.inv_15(ctx).passed is False  # equals request_date, not strictly after


def test_inv18_every_canonical_event_carries_provenance():
    event = _essential_event()
    row = {"amount_safe_to_pay": "0", "affordability_status": "not_affordable", "recommended_payment_method": "not_recommended"}
    selected = Candidate("not_recommended", (), (), None, Decimal(0), Decimal(0), False, False, True)
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("0"), canonical_events=[event])
    assert inv.inv_18(ctx).passed is True


def test_inv19_blank_substituted_events_are_never_silently_unflagged():
    from dataclasses import replace

    event = replace(_essential_event(), amount_was_blank=True)
    row = {"amount_safe_to_pay": "0", "affordability_status": "not_affordable", "recommended_payment_method": "not_recommended"}
    selected = Candidate("not_recommended", (), (), None, Decimal(0), Decimal(0), False, False, True)
    ctx = _make_ctx(row, selected, amount_safe_baseline=Decimal("0"), canonical_events=[event])
    result = inv.inv_19(ctx)
    assert "event_8" in result.detail  # named in the detail, not silently passed


def test_inv20_installments_must_respect_max_installment_months():
    from engine.options import OptionSchedule

    profile = make_profile(max_installment_months=3)
    long_option = OptionSchedule(
        payment_option_id="opt_1", request_id="request_1", payment_method="installments",
        payments=tuple((date(2024, 1, 1) + timedelta(days=30 * k), Decimal("100")) for k in range(6)),  # ~5 months
        total_payable_amount=Decimal("600"), financing_fee=Decimal(0), eligible=True, ineligible_reason=None,
    )
    row = {"amount_safe_to_pay": "600", "affordability_status": "affordable_with_plan", "recommended_payment_method": "installments"}
    selected = Candidate("installments", long_option.payments, (), "opt_1", Decimal("600"), Decimal(0), True, False, True)
    ctx = _make_ctx(row, selected, profile=profile, amount_safe_baseline=Decimal("600"), option_schedules=[long_option])
    assert inv.inv_20(ctx).passed is False
