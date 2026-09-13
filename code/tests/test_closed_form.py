r"""engine/closed_form.py: linearity, suffix-minimum, quantise-down,
partial-remainder exactness (design.md \S7)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from engine.closed_form import amount_safe_to_pay, earliest_date_for_full_payment, quantise_down
from engine.forecast import BalanceCurve


def _curve(balances: dict[date, Decimal], request_date: date) -> BalanceCurve:
    return BalanceCurve(
        request_date=request_date,
        horizon_end=max(balances),
        starting_balance=balances[request_date],
        deltas=(),
        balances=balances,
    )


def test_amount_safe_to_pay_is_the_window_minimum_headroom_clamped():
    req_date = date(2024, 1, 1)
    balances = {
        req_date: Decimal("1000"),
        req_date + timedelta(days=1): Decimal("400"),  # tightest point
        req_date + timedelta(days=2): Decimal("2000"),
    }
    amt = amount_safe_to_pay(_curve(balances, req_date), Decimal("100"), Decimal("10000"))
    # headroom at tightest point = 400 - 100 = 300, clamped to requested (10000) -> 300
    assert amt == Decimal("300.00")


def test_amount_safe_to_pay_clamped_to_requested_amount():
    req_date = date(2024, 1, 1)
    balances = {req_date: Decimal("5000")}
    amt = amount_safe_to_pay(_curve(balances, req_date), Decimal("0"), Decimal("100"))
    assert amt == Decimal("100.00")  # headroom 5000 clamps down to requested 100


def test_amount_safe_to_pay_floors_at_zero_never_negative():
    req_date = date(2024, 1, 1)
    balances = {req_date: Decimal("50")}
    amt = amount_safe_to_pay(_curve(balances, req_date), Decimal("100"), Decimal("500"))
    assert amt == Decimal("0.00")


def test_quantise_down_rounds_toward_zero_not_nearest():
    # Rounding UP could push a plan below minimum_balance_to_keep by a
    # cent -- the wrong direction to be wrong in (design.md \S7).
    assert quantise_down(Decimal("99.999")) == Decimal("99.99")
    assert quantise_down(Decimal("99.991")) == Decimal("99.99")


def test_earliest_date_uses_suffix_minimum_not_pointwise_check():
    req_date = date(2024, 1, 1)
    # Balance dips below threshold on day 3 even though day 1 alone looks
    # safe -- suffix-min must look ahead, not just at each date in isolation.
    balances = {
        req_date: Decimal("1000"),
        req_date + timedelta(days=1): Decimal("1000"),
        req_date + timedelta(days=2): Decimal("100"),  # a later dip
        req_date + timedelta(days=3): Decimal("1000"),
    }
    earliest = earliest_date_for_full_payment(_curve(balances, req_date), Decimal("0"), Decimal("900"))
    # day 0 and day 1 both individually have >=900, but the suffix min
    # through day 2's dip (100) means neither is actually safe for a
    # payment that must stay safe through the rest of the window.
    assert earliest == req_date + timedelta(days=3)


def test_earliest_date_is_none_when_never_safe_in_window():
    req_date = date(2024, 1, 1)
    balances = {req_date: Decimal("10"), req_date + timedelta(days=1): Decimal("10")}
    assert earliest_date_for_full_payment(_curve(balances, req_date), Decimal("0"), Decimal("1000")) is None


def test_partial_payment_remainder_sums_exactly_to_requested_amount():
    requested = Decimal("1000.00")
    amt_safe = quantise_down(Decimal("333.336"))  # -> 333.33
    remainder = requested - amt_safe
    assert amt_safe + remainder == requested
    assert remainder == Decimal("666.67")
