r"""Parse request_payment_options.csv rows into concrete payment schedules
and apply the max_installment_months hard filter (roadmap doc \S0 finding 2).

CLOSED per finding 1: dates come from first_payment_date + k *
payment_frequency_days; amount is payment_amount repeated verbatim for every
payment. Nothing is split, recomputed, or residual-adjusted -- fees live
inside total_payable_amount and are asserted to be reproduced exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from io_layer.loader import PaymentOption, Profile


@dataclass(frozen=True)
class OptionSchedule:
    payment_option_id: str
    request_id: str
    payment_method: str  # full_payment | installments
    payments: tuple[tuple[date, Decimal], ...]
    total_payable_amount: Decimal
    financing_fee: Decimal
    eligible: bool
    ineligible_reason: str | None


def _schedule_span_months(first: date, last: date) -> float:
    return (last - first).days / 30.0


def expand_option(option: PaymentOption, profile: Profile) -> OptionSchedule:
    payments = []
    freq = option.payment_frequency_days or 0
    for k in range(option.number_of_payments):
        d = option.first_payment_date + timedelta(days=freq * k)
        payments.append((d, option.payment_amount))
    payments = tuple(payments)

    total = sum((amt for _, amt in payments), Decimal(0))
    assert abs(total - option.total_payable_amount) <= Decimal("0.01"), (
        f"{option.payment_option_id}: verbatim schedule sums to {total}, "
        f"expected total_payable_amount {option.total_payable_amount}"
    )

    eligible = True
    reason = None
    if option.payment_method == "installments":
        if profile.max_installment_months is None:
            eligible = False
            reason = "user_will_not_consider_installments"
        else:
            span = _schedule_span_months(payments[0][0], payments[-1][0])
            if span > profile.max_installment_months + 1e-9:
                eligible = False
                reason = (
                    f"schedule_span_{math.ceil(span)}mo_exceeds_"
                    f"max_installment_months_{profile.max_installment_months}"
                )

    return OptionSchedule(
        payment_option_id=option.payment_option_id,
        request_id=option.request_id,
        payment_method=option.payment_method,
        payments=payments,
        total_payable_amount=option.total_payable_amount,
        financing_fee=option.financing_fee,
        eligible=eligible,
        ineligible_reason=reason,
    )


def expand_options_for_request(
    options: list[PaymentOption], request_id: str, profile: Profile
) -> list[OptionSchedule]:
    return [
        expand_option(o, profile) for o in options if o.request_id == request_id
    ]
