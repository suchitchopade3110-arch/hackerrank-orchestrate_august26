"""Grid search over the variable-expense model's amount_rule x
occurrence_rule combinations (ledger/canonical.py's
`variable_expense_model`), against the 25 labelled samples.

Does NOT touch engine/ -- every combination feeds through
ledger.canonical.build_canonical_events's existing `variable_expense_model`
parameter (itself only reachable via config.yaml's `recurring.
variable_expense_model` key in normal operation; this script passes it
directly to avoid the ~25s-per-call cost of round-tripping through
config.yaml + a full CSV reload for every grid point). Candidate
enumeration, ranking, and the status table (engine/candidates.py, rank.py,
status.py) run completely unmodified via main.decide().

Grid: the SAME (amount_rule, occurrence_rule) pair is applied uniformly to
every category in `variable_expense_categories` (groceries/transport/
dining by default) -- not each category swept independently. An
independent per-category grid is (6*3)^3 = 5,832 points; at the cost of
one pipeline pass per point (candidate enumeration + ranking + invariants
for 25 requests), that is hours, not minutes. The uniform grid is
6*3 = 18 points and runs in well under a minute once the dataset is
loaded once (this script's whole reason to exist rather than calling
evaluation/main.py::score() per point). Left as a documented follow-up in
ablations.md rather than run silently smaller than described.

Does NOT special-case any request_id: every grid point runs the identical
code path for all 25 samples; nothing here reads sample_requests.csv's
label columns except to score the already-computed prediction against them.

Usage:
    python code/evaluation/fit.py [--top N] [--k-stdev K]
"""

from __future__ import annotations

import argparse
import itertools
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT.parent / ".env")

import pandas as pd

import main as pipeline
from engine.forecast import build_balance_curve
from engine.options import expand_options_for_request
from io_layer import loader
from ledger.canonical import build_canonical_events

AMOUNT_RULES = ["last", "mean", "median", "max", "p75", "mean_plus_k_stdev"]
OCCURRENCE_RULES = ["observed_cadence", "weekly_anchored", "category_level_cadence"]
DAILY_DRIP_FRACTIONS = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
DEFAULT_CATEGORIES = ("groceries", "transport", "dining")


def _load_once():
    """Load and FX-convert the dataset exactly once -- every grid point
    reuses these in-memory objects, only rebuilding canonical events."""
    fx = loader.FxTable.load()
    profiles = loader.load_profiles()
    events = loader.load_events(fx=fx, profiles=profiles, exclusion_log=[])
    requests = loader.load_requests(loader.DATASET_DIR / "sample_requests.csv")
    payment_options = loader.load_payment_options()

    events_by_user: dict[str, list] = {}
    for e in events:
        events_by_user.setdefault(e.user_id, []).append(e)

    labels = pd.read_csv(
        loader.DATASET_DIR / "sample_requests.csv", dtype=str, keep_default_na=False
    ).to_dict("records")

    return profiles, events_by_user, {r.request_id: r for r in requests}, payment_options, labels


def run_one_model(
    variable_expense_model: dict,
    k_stdev: float,
    profiles,
    events_by_user,
    requests_by_id,
    payment_options,
    labels,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
) -> dict:
    """Core grid-point evaluator: takes the variable_expense_model dict
    directly (per-category rules need not be uniform), rebuilds canonical
    events per request, and runs the unmodified engine via main.decide()."""
    matches = {"amount_safe_to_pay": 0, "earliest_date_for_full_payment": 0, "payment_plan": 0}

    for label in labels:
        rid = label["request_id"]
        req = requests_by_id[rid]
        profile = profiles[req.user_id]
        user_events = events_by_user.get(req.user_id, [])

        canonical = build_canonical_events(
            user_events, profile, req.request_date, [],
            variable_expense_categories=categories,
            variable_expense_model=variable_expense_model,
            k_stdev=k_stdev,
        )
        curve = build_balance_curve(canonical, req.request_date, profile.current_available_balance)
        options = expand_options_for_request(payment_options, rid, profile)

        row, _audit, _fs = pipeline.decide(profile, req, canonical, curve, options)

        for field in matches:
            if row[field] == label[field]:
                matches[field] += 1

    return matches


def run_one_combination(
    amount_rule: str,
    occurrence_rule: str,
    k_stdev: float,
    profiles,
    events_by_user,
    requests_by_id,
    payment_options,
    labels,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    daily_drip_fraction: float = 0.15,
) -> dict:
    variable_expense_model = {
        cat: {
            "amount_rule": amount_rule, "occurrence_rule": occurrence_rule,
            "daily_drip_fraction": daily_drip_fraction,
        }
        for cat in categories
    }
    return run_one_model(
        variable_expense_model, k_stdev,
        profiles, events_by_user, requests_by_id, payment_options, labels, categories,
    )


PER_CATEGORY_FRACTIONS = [0.05, 0.1, 0.15, 0.2, 0.3, 0.5]


def run_percat_grid(
    k_stdev: float, categories: tuple[str, ...] = DEFAULT_CATEGORIES
) -> list[dict]:
    """Independent per-category daily_drip_fraction sweep (roadmap review
    Tier-1 #1: 'let the fraction itself be a swept parameter per category
    rather than uniform'). PER_CATEGORY_FRACTIONS^len(categories) points --
    6^3 = 216 for the default 3 categories, each point one pipeline pass
    over the 25 samples; ~0.5s/point empirically, so well under 5 minutes
    total. Does not touch engine/ -- same as run_grid, only the model dict
    fed to build_canonical_events varies."""
    profiles, events_by_user, requests_by_id, payment_options, labels = _load_once()

    results = []
    for combo in itertools.product(PER_CATEGORY_FRACTIONS, repeat=len(categories)):
        per_cat_fraction = dict(zip(categories, combo))
        variable_expense_model = {
            cat: {"amount_rule": "max", "occurrence_rule": "daily_drip", "daily_drip_fraction": frac}
            for cat, frac in per_cat_fraction.items()
        }
        matches = run_one_model(
            variable_expense_model, k_stdev,
            profiles, events_by_user, requests_by_id, payment_options, labels, categories,
        )
        total = sum(matches.values())
        results.append(
            {
                "amount_rule": "n/a",
                "occurrence_rule": "daily_drip",
                "k_stdev": k_stdev,
                "daily_drip_fraction": None,
                "per_category_fraction": per_cat_fraction,
                "categories": categories,
                "amount_safe_to_pay": matches["amount_safe_to_pay"],
                "earliest_date_for_full_payment": matches["earliest_date_for_full_payment"],
                "payment_plan": matches["payment_plan"],
                "total": total,
            }
        )
    return results


def write_top_n_percat(results: list[dict], n: int, path: Path) -> None:
    ranked = sorted(
        results,
        key=lambda r: (-r["total"], -r["amount_safe_to_pay"], -r["earliest_date_for_full_payment"]),
    )
    top = ranked[:n]

    lines = [
        "## 13. Per-category `daily_drip_fraction` sweep (independent, not uniform)",
        "",
        f"Roadmap review Tier-1 #1: sweep the fraction per category rather than"
        f" uniformly across groceries/transport/dining. Grid:"
        f" {PER_CATEGORY_FRACTIONS} per category, {len(PER_CATEGORY_FRACTIONS)}^3 ="
        f" {len(PER_CATEGORY_FRACTIONS) ** 3} points, each one full pipeline pass"
        f" over the 25 labelled samples via the unmodified main.decide(). Same"
        f" scoring as §12: exact match on amount_safe_to_pay,"
        f" earliest_date_for_full_payment, payment_plan. No request_id is"
        f" special-cased anywhere in fit.py or canonical.py.",
        "",
        f"Top {n} by total exact matches, ties broken by amount_safe_to_pay then earliest:",
        "",
        "| Rank | groceries frac | transport frac | dining frac | amount_safe_to_pay | earliest_date_for_full_payment | payment_plan | total |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(top, start=1):
        pcf = r["per_category_fraction"]
        lines.append(
            f"| {i} | {pcf.get('groceries', 'n/a')} | {pcf.get('transport', 'n/a')} | "
            f"{pcf.get('dining', 'n/a')} | {r['amount_safe_to_pay']}/25 | "
            f"{r['earliest_date_for_full_payment']}/25 | {r['payment_plan']}/25 | {r['total']}/75 |"
        )
    lines.append("")

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nAppended per-category top {n} to {path}")


def run_grid(k_stdev: float, categories: tuple[str, ...] = DEFAULT_CATEGORIES) -> list[dict]:
    profiles, events_by_user, requests_by_id, payment_options, labels = _load_once()

    results = []
    for amount_rule, occurrence_rule in itertools.product(AMOUNT_RULES, OCCURRENCE_RULES):
        matches = run_one_combination(
            amount_rule, occurrence_rule, k_stdev,
            profiles, events_by_user, requests_by_id, payment_options, labels, categories,
        )
        total = sum(matches.values())
        results.append(
            {
                "amount_rule": amount_rule,
                "occurrence_rule": occurrence_rule,
                "k_stdev": k_stdev,
                "daily_drip_fraction": None,
                "categories": categories,
                "amount_safe_to_pay": matches["amount_safe_to_pay"],
                "earliest_date_for_full_payment": matches["earliest_date_for_full_payment"],
                "payment_plan": matches["payment_plan"],
                "total": total,
            }
        )
        print(
            f"  {amount_rule:<18} {occurrence_rule:<22} "
            f"amt={matches['amount_safe_to_pay']:>2} earliest={matches['earliest_date_for_full_payment']:>2} "
            f"plan={matches['payment_plan']:>2} total={total:>2}"
        )

    # daily_drip is a fourth occurrence_rule that doesn't use amount_rule at
    # all (its amount is a smoothed daily rate, not an amount_rule pick) --
    # swept along its own axis, daily_drip_fraction, instead of crossed
    # with amount_rule (reinstated per roadmap review; see ablations.md \S12).
    for fraction in DAILY_DRIP_FRACTIONS:
        matches = run_one_combination(
            "max", "daily_drip", k_stdev,
            profiles, events_by_user, requests_by_id, payment_options, labels, categories,
            daily_drip_fraction=fraction,
        )
        total = sum(matches.values())
        results.append(
            {
                "amount_rule": "n/a",
                "occurrence_rule": "daily_drip",
                "k_stdev": k_stdev,
                "daily_drip_fraction": fraction,
                "categories": categories,
                "amount_safe_to_pay": matches["amount_safe_to_pay"],
                "earliest_date_for_full_payment": matches["earliest_date_for_full_payment"],
                "payment_plan": matches["payment_plan"],
                "total": total,
            }
        )
        print(
            f"  {'daily_drip':<18} fraction={fraction:<13} "
            f"amt={matches['amount_safe_to_pay']:>2} earliest={matches['earliest_date_for_full_payment']:>2} "
            f"plan={matches['payment_plan']:>2} total={total:>2}"
        )

    return results


def write_top_n(results: list[dict], n: int, path: Path) -> None:
    ranked = sorted(results, key=lambda r: (-r["total"], -r["amount_safe_to_pay"], -r["earliest_date_for_full_payment"]))
    top = ranked[:n]

    lines = [
        "## 12. `fit.py` re-run with `daily_drip` reinstated as a fourth occurrence_rule",
        "",
        "Roadmap review found the closed 3-rule grid's own winner (§11,"
        " `observed_cadence`, 36/75) scored WORSE than the daily-drip fraction"
        " mechanism it had replaced (§9, 39/75-equivalent) -- because daily_drip's"
        " category-wide one-day-at-a-time stepping isn't expressible in"
        " {observed_cadence, weekly_anchored, category_level_cadence}. Reinstated"
        " as a fourth occurrence_rule (`ledger/canonical.py`) and swept along its"
        " own axis (`daily_drip_fraction`, 8 points) rather than crossed with"
        " amount_rule -- daily_drip's amount is a smoothed historical daily rate,"
        " not an amount_rule pick, so amount_rule is `n/a` for these rows. Same"
        " scoring as §11: exact match against all 25 labelled samples on"
        " amount_safe_to_pay, earliest_date_for_full_payment, payment_plan. No"
        " request_id is special-cased anywhere in fit.py or canonical.py.",
        "",
        f"Top {n} across BOTH the original 18-point grid and the 8-point daily_drip sweep (26 points total), by total exact matches, ties broken by amount_safe_to_pay then earliest:",
        "",
        "| Rank | amount_rule | occurrence_rule | daily_drip_fraction | amount_safe_to_pay | earliest_date_for_full_payment | payment_plan | total | Full config |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(top, start=1):
        if r["occurrence_rule"] == "daily_drip":
            model_lines = "".join(
                f"  {c}: {{amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: {r['daily_drip_fraction']}}}\n"
                for c in r["categories"]
            )
        else:
            model_lines = "".join(
                f"  {c}: {{amount_rule: {r['amount_rule']}, occurrence_rule: {r['occurrence_rule']}}}\n"
                for c in r["categories"]
            )
        cfg = (
            "```yaml\n"
            "variable_expense_categories: [" + ", ".join(r["categories"]) + "]\n"
            "variable_expense_model:\n"
            + model_lines
            + f"k_stdev: {r['k_stdev']}\n"
            "```"
        )
        fraction_display = r["daily_drip_fraction"] if r["daily_drip_fraction"] is not None else "n/a"
        lines.append(
            f"| {i} | `{r['amount_rule']}` | `{r['occurrence_rule']}` | {fraction_display} | "
            f"{r['amount_safe_to_pay']}/25 | {r['earliest_date_for_full_payment']}/25 | "
            f"{r['payment_plan']}/25 | {r['total']}/75 | {cfg} |"
        )
    lines.append("")

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nAppended top {n} to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--k-stdev", type=float, default=1.0)
    parser.add_argument(
        "--per-category", action="store_true",
        help="Run the independent per-category daily_drip_fraction sweep (§13) instead of the uniform grid",
    )
    args = parser.parse_args()

    if args.per_category:
        print(
            f"Per-category grid search: {len(PER_CATEGORY_FRACTIONS)} fractions ^ "
            f"{len(DEFAULT_CATEGORIES)} categories = {len(PER_CATEGORY_FRACTIONS) ** len(DEFAULT_CATEGORIES)} points\n"
        )
        results = run_percat_grid(k_stdev=args.k_stdev)
        write_top_n_percat(results, args.top, REPO_ROOT / "evaluation" / "ablations.md")
        return 0

    print(f"Grid search: {len(AMOUNT_RULES)} amount_rules x {len(OCCURRENCE_RULES)} occurrence_rules "
          f"= {len(AMOUNT_RULES) * len(OCCURRENCE_RULES)} combinations, uniform across "
          f"{DEFAULT_CATEGORIES}\n")

    results = run_grid(k_stdev=args.k_stdev)
    write_top_n(results, args.top, REPO_ROOT / "evaluation" / "ablations.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
