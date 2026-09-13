r"""Unlabelled distribution check on the full 250-row run (PRD.md \S7 4a /
design.md \S13). The 25 labelled samples are too thin to detect a plateau
on their own; this catches a systematically wrong run (e.g. 80%
not_affordable, or installments never appearing) without needing a single
label. Reference distribution from the 25 samples (roadmap doc \S0):
statuses 9 affordable_with_plan / 7 not_affordable / 6 affordable_later /
3 affordable_now; methods 7 not_recommended / 6 full_payment / 6 wait /
5 installments / 1 partial_payment; 7/25 empty earliest; 3/25 spending
changes.

Usage:
    python code/evaluation/sanity.py [--path output.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ALL_STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
ALL_METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}

# Thresholds derived from the sample distribution, with headroom -- this is
# a plausibility check, not an exact-match one. Every band below has BOTH
# a floor and a ceiling: a one-sided check only catches a run that's too
# extreme in one direction, and missed exactly the defect that motivated
# this file -- 1/250 (0.4%) spending-changes rows sailed through the old
# "<= 30%" check with no floor to catch it against the samples' 12%.
MIN_SINGLE_STATUS_SHARE_ANY = 0.02  # every status present at all (see below), but not vanishingly rare
MAX_SINGLE_STATUS_SHARE = 0.55  # samples' max is 9/25 = 36%; allow up to ~55%
MIN_EMPTY_EARLIEST_SHARE = 0.05
MAX_EMPTY_EARLIEST_SHARE = 0.55  # samples: 7/25 = 28%
MIN_SPENDING_CHANGES_SHARE = 0.02  # samples: 3/25 = 12%; loosened for the
# full 250's structural reality (most deficits dwarf available flexible
# spend -- see error_analysis.md) but still a real floor, not absent
MAX_SPENDING_CHANGES_SHARE = 0.30
MIN_NOT_AFFORDABLE_SHARE = 0.05  # samples: 7/25 = 28%; a floor here would
MAX_NOT_AFFORDABLE_SHARE = 0.55  # catch "nothing is ever affordable" same
# as a ceiling catches "everything is" -- named explicitly rather than
# folded into the generic worst-status check, which wouldn't flag THIS
# status by name if some other status happened to be the extreme one.


def run_sanity_check(path: Path) -> bool:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    n = len(rows)
    print(f"Rows: {n}")

    statuses = Counter(r["affordability_status"] for r in rows)
    methods = Counter(r["recommended_payment_method"] for r in rows)
    empty_earliest = sum(1 for r in rows if not r["earliest_date_for_full_payment"])
    has_changes = sum(1 for r in rows if r["spending_changes_needed"] != "none")

    print("\nStatus mix:")
    for s in sorted(ALL_STATUSES):
        c = statuses.get(s, 0)
        print(f"  {s:<22} {c:>4}  ({c/n:.1%})")

    print("\nMethod mix:")
    for m in sorted(ALL_METHODS):
        c = methods.get(m, 0)
        print(f"  {m:<22} {c:>4}  ({c/n:.1%})")

    print(f"\nEmpty earliest_date_for_full_payment: {empty_earliest}/{n} ({empty_earliest/n:.1%})")
    print(f"Rows with spending_changes_needed != none: {has_changes}/{n} ({has_changes/n:.1%})")

    checks: list[tuple[str, bool]] = []

    missing_statuses = ALL_STATUSES - set(statuses)
    checks.append((f"every affordability_status value appears (missing: {missing_statuses or 'none'})", not missing_statuses))

    missing_methods = ALL_METHODS - set(methods)
    checks.append((f"every recommended_payment_method value appears (missing: {missing_methods or 'none'})", not missing_methods))

    worst_status_share = max(statuses.values()) / n if statuses else 1.0
    checks.append((f"no single status exceeds {MAX_SINGLE_STATUS_SHARE:.0%} of rows (worst: {worst_status_share:.1%})", worst_status_share <= MAX_SINGLE_STATUS_SHARE))
    # Every present status also has a floor -- a status that appears only
    # once or twice (technically "present," passing the check above) is as
    # suspicious as one that's absent; this is what would have caught a
    # regression that pushed almost everything into one bucket while
    # leaving a token row of each other status to satisfy the presence check.
    thin_statuses = {s for s in ALL_STATUSES if 0 < statuses.get(s, 0) / n < MIN_SINGLE_STATUS_SHARE_ANY}
    checks.append((
        f"no present status is vanishingly rare, >= {MIN_SINGLE_STATUS_SHARE_ANY:.0%} once present (thin: {thin_statuses or 'none'})",
        not thin_statuses,
    ))

    checks.append((f"installments appears at least once (count: {methods.get('installments', 0)})", methods.get("installments", 0) > 0))

    not_affordable_share = statuses.get("not_affordable", 0) / n
    checks.append((
        f"not_affordable share in plausible band [{MIN_NOT_AFFORDABLE_SHARE:.0%}, {MAX_NOT_AFFORDABLE_SHARE:.0%}] (actual: {not_affordable_share:.1%})",
        MIN_NOT_AFFORDABLE_SHARE <= not_affordable_share <= MAX_NOT_AFFORDABLE_SHARE,
    ))

    empty_share = empty_earliest / n
    checks.append((
        f"empty-earliest share in plausible band [{MIN_EMPTY_EARLIEST_SHARE:.0%}, {MAX_EMPTY_EARLIEST_SHARE:.0%}] (actual: {empty_share:.1%})",
        MIN_EMPTY_EARLIEST_SHARE <= empty_share <= MAX_EMPTY_EARLIEST_SHARE,
    ))

    changes_share = has_changes / n
    checks.append((
        f"spending-changes share in plausible band [{MIN_SPENDING_CHANGES_SHARE:.0%}, {MAX_SPENDING_CHANGES_SHARE:.0%}] (actual: {changes_share:.1%})",
        MIN_SPENDING_CHANGES_SHARE <= changes_share <= MAX_SPENDING_CHANGES_SHARE,
    ))

    print("\nChecks:")
    all_passed = True
    for desc, passed in checks:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {desc}")
        all_passed = all_passed and passed

    return all_passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=str(REPO_ROOT / "output.csv"))
    args = parser.parse_args()

    ok = run_sanity_check(Path(args.path))
    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
