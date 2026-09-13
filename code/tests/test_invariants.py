r"""Fixture tests locking the two counter-intuitive invariants from
design.md \S10. Both are exercised against real requests/profiles from
dataset/, not fabricated user data -- only the specific candidate/row
combination is constructed where the real engine doesn't (yet) produce it.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from engine.candidates import Candidate
from io_layer import loader
from verify.invariants import VerificationContext, run_invariants
from tests.conftest import requires_dataset

pytestmark = requires_dataset


def _load_request(request_id: str, path=None):
    requests = loader.load_requests(path) if path else loader.load_requests()
    return next(r for r in requests if r.request_id == request_id)


def test_inv17_not_recommended_can_carry_nonzero_amount_safe_to_pay():
    """INV-17: amount_safe_to_pay is independent of the selected method.
    request_05's own ground truth is exactly this shape: not_recommended
    with amount_safe_to_pay=737 (nonzero) -- dataset/sample_requests.csv,
    not a value we invented."""
    profiles = loader.load_profiles()
    req = _load_request("request_05", REPO_ROOT / "dataset" / "sample_requests.csv")
    profile = profiles[req.user_id]

    row = {
        "request_id": "request_05",
        "amount_safe_to_pay": "737",
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "",
        "spending_changes_needed": "none",
        "decision_explanation": (
            "Do not make this payment by 2026-01-12. None of the available "
            "options keeps the ZAR 13,100 minimum protected."
        ),
    }
    not_recommended = Candidate(
        method="not_recommended", payments=(), changes=(), option_id=None,
        total_paid=Decimal(0), total_reduction=Decimal(0),
        completes_by_deadline=False, tail_breach=False, feasible=True,
    )
    ctx = VerificationContext(
        row=row, profile=profile, req=req, selected=not_recommended,
        amount_safe_baseline=Decimal("737"), earliest_baseline=None,
        canonical_events=[], option_schedules=[],
    )
    results = {r.code: r for r in run_invariants(ctx)}
    assert results["INV-17"].passed
    assert results["INV-01"].passed  # 0 <= 737 <= 15488, still holds
    assert results["INV-07"].passed  # not_recommended => plan == none
    assert results["INV-02"].passed


def test_inv16_affordable_with_plan_allows_empty_earliest():
    r"""INV-16: an empty earliest_date_for_full_payment does NOT imply
    not_affordable. An installments plan can be safe with no single-payment
    date ever safe on its own. Constructed on request_07's real profile/
    request (which does have a non-empty earliest in practice) specifically
    to isolate the rule: no sample in the 25 has this exact combination, so
    this is the one deliberately-constructed fixture in this file, per
    design.md \S10's own note that INV-16 guards a future refactor, not a
    currently-observed row."""
    profiles = loader.load_profiles()
    req = _load_request("request_07", REPO_ROOT / "dataset" / "sample_requests.csv")
    profile = profiles[req.user_id]

    row = {
        "request_id": "request_07",
        "amount_safe_to_pay": "87170.56",
        "affordability_status": "affordable_with_plan",
        "recommended_payment_method": "installments",
        "payment_plan": "2024-09-12:68432|2024-10-10:68432|2024-11-07:68432",
        "earliest_date_for_full_payment": "",  # the counter-intuitive part
        "spending_changes_needed": "none",
        "decision_explanation": "Use 3 installments of INR 68,432, starting 2024-09-12.",
    }
    installments = Candidate(
        method="installments",
        payments=(
            (req.request_date.replace(day=12), Decimal("68432")),
        ),
        changes=(), option_id="payment_option_19",
        total_paid=Decimal("205296"), total_reduction=Decimal(0),
        completes_by_deadline=True, tail_breach=False, feasible=True,
    )
    ctx = VerificationContext(
        row=row, profile=profile, req=req, selected=installments,
        amount_safe_baseline=Decimal("87170.56"), earliest_baseline=None,
        canonical_events=[], option_schedules=[],
    )
    results = {r.code: r for r in run_invariants(ctx)}
    assert results["INV-16"].passed
    assert results["INV-03"].passed  # n/a, status isn't affordable_now
    assert results["INV-15"].passed  # n/a, status isn't affordable_later
    assert results["INV-02"].passed
