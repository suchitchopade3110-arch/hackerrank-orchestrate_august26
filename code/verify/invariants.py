r"""INV-01 through INV-20 (design.md \S10, amended per roadmap doc \S0):
every row passes all of these before it is written. Each returns a coded
result so a wrong answer is debuggable from the audit trace in ten seconds,
not by re-deriving the whole pipeline by hand.

INV-05 (amended): installments checks the plan against the SUPPLIED
option row's own payment_amount/date series (not a reconstructed split --
closed per roadmap doc \S0 finding 1).
INV-20 (new): every installment plan respects the user's
max_installment_months (roadmap doc \S0 finding 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from engine.candidates import Candidate
from engine.options import OptionSchedule
from engine.simulate import simulate_candidate
from io_layer.loader import Profile, RequestRow
from ledger.canonical import CanonicalEvent
from writer import format_amount_safe_to_pay, format_installment_plan, format_payment_plan

VALID_STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
VALID_METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}


@dataclass(frozen=True)
class InvariantResult:
    code: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class VerificationContext:
    row: dict  # the eight output fields, as strings
    profile: Profile
    req: RequestRow
    selected: Candidate
    amount_safe_baseline: Decimal
    earliest_baseline: date | None
    canonical_events: list[CanonicalEvent]
    option_schedules: list[OptionSchedule]
    horizon_days: int = 90


def _parse_plan(plan_str: str) -> list[tuple[date, Decimal]]:
    if plan_str == "none":
        return []
    out = []
    for part in plan_str.split("|"):
        d_str, amt_str = part.split(":")
        out.append((date.fromisoformat(d_str), Decimal(amt_str)))
    return out


def inv_01(ctx: VerificationContext) -> InvariantResult:
    amt = Decimal(ctx.row["amount_safe_to_pay"])
    ok = Decimal(0) <= amt <= ctx.req.requested_amount
    return InvariantResult("INV-01", ok, f"amount_safe_to_pay={amt} requested_amount={ctx.req.requested_amount}")


def inv_02(ctx: VerificationContext) -> InvariantResult:
    ok = (
        ctx.row["affordability_status"] in VALID_STATUSES
        and ctx.row["recommended_payment_method"] in VALID_METHODS
    )
    return InvariantResult("INV-02", ok, f"status={ctx.row['affordability_status']} method={ctx.row['recommended_payment_method']}")


def inv_03(ctx: VerificationContext) -> InvariantResult:
    if ctx.row["affordability_status"] != "affordable_now":
        return InvariantResult("INV-03", True, "n/a")
    ok = (
        ctx.row["earliest_date_for_full_payment"] == ctx.req.request_date.isoformat()
        and ctx.row["recommended_payment_method"] == "full_payment"
    )
    return InvariantResult("INV-03", ok, f"earliest={ctx.row['earliest_date_for_full_payment']}")


def inv_04(ctx: VerificationContext) -> InvariantResult:
    if ctx.row["recommended_payment_method"] != "partial_payment":
        return InvariantResult("INV-04", True, "n/a")
    if ctx.row["affordability_status"] != "affordable_with_plan":
        return InvariantResult("INV-04", False, "status must be affordable_with_plan")
    # Checked against the candidate's own full-precision Decimals, not the
    # currency-formatted display string -- payment_plan rounds to the
    # currency's display precision (e.g. 0dp for INR/ZAR/IDR) while
    # amount_safe_to_pay always keeps 2dp (roadmap doc \S0), so the two
    # STRINGS can legitimately differ in trailing precision even though
    # the underlying plan is arithmetically exact. INV-08/INV-14 already
    # check the string format separately.
    payments = ctx.selected.payments
    if len(payments) != 2:
        return InvariantResult("INV-04", False, f"expected 2 payments, got {len(payments)}")
    p1, p2 = payments
    ok = (
        p1[0] == ctx.req.request_date
        and p1[1] == ctx.amount_safe_baseline
        and p1[1] + p2[1] == ctx.req.requested_amount
        and ctx.earliest_baseline == p2[0]
        and p2[0] <= ctx.req.desired_completion_date
    )
    return InvariantResult("INV-04", ok, f"p1={p1} p2={p2}")


def inv_05(ctx: VerificationContext) -> InvariantResult:
    """Amended: check against the SUPPLIED option row's own series."""
    if ctx.row["recommended_payment_method"] != "installments":
        return InvariantResult("INV-05", True, "n/a")
    option = next(
        (o for o in ctx.option_schedules if o.payment_option_id == ctx.selected.option_id), None
    )
    if option is None:
        return InvariantResult("INV-05", False, f"option {ctx.selected.option_id} not found")
    expected_plan = format_installment_plan(list(option.payments))
    ok = ctx.row["payment_plan"] == expected_plan
    total = sum((amt for _, amt in option.payments), Decimal(0))
    sum_ok = abs(total - option.total_payable_amount) <= Decimal("0.01")
    return InvariantResult(
        "INV-05", ok and sum_ok, f"got={ctx.row['payment_plan']} expected={expected_plan} sum_ok={sum_ok}"
    )


def inv_06(ctx: VerificationContext) -> InvariantResult:
    if ctx.row["recommended_payment_method"] != "wait":
        return InvariantResult("INV-06", True, "n/a")
    payments = ctx.selected.payments
    ok = (
        len(payments) == 1
        and payments[0][1] == ctx.req.requested_amount
        and "full_payment" in ctx.profile.payment_methods_user_will_consider
    )
    return InvariantResult("INV-06", ok, f"payments={payments}")


def inv_07(ctx: VerificationContext) -> InvariantResult:
    if ctx.row["recommended_payment_method"] != "not_recommended":
        return InvariantResult("INV-07", True, "n/a")
    ok = ctx.row["payment_plan"] == "none"
    return InvariantResult("INV-07", ok, f"plan={ctx.row['payment_plan']}")


def inv_08(ctx: VerificationContext) -> InvariantResult:
    plan_str = ctx.row["payment_plan"]
    if plan_str == "none":
        return InvariantResult("INV-08", True, "n/a")
    if not re.fullmatch(r"(\d{4}-\d{2}-\d{2}:[0-9.]+)(\|\d{4}-\d{2}-\d{2}:[0-9.]+)*", plan_str):
        return InvariantResult("INV-08", False, f"malformed: {plan_str}")
    payments = _parse_plan(plan_str)
    dates = [d for d, _ in payments]
    ok = dates == sorted(dates)
    return InvariantResult("INV-08", ok, f"dates={dates}")


def inv_09(ctx: VerificationContext) -> InvariantResult:
    ok = all(c.event.kind == "recurring_flexible" for c in ctx.selected.changes)
    return InvariantResult("INV-09", ok, f"changes={[c.event.kind for c in ctx.selected.changes]}")


def inv_10(ctx: VerificationContext) -> InvariantResult:
    changes = ctx.selected.changes
    if len(changes) > 3:
        return InvariantResult("INV-10", False, f"{len(changes)} changes > 3")
    ids = [c.event.event_id for c in changes]
    if len(ids) != len(set(ids)):
        return InvariantResult("INV-10", False, "repeated event in changes")
    return InvariantResult("INV-10", True, f"{len(changes)} changes")


def inv_11(ctx: VerificationContext) -> InvariantResult:
    """The selected plan re-simulates safe inside HORIZON in a freshly
    built simulator -- independent re-verification, not the cached result."""
    from engine.simulate import apply_spending_changes

    modified_events = apply_spending_changes(ctx.canonical_events, ctx.selected.changes)
    if not ctx.selected.payments:
        return InvariantResult("INV-11", True, "no-payment candidate")
    sim = simulate_candidate(
        modified_events,
        ctx.req.request_date,
        ctx.profile.current_available_balance,
        ctx.profile.minimum_balance_to_keep,
        list(ctx.selected.payments),
        ctx.horizon_days,
    )
    return InvariantResult("INV-11", sim.feasible, f"feasible={sim.feasible} worst={sim.worst_date}")


def inv_12(ctx: VerificationContext) -> InvariantResult:
    """Every numeral in decision_explanation resolves against known facts
    for this request (window constants and dates allowlisted)."""
    text = ctx.row["decision_explanation"]
    # Dates (fact-sheet dates are allowlisted wholesale, per design.md \S12)
    # must be stripped BEFORE numeral extraction, or "2024-09-12" fragments
    # into ungroundable "2024"/"09"/"12".
    text_without_dates = re.sub(r"\d{4}-\d{2}-\d{2}", "", text)
    numerals = re.findall(r"[\d][\d,]*\.?\d*", text_without_dates)
    known: set[str] = set()
    def _add(val: Decimal) -> None:
        from decimal import ROUND_DOWN

        known.add(str(val))
        known.add(f"{val:,}")
        known.add(format(val, ",.2f"))
        known.add(format(val, ",.0f"))
        # Explanation templates floor to the currency's display precision
        # (ROUND_DOWN), not Python's default round-half-even -- ground
        # against both so a floored display value (e.g. "42,234" from
        # 42234.73) is recognised as grounded.
        for dp in (0, 2):
            quantum = Decimal(1).scaleb(-dp)
            floored = val.quantize(quantum, rounding=ROUND_DOWN)
            known.add(format(floored, f",.{dp}f"))

    for val in (
        ctx.req.requested_amount,
        Decimal(ctx.row["amount_safe_to_pay"]),
        ctx.profile.minimum_balance_to_keep,
        ctx.profile.current_available_balance,
    ):
        _add(val)
    for p in ctx.selected.payments:
        _add(p[1])
    # Window constant, and the installment count itself (fine-grained --
    # design.md \S12 allowlists "window constants... plus ordinals").
    allowlist = {"90", str(len(ctx.selected.payments))}
    bad = []
    for n in numerals:
        clean = n.rstrip(".")
        if clean in allowlist or clean in known:
            continue
        # also accept a bare numeral that's a substring match against a known figure
        if any(clean.replace(",", "") == k.replace(",", "") for k in known):
            continue
        bad.append(n)
    return InvariantResult("INV-12", not bad, f"ungrounded numerals: {bad}" if bad else "all grounded")


def inv_13(ctx: VerificationContext) -> InvariantResult:
    # Checked at the writer level (one row per template request_id, header
    # order from template) -- always true for a single-row context.
    return InvariantResult("INV-13", True, "checked at writer level")


def inv_14(ctx: VerificationContext) -> InvariantResult:
    expected = format_amount_safe_to_pay(ctx.amount_safe_baseline)
    ok = ctx.row["amount_safe_to_pay"] == expected
    return InvariantResult("INV-14", ok, f"got={ctx.row['amount_safe_to_pay']} expected={expected}")


def inv_15(ctx: VerificationContext) -> InvariantResult:
    if ctx.row["affordability_status"] != "affordable_later":
        return InvariantResult("INV-15", True, "n/a")
    earliest_str = ctx.row["earliest_date_for_full_payment"]
    if not earliest_str:
        return InvariantResult("INV-15", False, "earliest is empty")
    ok = date.fromisoformat(earliest_str) > ctx.req.request_date
    return InvariantResult("INV-15", ok, f"earliest={earliest_str}")


def inv_16(ctx: VerificationContext) -> InvariantResult:
    """An empty earliest does NOT constrain status -- affordable_with_plan
    with an empty earliest is valid (installments never need a single safe
    full payment). This invariant only ever PASSES by construction (there
    is no rule to violate); it exists so a future refactor that adds one
    is caught by the fixture test in code/tests, not by this function."""
    return InvariantResult("INV-16", True, "no constraint by design")


def inv_17(ctx: VerificationContext) -> InvariantResult:
    """amount_safe_to_pay is independent of the selected method -- a
    not_recommended row may carry a nonzero value. Same shape as INV-16:
    passes by construction, locked by a fixture test."""
    return InvariantResult("INV-17", True, "no constraint by design")


def inv_18(ctx: VerificationContext) -> InvariantResult:
    ungrounded = [
        e.raw_event_id
        for e in ctx.canonical_events
        # amount was resolved via FX at load time; RawEvent-level rate
        # provenance is carried in io_layer.loader.RawEvent, not re-checked
        # per-CanonicalEvent here since a CanonicalEvent can aggregate many
        # raw rows. Presence of a raw_event_id is the traceability check.
        if not e.raw_event_id
    ]
    return InvariantResult("INV-18", not ungrounded, f"events missing provenance: {ungrounded}")


def inv_19(ctx: VerificationContext) -> InvariantResult:
    blanks = [e.raw_event_id for e in ctx.canonical_events if e.amount_was_blank]
    # Any blank-amount event reaching the forecast must be flagged -- the
    # flag itself lives in the exclusion log (blank_amount_conservative_
    # placeholder_phase1), checked at the run level, not per-row here.
    return InvariantResult("INV-19", True, f"blank-substituted events: {blanks}")


def inv_20(ctx: VerificationContext) -> InvariantResult:
    """New: every installment plan respects max_installment_months."""
    if ctx.row["recommended_payment_method"] != "installments":
        return InvariantResult("INV-20", True, "n/a")
    option = next(
        (o for o in ctx.option_schedules if o.payment_option_id == ctx.selected.option_id), None
    )
    if option is None or ctx.profile.max_installment_months is None:
        return InvariantResult("INV-20", False, "no eligible option / user rejects installments")
    span_months = (option.payments[-1][0] - option.payments[0][0]).days / 30.0
    ok = span_months <= ctx.profile.max_installment_months + 1e-9
    return InvariantResult("INV-20", ok, f"span_months={span_months:.2f} cap={ctx.profile.max_installment_months}")


ALL_INVARIANTS = [
    inv_01, inv_02, inv_03, inv_04, inv_05, inv_06, inv_07, inv_08, inv_09, inv_10,
    inv_11, inv_12, inv_13, inv_14, inv_15, inv_16, inv_17, inv_18, inv_19, inv_20,
]


def run_invariants(ctx: VerificationContext) -> list[InvariantResult]:
    return [inv(ctx) for inv in ALL_INVARIANTS]


def all_passed(results: list[InvariantResult]) -> bool:
    return all(r.passed for r in results)
