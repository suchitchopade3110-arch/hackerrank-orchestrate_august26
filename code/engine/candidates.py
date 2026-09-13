"""Candidate plan enumeration (L4): full payment today, wait, a two-payment
partial, one candidate per surviving payment option, each (except wait and
partial_payment -- see below) crossed with spending-change subsets.

wait and partial_payment are generated once at their baseline (0-change)
amounts only. FR-14/FR-15 define amount_safe_to_pay and
earliest_date_for_full_payment *before* optional spending changes, and
INV-04 requires partial_payment's first payment to equal that exact
amount_safe_to_pay -- crossing them with changes would either be a no-op
(the date/amount is already fixed by the baseline closed form) or would
violate that invariant. full_payment and installment options are exactly
where a change can flip an infeasible candidate to feasible (the
changes-enable-full-payment regression fixture, see evaluation/
error_analysis.md), so those are the ones crossed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from config import load_config
from engine import closed_form
from engine.forecast import build_balance_curve
from engine.options import OptionSchedule
from engine.reductions import (
    SpendingChange,
    binding_date_and_deficit,
    build_subset_changes,
    max_possible_reduce_freed,
    whole_window_reduction_amount,
)
from engine.simulate import apply_spending_changes, simulate_candidate
from io_layer.loader import Profile, RequestRow
from ledger.canonical import CanonicalEvent
from ledger.classify import eligible_flexible_events, reduce_eligible, stop_eligible
from ledger.recurrence import occurrences

_cfg = load_config()
MAX_CHANGES = _cfg.get("spending_changes", {}).get("max_changes", 3)
TOP_N_EVENTS_FOR_SUBSETS = 6
HORIZON_DAYS = _cfg.get("horizon", {}).get("days", 90)
ENFORCE_OUTSIDE_HORIZON = _cfg.get("horizon", {}).get("enforce_outside", False)


def _feasible(sim) -> bool:
    r"""horizon.enforce_outside (config.yaml): a tail breach (strictly past
    the window) is logged either way (sim.tail_breach, carried into the
    audit trace) but only demotes the candidate when this flag is set --
    default False means a long installment plan the dataset went out of
    its way to supply isn't silently rejected for a breach outside the
    90-day forecast the spec actually scores (design.md \S8)."""
    if ENFORCE_OUTSIDE_HORIZON and sim.tail_breach:
        return False
    return sim.feasible


@dataclass(frozen=True)
class Candidate:
    method: str  # full_payment | partial_payment | installments | wait | not_recommended
    payments: tuple[tuple[date, Decimal], ...]
    changes: tuple[SpendingChange, ...]
    option_id: str | None
    total_paid: Decimal
    total_reduction: Decimal
    completes_by_deadline: bool
    tail_breach: bool
    feasible: bool


def _candidate_change_slots(
    canonical_events: list[CanonicalEvent],
    profile: Profile,
    request_date: date,
    binding_date: date,
) -> list[tuple[str, CanonicalEvent, Decimal]]:
    """Ranked (kind, event, max_possible_cash_freed) slots. stop and
    reduce_to are SIBLING alternatives for the same event -- both are
    proposed when both are eligible, not a fallback chain where reduce
    shadows stop (roadmap-review fix). The "max possible" figure here is
    only for ranking which events matter most; the actual proposed amount
    for a chosen subset is solved by build_subset_changes once the
    subset's composition (and therefore each member's fair share of the
    deficit) is fixed."""
    eligible_events = eligible_flexible_events(canonical_events, profile)
    slots: list[tuple[str, CanonicalEvent, Decimal]] = []
    for event in eligible_events:
        if stop_eligible(event, profile):
            occ = occurrences_up_to(event, request_date, binding_date)
            if occ:
                slots.append(("stop", event, event.amount * len(occ)))
        if reduce_eligible(event, profile):
            max_freed, k = max_possible_reduce_freed(event, request_date, binding_date)
            if k > 0 and max_freed > 0:
                slots.append(("reduce_to", event, max_freed))
    slots.sort(key=lambda s: s[2], reverse=True)
    return slots


def occurrences_up_to(event: CanonicalEvent, request_date: date, binding_date: date) -> list[date]:
    return occurrences(event.anchor_date, event.interval_kind, event.interval_n, request_date, binding_date)


def _change_subsets(
    canonical_events: list[CanonicalEvent],
    profile: Profile,
    request_date: date,
    binding_date: date,
    deficit: Decimal,
) -> list[tuple[SpendingChange, ...]]:
    if deficit <= 0:
        return [()]

    slots = _candidate_change_slots(canonical_events, profile, request_date, binding_date)
    # Rank by the best of each event's slots, keep the top 6 EVENTS (design
    # doc's "top six"), but carry both action-slots for each kept event so
    # stop and reduce_to remain siblings through subset enumeration.
    best_per_event: dict[str, Decimal] = {}
    for kind, event, freed in slots:
        best_per_event[event.event_id] = max(best_per_event.get(event.event_id, Decimal(0)), freed)
    top_event_ids = {
        eid for eid, _ in sorted(best_per_event.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N_EVENTS_FOR_SUBSETS]
    }
    top_slots = [s for s in slots if s[1].event_id in top_event_ids]

    subsets: list[tuple[SpendingChange, ...]] = [()]
    for size in range(1, MAX_CHANGES + 1):
        for combo in combinations(top_slots, size):
            events_in_combo = [event for _, event, _ in combo]
            if len({e.event_id for e in events_in_combo}) != len(events_in_combo):
                continue  # stop and reduce_to on the same event -- INV-10 forbids sharing
            resolved = build_subset_changes(
                tuple((kind, event) for kind, event, _ in combo), request_date, binding_date, deficit
            )
            if resolved is not None:
                subsets.append(resolved)
    return subsets


def enumerate_candidates(
    profile: Profile,
    req: RequestRow,
    canonical_events: list[CanonicalEvent],
    baseline_curve,
    option_schedules: list[OptionSchedule],
    horizon_days: int = HORIZON_DAYS,
) -> list[Candidate]:
    min_balance = profile.minimum_balance_to_keep
    horizon_end = req.request_date + timedelta(days=horizon_days)
    accepts = profile.payment_methods_user_will_consider

    amt_safe_baseline = closed_form.amount_safe_to_pay(
        baseline_curve, min_balance, req.requested_amount
    )
    earliest_baseline = closed_form.earliest_date_for_full_payment(
        baseline_curve, min_balance, req.requested_amount
    )
    binding_date, deficit = binding_date_and_deficit(
        baseline_curve, min_balance, req.requested_amount
    )

    candidates: list[Candidate] = []

    subsets = _change_subsets(canonical_events, profile, req.request_date, binding_date, deficit)

    for subset in subsets:
        modified_events = apply_spending_changes(canonical_events, subset)
        total_reduction = sum(
            (whole_window_reduction_amount(c, req.request_date, horizon_end) for c in subset),
            Decimal(0),
        )

        # full_payment today
        if "full_payment" in accepts:
            payments = [(req.request_date, req.requested_amount)]
            sim = simulate_candidate(
                modified_events, req.request_date, profile.current_available_balance,
                min_balance, payments, horizon_days,
            )
            candidates.append(
                Candidate(
                    method="full_payment",
                    payments=tuple(payments),
                    changes=subset,
                    option_id=None,
                    total_paid=req.requested_amount,
                    total_reduction=total_reduction,
                    completes_by_deadline=req.request_date <= req.desired_completion_date,
                    tail_breach=sim.tail_breach,
                    feasible=_feasible(sim),
                )
            )

        # installment options
        if "installments" in accepts:
            for opt in option_schedules:
                if not opt.eligible or opt.payment_method != "installments":
                    continue
                sim = simulate_candidate(
                    modified_events, req.request_date, profile.current_available_balance,
                    min_balance, list(opt.payments), horizon_days,
                )
                last_date = opt.payments[-1][0]
                candidates.append(
                    Candidate(
                        method="installments",
                        payments=tuple(opt.payments),
                        changes=subset,
                        option_id=opt.payment_option_id,
                        total_paid=opt.total_payable_amount,
                        total_reduction=total_reduction,
                        completes_by_deadline=last_date <= req.desired_completion_date,
                        tail_breach=sim.tail_breach,
                        feasible=_feasible(sim),
                    )
                )

    # wait -- baseline only, no change crossing (see module docstring)
    if earliest_baseline is not None and "full_payment" in accepts:
        payments = [(earliest_baseline, req.requested_amount)]
        sim = simulate_candidate(
            canonical_events, req.request_date, profile.current_available_balance,
            min_balance, payments, horizon_days,
        )
        candidates.append(
            Candidate(
                method="wait",
                payments=tuple(payments),
                changes=(),
                option_id=None,
                total_paid=req.requested_amount,
                total_reduction=Decimal(0),
                completes_by_deadline=earliest_baseline <= req.desired_completion_date,
                tail_breach=sim.tail_breach,
                feasible=_feasible(sim),
            )
        )

    # partial_payment -- baseline only, exactly two payments (INV-04)
    if (
        req.allows_partial_payment
        and "partial_payment" in accepts
        and Decimal(0) < amt_safe_baseline < req.requested_amount
        and earliest_baseline is not None
        and earliest_baseline <= req.desired_completion_date
    ):
        remainder = req.requested_amount - amt_safe_baseline
        payments = [(req.request_date, amt_safe_baseline), (earliest_baseline, remainder)]
        sim = simulate_candidate(
            canonical_events, req.request_date, profile.current_available_balance,
            min_balance, payments, horizon_days,
        )
        candidates.append(
            Candidate(
                method="partial_payment",
                payments=tuple(payments),
                changes=(),
                option_id=None,
                total_paid=req.requested_amount,
                total_reduction=Decimal(0),
                completes_by_deadline=earliest_baseline <= req.desired_completion_date,
                tail_breach=sim.tail_breach,
                feasible=_feasible(sim),
            )
        )

    # not_recommended -- always available as the fallback
    candidates.append(
        Candidate(
            method="not_recommended",
            payments=(),
            changes=(),
            option_id=None,
            total_paid=Decimal(0),
            total_reduction=Decimal(0),
            completes_by_deadline=False,
            tail_breach=False,
            feasible=True,
        )
    )

    return candidates
