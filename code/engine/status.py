r"""affordability_status decision table (design.md \S9), honouring the
couplings the spec states explicitly:
  affordable_now       <=> earliest_date_for_full_payment == request_date
  partial_payment      => affordable_with_plan
  not_recommended      => payment_plan == none
"""

from __future__ import annotations

from engine.candidates import Candidate


def decide_status(candidate: Candidate, request_date) -> str:
    if candidate.method == "not_recommended":
        return "not_affordable"

    if candidate.method == "wait":
        return "affordable_later"

    if candidate.method == "full_payment":
        first_date = candidate.payments[0][0] if candidate.payments else None
        if not candidate.changes and first_date == request_date:
            return "affordable_now"
        return "affordable_with_plan"

    if candidate.method in ("installments", "partial_payment"):
        return "affordable_with_plan"

    raise ValueError(f"unhandled method: {candidate.method}")
