"""Dated FX normalisation into home_currency (FR-2, FR-9c).

Resolution order for a (from_currency, to_currency, on_date) lookup:
  1. Exact rate_date match for the stated pair.
  2. Most recent PRIOR rate_date for the stated pair.
  3. Same, but for the explicit inverse pair (rate -> 1/rate).
  4. Neither direction exists at any date -> raise. Never default to 1.0.

exchange_rates.csv only carries USD/EUR as from_currency, so INR/IDR/ZAR
amounts are already in home_currency terms when home_currency == currency;
cross-currency conversions route through whichever direction the table
actually supplies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RATES_PATH = REPO_ROOT / "dataset" / "exchange_rates.csv"


class NoRateAvailable(RuntimeError):
    """Raised when neither direction of a currency pair has any rate on or
    before the requested date. Never silently defaulted to 1.0."""


@dataclass(frozen=True)
class RateLookup:
    rate: Decimal
    rate_date: date
    inverted: bool
    source_from: str
    source_to: str


class FxTable:
    def __init__(self, rows: list[dict]):
        # rows: from_currency, to_currency, rate_date (date), rate (Decimal)
        self._rows = sorted(rows, key=lambda r: r["rate_date"])

    @classmethod
    def load(cls, path: Path = RATES_PATH) -> "FxTable":
        df = pd.read_csv(path, dtype=str)
        rows = []
        for _, row in df.iterrows():
            rows.append(
                {
                    "from_currency": row["from_currency"],
                    "to_currency": row["to_currency"],
                    "rate_date": pd.to_datetime(row["rate_date"]).date(),
                    "rate": Decimal(str(row["rate"])),
                }
            )
        return cls(rows)

    def _find(self, frm: str, to: str, on: date) -> RateLookup | None:
        candidates = [
            r for r in self._rows
            if r["from_currency"] == frm and r["to_currency"] == to and r["rate_date"] <= on
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda r: r["rate_date"])
        return RateLookup(
            rate=best["rate"],
            rate_date=best["rate_date"],
            inverted=False,
            source_from=frm,
            source_to=to,
        )

    def lookup(self, from_currency: str, to_currency: str, on: date) -> RateLookup:
        if from_currency == to_currency:
            return RateLookup(
                rate=Decimal(1),
                rate_date=on,
                inverted=False,
                source_from=from_currency,
                source_to=to_currency,
            )

        direct = self._find(from_currency, to_currency, on)
        if direct is not None:
            return direct

        inverse = self._find(to_currency, from_currency, on)
        if inverse is not None:
            return RateLookup(
                rate=Decimal(1) / inverse.rate,
                rate_date=inverse.rate_date,
                inverted=True,
                source_from=to_currency,
                source_to=from_currency,
            )

        raise NoRateAvailable(
            f"No rate available for {from_currency}->{to_currency} on or before {on} "
            f"in either direction."
        )

    def convert(
        self, amount: Decimal, from_currency: str, to_currency: str, on: date
    ) -> tuple[Decimal, RateLookup]:
        lookup = self.lookup(from_currency, to_currency, on)
        return amount * lookup.rate, lookup
