r"""Exclusions and linked_event_id chain resolution (L2 steps 2-5).

Base exclusions (each logged with a reason code):
  pending_credit           status == pending AND direction == credit
  failed                   status == failed
  cancelled                status == cancelled
  unrealized_investment    status == unrealized
  non_cash                 direction == non_cash

Pending *debits* survive and are reserved against the balance (FR-9b) -- a
generic "drop anything pending" filter would silently inflate every balance
in the dataset, so this is asserted by ledger/tests rather than assumed.

Linked chains (linked_event_id points at an EARLIER event in the same
lifecycle): a refund nets against the debit it links to; anything else is
treated as the same economic fact recorded twice (e.g. scheduled -> settled)
and collapsed to whichever member has the best status, per the comparator
in \S6.3 of AGENTS.md (explicit cancellation/settlement/amendment first,
then newer-same-source, then settled-over-estimate, then the safer read).
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from io_layer.loader import RawEvent

STATUS_RANK = {
    "settled": 3,
    "scheduled": 2,
    "pending": 1,
}


def apply_base_exclusions(
    events: list[RawEvent], exclusion_log: list[dict]
) -> list[RawEvent]:
    survivors = []
    for e in events:
        reason = None
        if e.status == "pending" and e.direction == "credit":
            reason = "pending_credit"
        elif e.status == "failed":
            reason = "failed"
        elif e.status == "cancelled":
            reason = "cancelled"
        elif e.status == "unrealized":
            reason = "unrealized_investment"
        elif e.direction == "non_cash":
            reason = "non_cash"

        if reason:
            exclusion_log.append({"event_id": e.event_id, "reason": reason})
        else:
            survivors.append(e)
    return survivors


def resolve_linked_chains(
    events: list[RawEvent], exclusion_log: list[dict]
) -> list[RawEvent]:
    by_id = {e.event_id: e for e in events}
    excluded_ids: set[str] = set()
    amount_override: dict[str, Decimal] = {}

    # children[parent_id] = [child events that link to parent_id]
    children: dict[str, list[RawEvent]] = {}
    for e in events:
        if e.linked_event_id and e.linked_event_id in by_id:
            children.setdefault(e.linked_event_id, []).append(e)

    for parent_id, kids in children.items():
        parent = by_id[parent_id]

        refunds = [k for k in kids if k.event_type == "refund"]
        others = [k for k in kids if k.event_type != "refund"]

        for refund in refunds:
            base = amount_override.get(parent_id, parent.amount_home or Decimal(0))
            refund_amt = refund.amount_home or Decimal(0)
            amount_override[parent_id] = max(Decimal(0), base - refund_amt)
            excluded_ids.add(refund.event_id)
            exclusion_log.append(
                {
                    "event_id": refund.event_id,
                    "reason": "netted_refund_into_linked_debit",
                    "detail": parent_id,
                }
            )

        if others:
            chain = [parent] + others
            chain_survivor = max(
                chain,
                key=lambda ev: (
                    STATUS_RANK.get(ev.status, 0),
                    ev.settlement_date or ev.event_date,
                ),
            )
            for ev in chain:
                if ev.event_id != chain_survivor.event_id:
                    excluded_ids.add(ev.event_id)
                    exclusion_log.append(
                        {
                            "event_id": ev.event_id,
                            "reason": "superseded_by_linked_event_id",
                            "detail": chain_survivor.event_id,
                        }
                    )

    out = []
    for e in events:
        if e.event_id in excluded_ids:
            continue
        if e.event_id in amount_override:
            out.append(dataclasses.replace(e, amount_home=amount_override[e.event_id]))
        else:
            out.append(e)
    return out
