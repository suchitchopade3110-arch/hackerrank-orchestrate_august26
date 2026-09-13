"""Shared test fixtures -- factory helpers so each test file states only
the fields it cares about, not all eighteen of RawEvent's."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from io_layer.loader import Profile, RawEvent, RequestRow


def make_event(
    event_id: str = "event_1",
    user_id: str = "user_1",
    event_type: str = "expense",
    description: str = "Test expense",
    category: str = "rent",
    direction: str = "debit",
    amount_home: Decimal | str | None = "100",
    currency_original: str = "INR",
    event_date: date = date(2024, 1, 1),
    settlement_date: date | None = None,
    status: str = "settled",
    linked_event_id: str | None = None,
    flexibility: str = "fixed",
    minimum_allowed_amount: Decimal | str | None = None,
) -> RawEvent:
    amt = Decimal(amount_home) if amount_home is not None else None
    min_amt = Decimal(minimum_allowed_amount) if minimum_allowed_amount is not None else None
    return RawEvent(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type,
        description=description,
        category=category,
        direction=direction,
        amount_home=amt,
        amount_original=amt,
        currency_original=currency_original,
        fx_rate=Decimal(1),
        fx_rate_date=event_date,
        fx_inverted=False,
        event_date=event_date,
        settlement_date=settlement_date or event_date,
        status=status,
        linked_event_id=linked_event_id,
        flexibility=flexibility,
        minimum_allowed_amount=min_amt,
        amount_was_blank=amount_home is None,
    )


def make_profile(
    user_id: str = "user_1",
    home_currency: str = "INR",
    current_available_balance: Decimal | str = "10000",
    minimum_balance_to_keep: Decimal | str = "1000",
    payment_methods_user_will_consider: frozenset[str] = frozenset({"full_payment"}),
    max_installment_months: int | None = None,
    expense_categories_to_protect: frozenset[str] = frozenset(),
    expense_categories_willing_to_reduce: frozenset[str] = frozenset(),
    expense_categories_willing_to_stop: frozenset[str] = frozenset(),
) -> Profile:
    return Profile(
        user_id=user_id,
        home_currency=home_currency,
        current_available_balance=Decimal(current_available_balance),
        minimum_balance_to_keep=Decimal(minimum_balance_to_keep),
        financial_priorities=frozenset(),
        expense_categories_to_protect=expense_categories_to_protect,
        expense_categories_willing_to_reduce=expense_categories_willing_to_reduce,
        expense_categories_willing_to_stop=expense_categories_willing_to_stop,
        payment_methods_user_will_consider=payment_methods_user_will_consider,
        max_installment_months=max_installment_months,
    )


def make_request(
    request_id: str = "request_1",
    user_id: str = "user_1",
    request_date: date = date(2024, 1, 1),
    request_type: str = "purchase",
    requested_amount: Decimal | str = "1000",
    desired_completion_date: date = date(2024, 2, 1),
    allows_partial_payment: bool = True,
    request_text: str = "Test request",
) -> RequestRow:
    return RequestRow(
        request_id=request_id,
        user_id=user_id,
        request_date=request_date,
        request_type=request_type,
        requested_amount=Decimal(requested_amount),
        desired_completion_date=desired_completion_date,
        allows_partial_payment=allows_partial_payment,
        request_text=request_text,
    )
