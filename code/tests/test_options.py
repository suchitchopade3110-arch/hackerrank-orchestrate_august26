r"""engine/options.py: schedule expansion from first_payment_date +
payment_frequency_days, and the max_installment_months filter (roadmap
doc \S0 findings 1 and 2), including the blank-max case."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from engine.options import expand_option
from io_layer.loader import PaymentOption
from tests.helpers import make_profile


def _option(**kwargs) -> PaymentOption:
    defaults = dict(
        payment_option_id="opt_1",
        request_id="request_1",
        payment_method="installments",
        payment_amount=Decimal("1000"),
        number_of_payments=3,
        first_payment_date=date(2024, 1, 10),
        payment_frequency_days=30,
        financing_fee=Decimal("50"),
        total_payable_amount=Decimal("3000"),
    )
    defaults.update(kwargs)
    return PaymentOption(**defaults)


def test_schedule_dates_are_first_payment_date_plus_k_times_frequency():
    schedule = expand_option(_option(), make_profile(max_installment_months=12))
    assert [d for d, _ in schedule.payments] == [
        date(2024, 1, 10), date(2024, 2, 9), date(2024, 3, 10),
    ]


def test_schedule_amounts_copy_payment_amount_verbatim():
    schedule = expand_option(
        _option(payment_amount=Decimal("1852.11"), total_payable_amount=Decimal("5556.33")),
        make_profile(max_installment_months=12),
    )
    assert all(amt == Decimal("1852.11") for _, amt in schedule.payments)


def test_max_installment_months_blank_rejects_every_installment_option():
    """Blank max_installment_months means the user will not consider
    installments at all -- drop the option regardless of its span."""
    profile = make_profile(max_installment_months=None)
    schedule = expand_option(
        _option(number_of_payments=2, payment_frequency_days=30, total_payable_amount=Decimal("2000")),
        profile,
    )
    assert schedule.eligible is False
    assert schedule.ineligible_reason == "user_will_not_consider_installments"


def test_max_installment_months_filters_a_schedule_that_runs_too_long():
    # 15 payments, 30-day frequency -> spans 14*30 = 420 days ~= 14 months,
    # exceeding a 12-month cap.
    profile = make_profile(max_installment_months=12)
    schedule = expand_option(
        _option(number_of_payments=15, payment_frequency_days=30, total_payable_amount=Decimal("15000")),
        profile,
    )
    assert schedule.eligible is False
    assert "exceeds_max_installment_months_12" in schedule.ineligible_reason


def test_schedule_within_cap_is_eligible():
    profile = make_profile(max_installment_months=12)
    schedule = expand_option(_option(number_of_payments=3, payment_frequency_days=30), profile)
    assert schedule.eligible is True
    assert schedule.ineligible_reason is None


def test_full_payment_option_is_never_filtered_by_installment_cap():
    profile = make_profile(max_installment_months=None)
    schedule = expand_option(
        _option(
            payment_method="full_payment", number_of_payments=1, payment_frequency_days=None,
            total_payable_amount=Decimal("1000"),
        ),
        profile,
    )
    assert schedule.eligible is True
