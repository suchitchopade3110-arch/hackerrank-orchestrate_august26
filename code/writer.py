"""Write predictions to the repository-root output.csv (L8). Column names,
order, dialect, quoting, and line terminator come from io_layer.template
rather than a hardcoded list.

NOTE: PRD.md FR-23 says write both root and dataset/output.csv, but the
project's explicit working rule for this session is "never modify dataset/"
-- dataset/ is treated as strictly read-only, including its output.csv
template. This writer therefore only ever writes the root path. If the
submission format later requires dataset/output.csv to be filled too, that
is a one-line addition here, deliberately deferred.
"""

from __future__ import annotations

import csv
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from io_layer.template import OutputTemplate

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_OUTPUT_PATH = REPO_ROOT / "output.csv"

# roadmap doc \S0: payment_plan amounts are formatted per home_currency.
PLAN_DECIMAL_PLACES = {
    "EUR": 2,
    "USD": 2,
    "INR": 0,
    "ZAR": 0,
    "IDR": 0,
}


def format_amount_safe_to_pay(amount: Decimal) -> str:
    r"""Round down to 2dp, strip trailing zeros (and a trailing '.') -- the
    same convention in every currency (roadmap doc \S0)."""
    q = amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_plan_amount(amount: Decimal, currency: str) -> str:
    dp = PLAN_DECIMAL_PLACES.get(currency, 2)
    quantum = Decimal(1).scaleb(-dp)
    q = amount.quantize(quantum, rounding=ROUND_DOWN)
    return format(q, "f")


def format_payment_plan(payments: list[tuple], currency: str) -> str:
    if not payments:
        return "none"
    parts = [f"{d.isoformat()}:{format_plan_amount(amt, currency)}" for d, amt in payments]
    return "|".join(parts)


def partial_payment_legs(payments: list[tuple], currency: str) -> tuple[Decimal, Decimal]:
    """The two display-precision leg amounts for a partial_payment plan,
    shared by format_partial_payment_plan (writer.py) and
    main.py::_explanation_for so decision_explanation can never quote a
    different number for leg 2 than payment_plan does (second-review
    finding: explain/templates.py used to re-derive leg 2 by independently
    flooring the raw amount, off by up to one display unit from the exact
    remainder computed here -- e.g. request_273's plan read ...:5444849
    while its explanation said "5,444,848"). Round only the FIRST leg down
    to display precision; derive the second leg as the exact remainder
    against the full-precision total (INV-04, spec text) -- never re-round
    it independently (roadmap-review fix, e.g. 42234.73/7145.27
    independently floored to 42234/7145, summing to 49379 instead of the
    true 49380)."""
    (_, amt1), (_, amt2) = payments
    total = amt1 + amt2
    dp = PLAN_DECIMAL_PLACES.get(currency, 2)
    quantum = Decimal(1).scaleb(-dp)
    leg1 = amt1.quantize(quantum, rounding=ROUND_DOWN)
    leg2 = (total - leg1).quantize(quantum)  # normalise display scale only -- value is already exact
    return leg1, leg2


def format_partial_payment_plan(payments: list[tuple], currency: str) -> str:
    """partial_payment's two legs must sum EXACTLY to requested_amount in
    the currency's display precision (INV-04, spec text)."""
    if len(payments) != 2:
        return format_payment_plan(payments, currency)
    (d1, _), (d2, _) = payments
    leg1, leg2 = partial_payment_legs(payments, currency)
    return f"{d1.isoformat()}:{format(leg1, 'f')}|{d2.isoformat()}:{format(leg2, 'f')}"


def format_spending_changes(changes, currency: str) -> str:
    if not changes:
        return "none"
    parts = []
    for c in changes:
        if c.kind == "stop":
            parts.append(f"stop:{c.event.raw_event_id}")
        else:
            parts.append(f"reduce_to:{c.event.raw_event_id}:{format_plan_amount(c.new_amount, currency)}")
    return "|".join(parts)


def format_installment_plan(payments: list[tuple]) -> str:
    r"""Installment amounts are copied verbatim from payment_amount (roadmap
    doc \S0 finding 1) -- no per-currency rounding, since option amounts
    already carry whatever precision the option row supplied."""
    if not payments:
        return "none"
    parts = [f"{d.isoformat()}:{format(amt, 'f')}" for d, amt in payments]
    return "|".join(parts)


def write_output(rows: list[dict], template: OutputTemplate, path: Path = ROOT_OUTPUT_PATH) -> None:
    """rows: list of dicts keyed by template.columns, one per request_id, in
    template.request_ids order (enforced here, not assumed from caller)."""
    by_id = {r["request_id"]: r for r in rows}
    missing = [rid for rid in template.request_ids if rid not in by_id]
    if missing:
        raise ValueError(f"missing output rows for request_ids: {missing[:5]} (+{len(missing)-5} more)" if len(missing) > 5 else f"missing output rows for request_ids: {missing}")

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f,
            delimiter=template.delimiter,
            quotechar=template.quotechar,
            quoting=template.quoting,
            lineterminator=template.lineterminator,
        )
        writer.writerow(template.columns)
        for rid in template.request_ids:
            row = by_id[rid]
            writer.writerow([row.get(col, "") for col in template.columns])
