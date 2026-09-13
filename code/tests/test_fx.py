r"""FX resolution (io_layer/fx.py): exact date, prior-date fallback,
explicit inverse, raise on a missing pair -- never a float in the money
path."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from io_layer.fx import FxTable, NoRateAvailable


def _table():
    rows = [
        {"from_currency": "USD", "to_currency": "INR", "rate_date": date(2024, 1, 15), "rate": Decimal("83.0")},
        {"from_currency": "USD", "to_currency": "INR", "rate_date": date(2024, 2, 15), "rate": Decimal("84.0")},
        {"from_currency": "USD", "to_currency": "EUR", "rate_date": date(2024, 1, 15), "rate": Decimal("0.9")},
    ]
    return FxTable(rows)


def test_exact_date_match():
    fx = _table()
    lookup = fx.lookup("USD", "INR", date(2024, 2, 15))
    assert lookup.rate == Decimal("84.0")
    assert lookup.rate_date == date(2024, 2, 15)
    assert not lookup.inverted


def test_prior_date_fallback():
    fx = _table()
    # No rate exactly on 2024-02-20 -- falls back to the most recent PRIOR
    # rate for the pair (2024-02-15), never a future one.
    lookup = fx.lookup("USD", "INR", date(2024, 2, 20))
    assert lookup.rate == Decimal("84.0")
    assert lookup.rate_date == date(2024, 2, 15)


def test_prior_date_fallback_never_uses_a_future_rate():
    fx = _table()
    lookup = fx.lookup("USD", "INR", date(2024, 1, 20))
    assert lookup.rate_date == date(2024, 1, 15)  # not 2024-02-15


def test_explicit_inverse_when_direct_pair_missing():
    fx = _table()
    # INR->USD isn't in the table at all; USD->INR is -- invert explicitly.
    lookup = fx.lookup("INR", "USD", date(2024, 2, 15))
    assert lookup.inverted
    assert lookup.rate == Decimal(1) / Decimal("84.0")


def test_raises_never_defaults_to_one_when_pair_missing_both_directions():
    fx = _table()
    with pytest.raises(NoRateAvailable):
        fx.lookup("GBP", "JPY", date(2024, 2, 15))


def test_same_currency_is_identity_rate_one():
    fx = _table()
    lookup = fx.lookup("INR", "INR", date(2024, 2, 15))
    assert lookup.rate == Decimal(1)


def test_convert_returns_decimal_not_float():
    fx = _table()
    amount, lookup = fx.convert(Decimal("100"), "USD", "INR", date(2024, 2, 15))
    assert isinstance(amount, Decimal)
    assert amount == Decimal("8400.0")


def test_no_rate_available_before_any_date_in_the_table():
    fx = _table()
    with pytest.raises(NoRateAvailable):
        fx.lookup("USD", "INR", date(2023, 12, 1))
