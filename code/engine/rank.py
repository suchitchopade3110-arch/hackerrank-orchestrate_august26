r"""Lexicographic ranking (L5) -- the spec's own tie-break order, plus one
local criterion (7) that only ever separates candidates the spec's order
left tied (design.md \S8):

  1. completes by desired_completion_date
  2. fewest spending changes
  3. lowest total paid
  4. earliest first payment
  5. fewest payments
  6. lowest payment_option_id (spec's final tie-break)
  7. smallest total reduction (local; sits after 6 so it never reorders
     anything the spec's order would have separated)
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.candidates import Candidate


def _key(c: Candidate):
    first_payment_date = c.payments[0][0] if c.payments else None
    return (
        not c.completes_by_deadline,
        len(c.changes),
        c.total_paid,
        first_payment_date,
        len(c.payments),
        c.option_id or "",
        c.total_reduction,
    )


@dataclass(frozen=True)
class RankResult:
    selected: Candidate
    decided_by: str  # which criterion separated the winner from the runner-up


CRITERION_NAMES = [
    "completes_by_deadline",
    "fewest_changes",
    "lowest_total_paid",
    "earliest_first_payment",
    "fewest_payments",
    "lowest_option_id",
    "smallest_total_reduction",
]


def sorted_feasible(candidates: list[Candidate]) -> list[Candidate]:
    """Feasible candidates (not_recommended excluded) in ranked order --
    the full ordering, so a repair pass can demote to the next entry
    instead of just the single winner."""
    feasible = [c for c in candidates if c.feasible and c.method != "not_recommended"]
    return sorted(feasible, key=_key)


def rank_candidates(candidates: list[Candidate]) -> RankResult:
    ranked = sorted_feasible(candidates)

    if not ranked:
        fallback = next(c for c in candidates if c.method == "not_recommended")
        return RankResult(selected=fallback, decided_by="no_feasible_candidate")

    winner = ranked[0]

    decided_by = "only_candidate"
    if len(ranked) > 1:
        runner_up = ranked[1]
        wk, rk = _key(winner), _key(runner_up)
        for name, wv, rv in zip(CRITERION_NAMES, wk, rk):
            if wv != rv:
                decided_by = name
                break
        else:
            decided_by = "tied_all_criteria"

    return RankResult(selected=winner, decided_by=decided_by)
