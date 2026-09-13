r"""Load the nine dataset/*.csv files into typed, home-currency-normalised
tables (L0). Decimal for money, date for calendar fields. Foreign-currency
event amounts are converted via fx.FxTable, matched on settlement_date
(AGENTS.md \S6.1); original amount/currency/rate/rate_date are kept for the
audit trace (INV-18).

requested_amount and request_payment_options amounts are already expressed
in the user's home_currency (confirmed against sample_requests.csv: request
text quotes the same currency as the profile's home_currency), so those are
loaded as Decimal with no FX step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from io_layer.fx import FxTable, NoRateAvailable

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"


def _dec(val) -> Decimal | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return Decimal(s)


def _d(val) -> date | None:
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return pd.to_datetime(s).date()


def _pipe_set(val) -> frozenset[str]:
    if val is None:
        return frozenset()
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return frozenset()
    return frozenset(p.strip() for p in s.split("|") if p.strip())


@dataclass(frozen=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: frozenset[str]
    expense_categories_to_protect: frozenset[str]
    expense_categories_willing_to_reduce: frozenset[str]
    expense_categories_willing_to_stop: frozenset[str]
    payment_methods_user_will_consider: frozenset[str]
    max_installment_months: int | None  # None == user will not consider installments


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str  # debit | credit | non_cash
    amount_home: Decimal | None  # None only if amount is blank AND unresolved
    amount_original: Decimal | None
    currency_original: str
    fx_rate: Decimal | None
    fx_rate_date: date | None
    fx_inverted: bool
    event_date: date
    settlement_date: date | None
    status: str  # settled | pending | scheduled | cancelled | failed | unrealized
    linked_event_id: str | None
    flexibility: str  # fixed | reducible | stoppable | reducible_or_stoppable
    minimum_allowed_amount: Decimal | None
    amount_was_blank: bool = False


@dataclass(frozen=True)
class RequestRow:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str  # full_payment | installments
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: str
    message_text: str


@dataclass(frozen=True)
class ImageRef:
    image_id: str
    user_id: str
    request_id: str
    related_event_id: str
    path: Path


@dataclass
class RawTables:
    profiles: dict[str, Profile]
    events: list[RawEvent]
    requests: list[RequestRow]
    payment_options: list[PaymentOption]
    messages: list[Message]
    images: list[ImageRef]
    exclusion_log: list[dict] = field(default_factory=list)


def load_profiles(path: Path = DATASET_DIR / "financial_profiles.csv") -> dict[str, Profile]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    out: dict[str, Profile] = {}
    for _, row in df.iterrows():
        months_raw = row.get("max_installment_months")
        max_months = None
        if months_raw and str(months_raw).strip() and str(months_raw).lower() != "nan":
            max_months = int(float(months_raw))
        out[row["user_id"]] = Profile(
            user_id=row["user_id"],
            home_currency=row["home_currency"],
            current_available_balance=_dec(row["current_available_balance"]),
            minimum_balance_to_keep=_dec(row["minimum_balance_to_keep"]),
            financial_priorities=_pipe_set(row.get("financial_priorities")),
            expense_categories_to_protect=_pipe_set(row.get("expense_categories_to_protect")),
            expense_categories_willing_to_reduce=_pipe_set(
                row.get("expense_categories_user_is_willing_to_reduce")
            ),
            expense_categories_willing_to_stop=_pipe_set(
                row.get("expense_categories_user_is_willing_to_stop")
            ),
            payment_methods_user_will_consider=_pipe_set(
                row.get("payment_methods_user_will_consider")
            ),
            max_installment_months=max_months,
        )
    return out


def load_events(
    path: Path = DATASET_DIR / "financial_events.csv",
    fx: FxTable | None = None,
    profiles: dict[str, Profile] | None = None,
    exclusion_log: list[dict] | None = None,
) -> list[RawEvent]:
    """Load events, converting amount to each user's home_currency.

    Blank amounts are NOT filled here (never treated as zero) -- that is the
    perception ladder's job (ledger/canonical.py + perception/vlm.py). This
    loader records amount_home=None and amount_was_blank=True so downstream
    layers can apply the ladder and log accordingly (INV-19).
    """
    if fx is None:
        fx = FxTable.load()
    if profiles is None:
        profiles = load_profiles()
    if exclusion_log is None:
        exclusion_log = []

    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    out: list[RawEvent] = []
    for _, row in df.iterrows():
        user_id = row["user_id"]
        profile = profiles.get(user_id)
        home_ccy = profile.home_currency if profile else row["currency"]

        event_date = _d(row["event_date"])
        settlement_date = _d(row.get("settlement_date")) or event_date
        currency = row["currency"]
        amount_original = _dec(row.get("amount"))
        amount_was_blank = amount_original is None

        amount_home = None
        fx_rate = None
        fx_rate_date = None
        fx_inverted = False

        if amount_original is not None:
            try:
                amount_home, lookup = fx.convert(
                    amount_original, currency, home_ccy, settlement_date or event_date
                )
                fx_rate = lookup.rate
                fx_rate_date = lookup.rate_date
                fx_inverted = lookup.inverted
            except NoRateAvailable:
                exclusion_log.append(
                    {
                        "event_id": row["event_id"],
                        "reason": "no_fx_rate",
                        "detail": f"{currency}->{home_ccy} on {settlement_date}",
                    }
                )
                raise

        out.append(
            RawEvent(
                event_id=row["event_id"],
                user_id=user_id,
                event_type=row["event_type"],
                description=row["description"],
                category=row["category"],
                direction=row["direction"],
                amount_home=amount_home,
                amount_original=amount_original,
                currency_original=currency,
                fx_rate=fx_rate,
                fx_rate_date=fx_rate_date,
                fx_inverted=fx_inverted,
                event_date=event_date,
                settlement_date=settlement_date,
                status=row["status"],
                linked_event_id=row.get("linked_event_id") or None,
                flexibility=row["flexibility"],
                minimum_allowed_amount=_dec(row.get("minimum_allowed_amount")),
                amount_was_blank=amount_was_blank,
            )
        )
    return out


def load_requests(path: Path = DATASET_DIR / "requests.csv") -> list[RequestRow]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    out = []
    for _, row in df.iterrows():
        out.append(
            RequestRow(
                request_id=row["request_id"],
                user_id=row["user_id"],
                request_date=_d(row["request_date"]),
                request_type=row["request_type"],
                requested_amount=_dec(row["requested_amount"]),
                desired_completion_date=_d(row["desired_completion_date"]),
                allows_partial_payment=str(row["allows_partial_payment"]).strip().lower() == "true",
                request_text=row["request_text"],
            )
        )
    return out


def load_payment_options(
    path: Path = DATASET_DIR / "request_payment_options.csv"
) -> list[PaymentOption]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    out = []
    for _, row in df.iterrows():
        freq = row.get("payment_frequency_days")
        freq_int = int(float(freq)) if freq and str(freq).lower() != "nan" else None
        out.append(
            PaymentOption(
                payment_option_id=row["payment_option_id"],
                request_id=row["request_id"],
                payment_method=row["payment_method"],
                payment_amount=_dec(row["payment_amount"]),
                number_of_payments=int(float(row["number_of_payments"])),
                first_payment_date=_d(row["first_payment_date"]),
                payment_frequency_days=freq_int,
                financing_fee=_dec(row["financing_fee"]),
                total_payable_amount=_dec(row["total_payable_amount"]),
            )
        )
    return out


def load_messages(path: Path = DATASET_DIR / "messages.csv") -> list[Message]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    out = []
    for _, row in df.iterrows():
        out.append(
            Message(
                message_id=row["message_id"],
                user_id=row["user_id"],
                request_id=row.get("request_id") or None,
                related_event_id=row.get("related_event_id") or None,
                sent_at=pd.to_datetime(row["sent_at"]).to_pydatetime(),
                source_type=row["source_type"],
                message_text=row["message_text"],
            )
        )
    return out


def load_images(path: Path = DATASET_DIR / "images.csv") -> list[ImageRef]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    media_dir = DATASET_DIR / "media" / "images"
    out = []
    for _, row in df.iterrows():
        out.append(
            ImageRef(
                image_id=row["image_id"],
                user_id=row["user_id"],
                request_id=row["request_id"],
                related_event_id=row["related_event_id"],
                path=media_dir / f"{row['image_id']}.png",
            )
        )
    return out


def load_all() -> RawTables:
    exclusion_log: list[dict] = []
    fx = FxTable.load()
    profiles = load_profiles()
    events = load_events(fx=fx, profiles=profiles, exclusion_log=exclusion_log)
    return RawTables(
        profiles=profiles,
        events=events,
        requests=load_requests(),
        payment_options=load_payment_options(),
        messages=load_messages(),
        images=load_images(),
        exclusion_log=exclusion_log,
    )
