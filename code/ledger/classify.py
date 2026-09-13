r"""Spending-change eligibility gate (roadmap doc \S0 finding 3 / config.yaml
spending_changes). Only recurring_flexible CanonicalEvents are candidates at
all; within those, `stop` and `reduce_to` each need their own two-key gate:

  stop_eligible    flexibility in {stoppable, reducible_or_stoppable}
                    AND category in expense_categories_user_is_willing_to_stop
                    AND category NOT in expense_categories_to_protect

  reduce_eligible   flexibility in {reducible, reducible_or_stoppable}
                    AND category in expense_categories_user_is_willing_to_reduce
                    AND category NOT in expense_categories_to_protect
"""

from __future__ import annotations

from io_layer.loader import Profile
from ledger.canonical import CanonicalEvent

STOPPABLE_FLEXIBILITY = {"stoppable", "reducible_or_stoppable"}
REDUCIBLE_FLEXIBILITY = {"reducible", "reducible_or_stoppable"}


def is_protected(event: CanonicalEvent, profile: Profile) -> bool:
    return event.category in profile.expense_categories_to_protect


def stop_eligible(event: CanonicalEvent, profile: Profile) -> bool:
    if event.kind != "recurring_flexible":
        return False
    if event.flexibility not in STOPPABLE_FLEXIBILITY:
        return False
    if event.category not in profile.expense_categories_willing_to_stop:
        return False
    if is_protected(event, profile):
        return False
    return True


def reduce_eligible(event: CanonicalEvent, profile: Profile) -> bool:
    if event.kind != "recurring_flexible":
        return False
    if event.flexibility not in REDUCIBLE_FLEXIBILITY:
        return False
    if event.category not in profile.expense_categories_willing_to_reduce:
        return False
    if is_protected(event, profile):
        return False
    return True


def eligible_flexible_events(
    canonical_events: list[CanonicalEvent], profile: Profile
) -> list[CanonicalEvent]:
    """Events eligible for at least one of stop/reduce."""
    return [
        e
        for e in canonical_events
        if e.kind == "recurring_flexible"
        and (stop_eligible(e, profile) or reduce_eligible(e, profile))
    ]
