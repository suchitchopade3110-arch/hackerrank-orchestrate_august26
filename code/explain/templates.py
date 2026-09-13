r"""Deterministic fallback explanation templates (L7). Phase 1 uses these
directly (no model yet); later phases fall back to them on a grounding
failure (design.md \S12) -- a template naming the binding constraint scores
fine on usefulness/consistency, a fluent sentence with an invented number
does not.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, Decimal

from writer import PLAN_DECIMAL_PLACES


def _fmt(amount: Decimal, currency: str) -> str:
    dp = PLAN_DECIMAL_PLACES.get(currency, 2)
    quantum = Decimal(1).scaleb(-dp)
    q = amount.quantize(quantum, rounding=ROUND_DOWN)
    return f"{q:,.{dp}f}"


def explain_full_payment_now(
    requested_amount: Decimal, currency: str, minimum_balance_to_keep: Decimal
) -> str:
    return (
        f"Pay {currency} {_fmt(requested_amount, currency)} today. This leaves at "
        f"least {currency} {_fmt(minimum_balance_to_keep, currency)} available over "
        f"the next 90 days."
    )


def explain_wait(
    requested_amount: Decimal,
    currency: str,
    minimum_balance_to_keep: Decimal,
    earliest_date: date,
    driving_event: str | None = None,
) -> str:
    cause = f", right after {driving_event}" if driving_event else ""
    return (
        f"Wait until {earliest_date.isoformat()}, then pay {currency} "
        f"{_fmt(requested_amount, currency)} in full. Paying sooner would put the "
        f"{currency} {_fmt(minimum_balance_to_keep, currency)} minimum at risk"
        f"{cause}."
    )


def explain_not_recommended(
    desired_completion_date: date, currency: str, minimum_balance_to_keep: Decimal,
    driving_event: str | None = None,
) -> str:
    cause = f" ({driving_event} is the tightest point)" if driving_event else ""
    return (
        f"Do not make this payment by {desired_completion_date.isoformat()}. None of "
        f"the available options keeps the {currency} "
        f"{_fmt(minimum_balance_to_keep, currency)} minimum protected{cause}."
    )


def explain_installments(
    number_of_payments: int,
    payment_amount: Decimal,
    currency: str,
    first_payment_date: date,
    minimum_balance_to_keep: Decimal,
) -> str:
    return (
        f"Use {number_of_payments} installments of {currency} {payment_amount:,}, "
        f"starting {first_payment_date.isoformat()}. This leaves at least {currency} "
        f"{_fmt(minimum_balance_to_keep, currency)} available."
    )


def explain_partial_payment(
    first_amount: Decimal,
    second_amount: Decimal,
    second_date: date,
    currency: str,
    minimum_balance_to_keep: Decimal,
) -> str:
    return (
        f"Pay {currency} {_fmt(first_amount, currency)} today and the remaining "
        f"{currency} {_fmt(second_amount, currency)} on {second_date.isoformat()}. "
        f"This completes the full request and keeps the {currency} "
        f"{_fmt(minimum_balance_to_keep, currency)} minimum protected."
    )


def explain_full_payment_with_changes(
    requested_amount: Decimal,
    currency: str,
    minimum_balance_to_keep: Decimal,
    change_descriptions: list[str],
) -> str:
    changes_text = " and ".join(change_descriptions)
    return (
        f"{changes_text}, then pay {currency} {_fmt(requested_amount, currency)} "
        f"today. This leaves at least {currency} "
        f"{_fmt(minimum_balance_to_keep, currency)} available."
    )
