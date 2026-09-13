r"""The injection boundary (design.md \S11). A message classifier can only
ever return this shape -- no field can express an output value, a payment
method, a status, or a policy change. `target_event_id` is validated
against the event IDs joined to that user; anything else is dropped before
it ever reaches the ledger.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AmendmentKind = Literal["cancel", "reschedule", "amend_amount", "confirm", "no_op"]


class Amendment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AmendmentKind
    # None for kind="no_op" -- a message with no confident tie to a
    # specific event has nothing to target. Never optional for any other
    # kind (enforced in perception/messages.py's gate, not here, so a
    # missing target on a real amendment is dropped rather than raising
    # and losing the whole batch).
    target_event_id: str | None = None
    new_amount: float | None = None
    new_date: str | None = None  # ISO date, YYYY-MM-DD
    message_id: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("new_date")
    @classmethod
    def _validate_date(cls, v: str | None) -> str | None:
        if v is None:
            return v
        import re

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError(f"new_date must be YYYY-MM-DD, got {v!r}")
        return v


class AmendmentBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amendments: list[Amendment] = Field(default_factory=list)


class ImageExtraction(BaseModel):
    r"""The image-extraction interface (stack.md \S3): one function
    returning this shape, provider order is a config list, not a code
    change."""

    model_config = ConfigDict(extra="forbid")

    amount: float
    currency: str
    date: str | None = None
    doc_type: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
