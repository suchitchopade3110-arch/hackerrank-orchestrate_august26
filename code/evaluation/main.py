r"""Per-field scorer against dataset/sample_requests.csv's 25 labelled rows
(PRD.md \S8 step 5). Reports per-field accuracy, not row accuracy, and
buckets errors by field x request_type x affordability_status so a wrong
config decision (design.md \S3) is findable in one look.

Usage:
    python code/evaluation/main.py [--verbose]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

import main as pipeline

DATASET_DIR = REPO_ROOT.parent / "dataset"

FIELDS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]


def load_labels() -> list[dict]:
    df = pd.read_csv(DATASET_DIR / "sample_requests.csv", dtype=str, keep_default_na=False)
    return df.to_dict("records")


def score(
    verbose: bool = False,
    income_mode: str | None = None,
    use_vlm: bool = True,
    use_llm: bool = True,
    use_message_amendments: bool | None = None,
    variable_expense_categories: tuple[str, ...] | None = None,
    variable_expense_model: dict[str, dict] | None = None,
    k_stdev: float | None = None,
    daily_drip_fraction: float | None = None,
) -> dict:
    labels = load_labels()
    sample_ids = [r["request_id"] for r in labels]
    predicted_rows = pipeline.run(
        sample_ids, verbose=False, income_mode=income_mode,
        use_vlm=use_vlm, use_llm=use_llm, use_message_amendments=use_message_amendments,
        variable_expense_categories=variable_expense_categories,
        variable_expense_model=variable_expense_model, k_stdev=k_stdev,
        daily_drip_fraction=daily_drip_fraction,
    )
    pred_by_id = {r["request_id"]: r for r in predicted_rows}

    per_field = {f: {"correct": 0, "total": 0} for f in FIELDS}
    buckets = defaultdict(lambda: defaultdict(int))  # field -> (request_type, status) -> miss count
    mismatches = []

    for label in labels:
        rid = label["request_id"]
        pred = pred_by_id.get(rid)
        for f in FIELDS:
            per_field[f]["total"] += 1
            expected = label[f]
            actual = pred.get(f, "") if pred else ""
            if actual == expected:
                per_field[f]["correct"] += 1
            else:
                buckets[f][(label["request_type"], label["affordability_status"])] += 1
                mismatches.append(
                    {
                        "request_id": rid,
                        "field": f,
                        "expected": expected,
                        "actual": actual,
                        "request_type": label["request_type"],
                        "expected_status": label["affordability_status"],
                    }
                )

    if verbose:
        for m in mismatches:
            print(
                f"  MISMATCH {m['request_id']} field={m['field']} "
                f"expected={m['expected']!r} actual={m['actual']!r} "
                f"(request_type={m['request_type']}, expected_status={m['expected_status']})"
            )

    print("\nPer-field accuracy on the 25 labelled samples:")
    for f in FIELDS:
        c, t = per_field[f]["correct"], per_field[f]["total"]
        print(f"  {f:<32} {c:>2}/{t}  ({c/t:.0%})")

    print("\nError buckets (field -> (request_type, expected_status): miss count):")
    for f in FIELDS:
        if buckets[f]:
            print(f"  {f}:")
            for key, count in sorted(buckets[f].items(), key=lambda kv: -kv[1]):
                print(f"    {key}: {count}")

    return {"per_field": per_field, "buckets": buckets, "mismatches": mismatches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    score(verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
