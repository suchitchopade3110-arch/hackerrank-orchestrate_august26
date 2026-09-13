r"""Closed-form reduce_to / stop candidate proposals (L4). One candidate per
eligible event, solved from the deficit at the binding constraint date and
the event's occurrence count inside the window up to that date -- never
sampled from a ladder of guesses (design.md \S8, roadmap doc \S0 finding 4).

reduce_to = max(minimum_allowed_amount, current_amount - ceil(D / k))
If that still doesn't clear the deficit, the candidate is infeasible
(never silently degrades to `stop`; `stop` is proposed and checked on its
own merits by ledger.classify's separate eligibility gate).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from engine.forecast import BalanceCurve
from ledger.canonical import CanonicalEvent
from ledger.recurrence import occurrences

TWO_DP = Decimal("0.01")


def ceil_to_precision(value: Decimal, precision: Decimal = TWO_DP) -> Decimal:
    if value <= 0:
        return Decimal(0)
    n = math.ceil(value / precision)
    return Decimal(n) * precision


def binding_date_and_deficit(
    curve: BalanceCurve, minimum_balance_to_keep: Decimal, requested_amount: Decimal
) -> tuple[date, Decimal]:
    dates = sorted(curve.balances)
    t_star = min(dates, key=lambda d: curve.balances[d])
    headroom = curve.balances[t_star] - minimum_balance_to_keep
    deficit = requested_amount - headroom
    return t_star, deficit


@dataclass(frozen=True)
class SpendingChange:
    kind: str  # "stop" | "reduce_to"
    event: CanonicalEvent
    new_amount: Decimal | None  # None for stop
    cash_freed_by_binding_date: Decimal
    occurrences_up_to_binding: tuple[date, ...]


def propose_stop(
    event: CanonicalEvent, request_date: date, binding_date: date
) -> SpendingChange | None:
    occ = occurrences(event.anchor_date, event.interval_kind, event.interval_n, request_date, binding_date)
    if not occ:
        return None
    freed = event.amount * len(occ)
    return SpendingChange(
        kind="stop",
        event=event,
        new_amount=None,
        cash_freed_by_binding_date=freed,
        occurrences_up_to_binding=tuple(occ),
    )


def propose_reduce_to(
    event: CanonicalEvent, request_date: date, binding_date: date, deficit: Decimal
) -> SpendingChange | None:
    occ = occurrences(event.anchor_date, event.interval_kind, event.interval_n, request_date, binding_date)
    k = len(occ)
    if k == 0 or deficit <= 0:
        return None

    required_cut = ceil_to_precision(deficit / k)
    floor = event.minimum_allowed_amount if event.minimum_allowed_amount is not None else Decimal(0)
    new_amount = max(floor, event.amount - required_cut)
    freed = (event.amount - new_amount) * k
    if freed < deficit:
        return None  # clamp prevented covering the deficit -- infeasible, not a stop

    return SpendingChange(
        kind="reduce_to",
        event=event,
        new_amount=new_amount,
        cash_freed_by_binding_date=freed,
        occurrences_up_to_binding=tuple(occ),
    )


def max_possible_reduce_freed(
    event: CanonicalEvent, request_date: date, binding_date: date
) -> tuple[Decimal, int]:
    """The MOST this event's reduce_to could ever free by the binding date
    (reducing all the way to minimum_allowed_amount), used only to RANK
    reduce as a candidate slot against stop and other events -- never used
    as the actual proposed amount (build_subset_changes solves that in
    closed form once the subset composition is fixed)."""
    occ = occurrences(event.anchor_date, event.interval_kind, event.interval_n, request_date, binding_date)
    k = len(occ)
    if k == 0:
        return Decimal(0), 0
    floor = event.minimum_allowed_amount if event.minimum_allowed_amount is not None else Decimal(0)
    return (event.amount - floor) * k, k


def build_subset_changes(
    subset: tuple[tuple[str, CanonicalEvent], ...],
    request_date: date,
    binding_date: date,
    deficit: Decimal,
) -> tuple[SpendingChange, ...] | None:
    """Each event in the subset covers a SHARE of the deficit rather than
    being independently sized to clear it alone (roadmap-review fix: a
    3-event subset was previously unreachable because each proposal had to
    close the whole gap by itself). `stop` entries apply in full first
    (an all-or-nothing action); the remaining deficit is then split evenly
    across the subset's `reduce_to` events, each solved via the existing
    closed-form propose_reduce_to for its own share. Returns None if the
    subset, applied together, still can't clear the deficit (never
    silently drops to a smaller subset)."""
    stops = [e for kind, e in subset if kind == "stop"]
    reduces = [e for kind, e in subset if kind == "reduce_to"]

    changes: list[SpendingChange] = []
    remaining = deficit

    for event in stops:
        proposal = propose_stop(event, request_date, binding_date)
        if proposal is None:
            return None
        changes.append(proposal)
        remaining -= proposal.cash_freed_by_binding_date

    if reduces and remaining > 0:
        share = remaining / len(reduces)
        for event in reduces:
            proposal = propose_reduce_to(event, request_date, binding_date, share)
            if proposal is None:
                return None
            changes.append(proposal)
            remaining -= proposal.cash_freed_by_binding_date
    # else: the stops already cleared the deficit -- the subset's reduce_to
    # members contribute nothing further and are dropped rather than
    # forced into a token cut, matching "smallest total reduction."

    if remaining > 0:
        return None
    return tuple(changes)


def whole_window_reduction_amount(
    change: SpendingChange, request_date: date, horizon_end: date
) -> Decimal:
    """Total cash freed by this change across the FULL window (used for
    ranking criterion 7, "smallest total reduction"), since a spending
    change is an ongoing behaviour change, not a one-off up to the binding
    date."""
    occ = occurrences(
        change.event.anchor_date, change.event.interval_kind, change.event.interval_n,
        request_date, horizon_end,
    )
    per_occurrence_cut = change.event.amount - (change.new_amount or Decimal(0))
    return per_occurrence_cut * len(occ)
