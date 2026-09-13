"""Apply accepted Amendments (perception/messages.py) to RawEvents. Can
only ever act on an event_id already in the allowlist passed to the
classifier -- this is the last stop before the ledger, not a second
injection surface. cancel/reschedule/amend_amount mutate the ONE named
event; confirm and no_op never change anything.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from io_layer.loader import RawEvent
from perception.schemas import Amendment


def apply_amendments(events: list[RawEvent], amendments: list[Amendment]) -> list[RawEvent]:
    by_id = {e.event_id: e for e in events}

    for a in amendments:
        target = by_id.get(a.target_event_id)
        if target is None:
            continue  # not in this event list -- stay safe, do nothing

        if a.kind == "cancel":
            by_id[a.target_event_id] = dataclasses.replace(target, status="cancelled")
        elif a.kind == "reschedule" and a.new_date:
            by_id[a.target_event_id] = dataclasses.replace(
                target, settlement_date=date.fromisoformat(a.new_date)
            )
        elif a.kind == "amend_amount" and a.new_amount is not None:
            by_id[a.target_event_id] = dataclasses.replace(
                target, amount_home=Decimal(str(a.new_amount)), amount_was_blank=False
            )
        # "confirm": the message confirms the record as-is -- no change.

    return list(by_id.values())
