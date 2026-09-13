r"""writer.py: header, dialect, row order from the template, and
per-currency amount formatting against the sample label strings."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datetime import date
from decimal import Decimal

import pandas as pd

from io_layer.template import load_template
from writer import (
    format_amount_safe_to_pay,
    format_installment_plan,
    format_partial_payment_plan,
    format_payment_plan,
    format_plan_amount,
    write_output,
)
from tests.conftest import requires_dataset


@requires_dataset
def test_template_header_and_dialect_match_dataset_output_csv():
    template = load_template()
    assert template.columns == [
        "request_id", "amount_safe_to_pay", "affordability_status",
        "recommended_payment_method", "payment_plan",
        "earliest_date_for_full_payment", "spending_changes_needed",
        "decision_explanation",
    ]
    assert template.lineterminator == "\r\n"
    assert template.delimiter == ","


@requires_dataset
def test_write_output_preserves_template_row_order_not_input_order(tmp_path):
    template = load_template()
    ids = template.request_ids[:5]
    # Deliberately build rows in REVERSED order -- write_output must still
    # emit them in the template's order, not the order given.
    rows = [
        {
            "request_id": rid, "amount_safe_to_pay": "1", "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment", "payment_plan": "none",
            "earliest_date_for_full_payment": "", "spending_changes_needed": "none",
            "decision_explanation": "x",
        }
        for rid in reversed(ids)
    ]
    out_path = tmp_path / "output.csv"
    small_template = type(template)(
        columns=template.columns, request_ids=ids, delimiter=template.delimiter,
        quotechar=template.quotechar, lineterminator=template.lineterminator,
        quoting=template.quoting,
    )
    write_output(rows, template=small_template, path=out_path)

    with open(out_path, newline="") as f:
        written_ids = [row[0] for row in csv.reader(f)][1:]
    assert written_ids == ids  # template order, not input order


@requires_dataset
def test_write_output_raises_on_missing_request_id(tmp_path):
    template = load_template()
    ids = template.request_ids[:3]
    small_template = type(template)(
        columns=template.columns, request_ids=ids, delimiter=template.delimiter,
        quotechar=template.quotechar, lineterminator=template.lineterminator,
        quoting=template.quoting,
    )
    rows = [
        {
            "request_id": ids[0], "amount_safe_to_pay": "1", "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment", "payment_plan": "none",
            "earliest_date_for_full_payment": "", "spending_changes_needed": "none",
            "decision_explanation": "x",
        }
    ]  # missing ids[1] and ids[2]
    try:
        write_output(rows, template=small_template, path=tmp_path / "out.csv")
        assert False, "expected ValueError for missing rows"
    except ValueError as e:
        assert "missing" in str(e)


@requires_dataset
def test_amount_safe_to_pay_formatting_matches_all_25_sample_labels():
    samples = pd.read_csv(REPO_ROOT / "dataset" / "sample_requests.csv", dtype=str, keep_default_na=False)
    for _, row in samples.iterrows():
        expected = row["amount_safe_to_pay"]
        got = format_amount_safe_to_pay(Decimal(expected))
        assert got == expected, f"{row['request_id']}: expected {expected!r}, got {got!r}"


@requires_dataset
def test_payment_plan_formatting_matches_non_installment_sample_labels():
    samples = pd.read_csv(REPO_ROOT / "dataset" / "sample_requests.csv", dtype=str, keep_default_na=False)
    profiles = pd.read_csv(REPO_ROOT / "dataset" / "financial_profiles.csv", dtype=str)
    currency_by_user = dict(zip(profiles.user_id, profiles.home_currency))

    for _, row in samples.iterrows():
        if row["recommended_payment_method"] == "installments" or row["payment_plan"] == "none":
            continue
        currency = currency_by_user[row["user_id"]]
        for part in row["payment_plan"].split("|"):
            _, amt = part.split(":")
            got = format_plan_amount(Decimal(amt), currency)
            assert got == amt, f"{row['request_id']}: expected {amt!r}, got {got!r}"


def test_installment_plan_amounts_are_copied_verbatim_not_currency_rounded():
    payments = [(pd.Timestamp("2024-01-01").date(), Decimal("15952906.67"))]
    result = format_installment_plan(payments)
    assert result == "2024-01-01:15952906.67"  # not rounded to 0dp for IDR


def test_partial_payment_legs_sum_exactly_for_0dp_currency():
    """Regression: independently floor-rounding each leg to a 0dp currency
    silently under-sums the request when amount_safe_to_pay carries
    fractional precision the display truncates (e.g. 42234.73/7145.27
    each floored alone -> 42234+7145=49379, not the true 49380)."""
    d1, d2 = date(2026, 1, 7), date(2026, 2, 15)
    payments = [(d1, Decimal("42234.73")), (d2, Decimal("7145.27"))]
    result = format_partial_payment_plan(payments, "INR")
    assert result == "2026-01-07:42234|2026-02-15:7146"
    leg1_str, leg2_str = (p.split(":")[1] for p in result.split("|"))
    assert int(leg1_str) + int(leg2_str) == 49380


def test_partial_payment_legs_sum_exactly_for_2dp_currency():
    d1, d2 = date(2024, 1, 1), date(2024, 2, 1)
    payments = [(d1, Decimal("300.555")), (d2, Decimal("699.445"))]
    result = format_partial_payment_plan(payments, "EUR")
    leg1_str, leg2_str = (p.split(":")[1] for p in result.split("|"))
    assert Decimal(leg1_str) + Decimal(leg2_str) == Decimal("1000.00")
