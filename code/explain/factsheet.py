r"""Per-request fact sheet (design.md \S12): the only numbers the
explanation generator is allowed to talk about. Balance today, minimum
balance, the binding constraint date and its headroom, next income, the
chosen plan, the changes, and the two closed-form outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from engine.candidates import Candidate
from engine.forecast import BalanceCurve
from engine.reductions import binding_date_and_deficit
from io_layer.loader import Profile, RequestRow


def driving_event_description(canonical_events, request_date, binding_date) -> str | None:
    """The specific recurring event whose occurrence lands closest to the
    binding constraint date -- the concrete fact behind a wait/not_
    recommended explanation (roadmap-review Tier-3: explanations cited only
    balance/minimum/headroom, never the *specific* fact, e.g. "rent of X due
    on Y", even though the audit trace and factsheet already carry the
    binding_date). Returns e.g. "the Monthly rent payment (due 2024-06-04)"
    or None if no recurring event occurs on or before the binding date
    within the window (a one-off deficit with no clear cause). Lives here
    (not main.py) so both the deterministic template (via main.py) and the
    LLM factsheet (via build_factsheet below) share one implementation."""
    if canonical_events is None or binding_date is None:
        return None

    from ledger.recurrence import occurrences as _occurrences

    best = None  # (date, description, category)
    for ce in canonical_events:
        occ = _occurrences(ce.anchor_date, ce.interval_kind, ce.interval_n, request_date, binding_date)
        if not occ:
            continue
        last = occ[-1]
        if best is None or last > best[0]:
            best = (last, ce.description, ce.category)
    if best is None:
        return None
    occurrence_date, description, _category = best
    return f"the {description} payment (due {occurrence_date.isoformat()})"


@dataclass(frozen=True)
class FactSheet:
    request_id: str
    currency: str
    current_balance: Decimal
    minimum_balance_to_keep: Decimal
    binding_date: date
    binding_headroom: Decimal
    requested_amount: Decimal
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None
    method: str
    status: str
    payments: tuple
    changes: tuple
    driving_event: str | None = None


def build_factsheet(
    req: RequestRow,
    profile: Profile,
    curve: BalanceCurve,
    amount_safe_to_pay: Decimal,
    earliest_date_for_full_payment: date | None,
    selected: Candidate,
    status: str,
    canonical_events=None,
) -> FactSheet:
    binding_date, deficit = binding_date_and_deficit(
        curve, profile.minimum_balance_to_keep, req.requested_amount
    )
    headroom = curve.balances[binding_date] - profile.minimum_balance_to_keep
    return FactSheet(
        request_id=req.request_id,
        currency=profile.home_currency,
        current_balance=profile.current_available_balance,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        binding_date=binding_date,
        binding_headroom=headroom,
        requested_amount=req.requested_amount,
        amount_safe_to_pay=amount_safe_to_pay,
        earliest_date_for_full_payment=earliest_date_for_full_payment,
        method=selected.method,
        status=status,
        payments=selected.payments,
        changes=selected.changes,
        driving_event=driving_event_description(canonical_events, req.request_date, binding_date),
    )


def factsheet_to_dict(fs: FactSheet) -> dict:
    return {
        "request_id": fs.request_id,
        "currency": fs.currency,
        "current_balance": str(fs.current_balance),
        "minimum_balance_to_keep": str(fs.minimum_balance_to_keep),
        "binding_date": fs.binding_date.isoformat(),
        "binding_headroom": str(fs.binding_headroom),
        "requested_amount": str(fs.requested_amount),
        "amount_safe_to_pay": str(fs.amount_safe_to_pay),
        "earliest_date_for_full_payment": (
            fs.earliest_date_for_full_payment.isoformat() if fs.earliest_date_for_full_payment else None
        ),
        "method": fs.method,
        "status": fs.status,
        "payments": [{"date": d.isoformat(), "amount": str(a)} for d, a in fs.payments],
        "changes": [
            {"kind": c.kind, "event_id": c.event.raw_event_id, "new_amount": str(c.new_amount) if c.new_amount else None}
            for c in fs.changes
        ],
        "driving_event": fs.driving_event,
    }
