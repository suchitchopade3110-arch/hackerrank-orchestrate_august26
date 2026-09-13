r"""Candidate eligibility: payment_methods_user_will_consider filter,
wait requiring full_payment acceptance, and the request_12 regression
fixture (profile rejects full_payment -> installments must win even
though amount_safe_to_pay == requested_amount)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from engine.candidates import enumerate_candidates
from engine.forecast import build_balance_curve
from engine.options import expand_options_for_request
from engine.rank import rank_candidates
from io_layer import loader
from ledger.canonical import build_canonical_events
from tests.conftest import requires_dataset

pytestmark = requires_dataset


@pytest.fixture(scope="module")
def request_12_context():
    # Module-scoped: loading + FX-converting all 25k events is expensive
    # (~25-30s) -- do it once for every test in this file, not once per test.
    fx = loader.FxTable.load()
    profiles = loader.load_profiles()
    events = loader.load_events(fx=fx, profiles=profiles, exclusion_log=[])
    requests = loader.load_requests(loader.DATASET_DIR / "sample_requests.csv")
    payment_options = loader.load_payment_options()

    req = next(r for r in requests if r.request_id == "request_12")
    profile = profiles[req.user_id]
    user_events = [e for e in events if e.user_id == req.user_id]
    canon = build_canonical_events(user_events, profile, req.request_date, [])
    curve = build_balance_curve(canon, req.request_date, profile.current_available_balance)
    options = expand_options_for_request(payment_options, req.request_id, profile)
    return profile, req, canon, curve, options


def test_request_12_profile_rejects_full_payment(request_12_context):
    profile, req, canon, curve, options = request_12_context
    assert "full_payment" not in profile.payment_methods_user_will_consider


def test_request_12_selects_installments_not_full_payment(request_12_context):
    """The named regression fixture: request_12 comes out installments
    even though amount_safe_to_pay == requested_amount, because the
    profile simply does not accept full_payment at all."""
    profile, req, canon, curve, options = request_12_context
    candidates = enumerate_candidates(profile, req, canon, curve, options)
    result = rank_candidates(candidates)
    assert result.selected.method == "installments"
    assert not any(c.method == "full_payment" for c in candidates if c.feasible)


def test_no_full_payment_candidate_generated_when_not_accepted(request_12_context):
    profile, req, canon, curve, options = request_12_context
    candidates = enumerate_candidates(profile, req, canon, curve, options)
    assert all(c.method != "full_payment" for c in candidates)


def test_wait_requires_full_payment_acceptance(request_12_context):
    """A profile that only accepts installments should never produce a
    `wait` candidate, even if paying later would otherwise be safe."""
    profile, req, canon, curve, options = request_12_context
    candidates = enumerate_candidates(profile, req, canon, curve, options)
    assert all(c.method != "wait" for c in candidates)
