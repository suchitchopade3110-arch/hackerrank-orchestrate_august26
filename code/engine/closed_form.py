r"""The two closed-form outputs (L3): amount_safe_to_pay and
earliest_date_for_full_payment. Linear balance curve => no search needed
(design.md \S7).

earliest_date_for_full_payment measures capacity only: computed with no
reference to payment_methods_user_will_consider, independently of plan
selection. An empty result does NOT imply not_affordable (INV-16) --
an installment plan never needs the full amount safe as a single payment.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from engine.forecast import BalanceCurve

TWO_DP = Decimal("0.01")


def quantise_down(amount: Decimal, precision: Decimal = TWO_DP) -> Decimal:
    return amount.quantize(precision, rounding=ROUND_DOWN)


def clamp(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, value))


def amount_safe_to_pay(
    curve: BalanceCurve, minimum_balance_to_keep: Decimal, requested_amount: Decimal
) -> Decimal:
    dates = sorted(curve.balances)
    headroom = min(curve.balances[d] - minimum_balance_to_keep for d in dates)
    clamped = clamp(headroom, Decimal(0), requested_amount)
    return quantise_down(clamped)


def earliest_date_for_full_payment(
    curve: BalanceCurve, minimum_balance_to_keep: Decimal, requested_amount: Decimal
) -> date | None:
    dates = sorted(curve.balances)
    # suffix_min[d] = min(bal(t) for t in [d, window_end])
    suffix_min: dict[date, Decimal] = {}
    running_min = None
    for d in reversed(dates):
        bal = curve.balances[d]
        running_min = bal if running_min is None else min(running_min, bal)
        suffix_min[d] = running_min

    for d in dates:
        if suffix_min[d] - minimum_balance_to_keep >= requested_amount:
            return d
    return None
