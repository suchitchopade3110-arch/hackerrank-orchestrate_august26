r"""Daily balance curve bal(t) over HORIZON (L3), built by expanding every
CanonicalEvent through recurrence.occurrences() and adding the resulting
deltas to current_available_balance. Essentials are ordinary outflows in
this curve -- there is no separate "cover essentials" rule (design.md \S7).

One horizon constant is used here and by candidate feasibility (engine.simulate);
there is no second window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from config import load_config
from ledger.canonical import CanonicalEvent
from ledger.recurrence import occurrences

HORIZON_DAYS = load_config().get("horizon", {}).get("days", 90)


@dataclass(frozen=True)
class Delta:
    date: date
    amount: Decimal  # signed: positive adds to balance, negative subtracts
    event_id: str
    category: str


@dataclass(frozen=True)
class BalanceCurve:
    request_date: date
    horizon_end: date
    starting_balance: Decimal
    deltas: tuple[Delta, ...]  # chronological
    balances: dict  # date -> Decimal, for every date in [request_date, horizon_end]

    def bal(self, on: date) -> Decimal:
        if on < self.request_date:
            raise ValueError(f"{on} is before request_date {self.request_date}")
        if on > self.horizon_end:
            on = self.horizon_end  # tail: last computed value carries forward
        return self.balances[min(on, self.horizon_end)] if on in self.balances else self._nearest(on)

    def _nearest(self, on: date) -> Decimal:
        # dates dict is dense (every day in window), so this only fires for
        # a caller passing a date outside the dense range by construction.
        keys = sorted(self.balances)
        prior = [k for k in keys if k <= on]
        return self.balances[prior[-1]] if prior else self.starting_balance


def build_deltas(
    canonical_events: list[CanonicalEvent], request_date: date, horizon_end: date
) -> list[Delta]:
    deltas: list[Delta] = []
    for ce in canonical_events:
        dates = occurrences(
            ce.anchor_date, ce.interval_kind, ce.interval_n, request_date, horizon_end
        )
        signed = ce.amount if ce.direction == "credit" else -ce.amount
        for d in dates:
            deltas.append(Delta(date=d, amount=signed, event_id=ce.event_id, category=ce.category))
    return sorted(deltas, key=lambda x: x.date)


def build_balance_curve(
    canonical_events: list[CanonicalEvent],
    request_date: date,
    starting_balance: Decimal,
    horizon_days: int = HORIZON_DAYS,
) -> BalanceCurve:
    horizon_end = request_date + timedelta(days=horizon_days)
    deltas = build_deltas(canonical_events, request_date, horizon_end)

    balances: dict[date, Decimal] = {}
    running = starting_balance
    by_date: dict[date, Decimal] = {}
    for d in deltas:
        by_date[d.date] = by_date.get(d.date, Decimal(0)) + d.amount

    cursor = request_date
    while cursor <= horizon_end:
        running += by_date.get(cursor, Decimal(0))
        balances[cursor] = running
        cursor += timedelta(days=1)

    return BalanceCurve(
        request_date=request_date,
        horizon_end=horizon_end,
        starting_balance=starting_balance,
        deltas=tuple(deltas),
        balances=balances,
    )
