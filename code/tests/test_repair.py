"""Forced-failure test (PRD.md Phase 3 gate): inject a deliberately unsafe
selected plan as the top-ranked candidate and confirm the demotion path
fires, logs a demotion, and still emits a valid (never blank) row.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from engine.candidates import Candidate
from io_layer import loader
from ledger.canonical import build_canonical_events
from engine.forecast import build_balance_curve
from main import select_with_repair
from tests.conftest import requires_dataset

pytestmark = requires_dataset


def test_demotes_a_candidate_whose_claimed_feasibility_is_a_lie():
    profiles = loader.load_profiles()
    requests = loader.load_requests(REPO_ROOT / "dataset" / "sample_requests.csv")
    req = next(r for r in requests if r.request_id == "request_01")
    profile = profiles[req.user_id]

    fx = loader.FxTable.load()
    events = loader.load_events(fx=fx, profiles=profiles, exclusion_log=[])
    user_events = [e for e in events if e.user_id == req.user_id]
    canonical = build_canonical_events(user_events, profile, req.request_date, [])
    curve = build_balance_curve(canonical, req.request_date, profile.current_available_balance)

    # A candidate that CLAIMS feasible=True but whose payment would
    # obviously overdraw the account well past the minimum balance --
    # exactly the kind of stale/incorrect cached feasibility result INV-11
    # exists to catch via independent re-simulation.
    lying_candidate = Candidate(
        method="full_payment",
        payments=((req.request_date, profile.current_available_balance * 100),),
        changes=(), option_id=None,
        total_paid=profile.current_available_balance * 100,
        total_reduction=Decimal(0),
        completes_by_deadline=True, tail_breach=False,
        feasible=True,  # the lie
    )
    not_recommended = Candidate(
        method="not_recommended", payments=(), changes=(), option_id=None,
        total_paid=Decimal(0), total_reduction=Decimal(0),
        completes_by_deadline=False, tail_breach=False, feasible=True,
    )

    candidates = [lying_candidate, not_recommended]

    chosen, row, results, demotions, decided_by = select_with_repair(
        profile, req, candidates,
        amt_safe_baseline=Decimal("100"), earliest_baseline=req.request_date,
        canonical_events=canonical, option_schedules=[],
    )

    # The lie was caught: exactly one demotion, and it was INV-11 that
    # caught it (independent re-simulation, not the cached .feasible flag).
    assert len(demotions) == 1
    assert demotions[0]["method"] == "full_payment"
    assert "INV-11" in demotions[0]["failed_invariants"]

    # Fell back to the safe candidate -- never a blank or invalid row.
    assert chosen.method == "not_recommended"
    assert row["recommended_payment_method"] == "not_recommended"
    assert row["payment_plan"] == "none"
    assert row["amount_safe_to_pay"] != ""
    assert all(v != "" or k == "earliest_date_for_full_payment" for k, v in row.items())
    assert decided_by == "safe_fallback_after_repairs"
