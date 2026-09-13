r"""CanonicalEvent construction (L2 steps 1, 6, 7): lifecycle collapse via
ledger.precedence, blank-amount placeholder (real VLM resolution lands in
Phase 8 / perception/vlm.py), and classification into the four kinds with
an explicit anchor_date + interval_kind (never inferred at forecast time).

Built per (user_id, request_date) -- not per user_id alone -- because
"last observed occurrence" and "already reflected in current balance" both
depend on where request_date falls relative to each event's settlement_date
(design.md \S4: UserFinancialState is cached on that pair for exactly this
reason).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as dataclasses_replace
from datetime import date
from decimal import Decimal
from typing import Literal

from io_layer.loader import Profile, RawEvent
from ledger.precedence import apply_base_exclusions, resolve_linked_chains
from ledger.recurrence import MIN_OCCURRENCES_FOR_CADENCE, IntervalKind, detect_cadence

AmountRule = Literal["last", "mean", "median", "max", "p75", "mean_plus_k_stdev"]
OccurrenceRule = Literal[
    "observed_cadence", "weekly_anchored", "category_level_cadence", "daily_drip"
]

DEFAULT_VARIABLE_EXPENSE_CATEGORIES: tuple[str, ...] = ("groceries", "transport", "dining")
MIN_TOTAL_FOR_CATEGORY_AGGREGATE = 5


def _amount_from_rule(sorted_amounts: list[Decimal], rule: str, k_stdev: Decimal) -> Decimal:
    """The conservative-amount choice for a projected occurrence, evaluated
    over a category or series' full historical amount list (date order not
    required except for `last`, which the caller must have sorted)."""
    if not sorted_amounts:
        return Decimal(0)
    if rule == "last":
        return sorted_amounts[-1]
    if rule == "max":
        return max(sorted_amounts)
    if rule == "mean":
        return sum(sorted_amounts) / len(sorted_amounts)
    if rule == "median":
        s = sorted(sorted_amounts)
        n = len(s)
        mid = n // 2
        return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2
    if rule == "p75":
        s = sorted(sorted_amounts)
        n = len(s)
        idx = -(-(3 * n) // 4) - 1  # ceil(0.75n) - 1, integer-only arithmetic
        idx = max(0, min(idx, n - 1))
        return s[idx]
    if rule == "mean_plus_k_stdev":
        n = len(sorted_amounts)
        mean = sum(sorted_amounts) / n
        if n < 2:
            return mean
        variance = sum((a - mean) ** 2 for a in sorted_amounts) / (n - 1)
        return mean + k_stdev * variance.sqrt()
    raise ValueError(f"unknown amount_rule: {rule!r}")


def _occurrence_plan_from_rule(
    dates: list[date], rule: str
) -> tuple[date, IntervalKind, int] | None:
    """Returns (anchor_date, interval_kind, interval_n) for a category-level
    aggregate projection, or None if the rule finds no basis to project
    (category_level_cadence with no detectable cadence -- never invented).
    `observed_cadence` is handled entirely by the per-description loop in
    build_canonical_events and never reaches here."""
    if not dates:
        return None
    ordered = sorted(dates)
    anchor = ordered[-1]
    if rule == "weekly_anchored":
        return anchor, "days", 7
    if rule == "category_level_cadence":
        cadence = detect_cadence(ordered)
        if cadence is None:
            return None
        interval_kind, interval_n = cadence
        return anchor, interval_kind, interval_n
    if rule == "observed_cadence":
        return None
    raise ValueError(f"unknown occurrence_rule: {rule!r}")

Kind = Literal[
    "recurring_essential", "recurring_flexible", "one_time_confirmed", "income_confirmed"
]

INCOME_EVENT_TYPES = {"income"}


@dataclass(frozen=True)
class CanonicalEvent:
    event_id: str  # provenance: source event_id (anchor for a series)
    raw_event_id: str  # the actual financial_events.csv event_id to cite in output
    user_id: str
    kind: Kind
    category: str
    description: str
    direction: str  # debit | credit
    amount: Decimal  # home-currency magnitude
    flexible: bool
    flexibility: str
    minimum_allowed_amount: Decimal | None
    anchor_date: date
    interval_kind: IntervalKind
    interval_n: int | None
    provenance: tuple[str, ...]
    amount_was_blank: bool = False


def _series_key(e: RawEvent) -> tuple[str, str]:
    return (e.category, e.description)


def _conservative_blank_placeholder(
    blank_event: RawEvent, same_user_events: list[RawEvent]
) -> Decimal:
    r"""Ladder step 3 (design.md \S11): highest amount among the user's
    comparable (same category) settled events. Deliberately conservative,
    never neutral, because zero is forbidden and an underestimate inflates
    every downstream amount. Superseded by VLM extraction in Phase 8."""
    comparable = [
        e.amount_home
        for e in same_user_events
        if e.category == blank_event.category
        and e.amount_home is not None
        and e.event_id != blank_event.event_id
    ]
    if comparable:
        return max(comparable)
    all_amounts = [e.amount_home for e in same_user_events if e.amount_home is not None]
    return max(all_amounts) if all_amounts else Decimal(0)


def build_canonical_events(
    user_events: list[RawEvent],
    profile: Profile,
    request_date: date,
    exclusion_log: list[dict],
    income_mode: str = "recurring_projected",
    resolve_blank_amount=None,
    variable_expense_categories: tuple[str, ...] = DEFAULT_VARIABLE_EXPENSE_CATEGORIES,
    variable_expense_model: dict[str, dict] | None = None,
    k_stdev: float = 1.0,
    daily_drip_fraction: float = 0.15,
) -> list[CanonicalEvent]:
    r"""resolve_blank_amount: optional callable(event, same_user_events) ->
    (Decimal|None, source_label). Tried first (design.md \S11 ladder steps
    1-2, perception/vlm.py); falls through to the conservative placeholder
    (step 3, INV-19) when it returns None or isn't supplied.

    variable_expense_model: {category: {"amount_rule": ..., "occurrence_rule":
    ...}}, per-category, for the categories in variable_expense_categories
    (groceries/transport/dining by default -- real recurring essential
    spend that rarely forms a clean per-description cadence). Swept by
    code/evaluation/fit.py; see evaluation/ablations.md. Missing categories
    default to {"amount_rule": "max", "occurrence_rule": "observed_cadence"},
    which reproduces the plain per-description detection below with no
    category-level aggregate at all (the original, pre-calibration
    behaviour)."""
    var_cats = set(variable_expense_categories)
    var_model = variable_expense_model or {}
    k_stdev_dec = Decimal(str(k_stdev))

    def _rule_for(category: str) -> dict:
        return var_model.get(category, {"amount_rule": "max", "occurrence_rule": "observed_cadence"})
    survivors = apply_base_exclusions(user_events, exclusion_log)
    survivors = resolve_linked_chains(survivors, exclusion_log)

    resolved: list[RawEvent] = []
    for e in survivors:
        if e.amount_home is None:
            import dataclasses as _dc

            amount = None
            source = None
            if resolve_blank_amount is not None:
                amount, source = resolve_blank_amount(e, survivors)

            if amount is not None:
                exclusion_log.append(
                    {"event_id": e.event_id, "reason": source or "vlm_extraction", "detail": str(amount)}
                )
            else:
                amount = _conservative_blank_placeholder(e, survivors)
                exclusion_log.append(
                    {
                        "event_id": e.event_id,
                        "reason": "blank_amount_conservative_placeholder",
                        "detail": str(amount),
                    }
                )
            resolved.append(_dc.replace(e, amount_home=amount))
        else:
            resolved.append(e)

    past = [e for e in resolved if (e.settlement_date or e.event_date) <= request_date]
    future = [e for e in resolved if (e.settlement_date or e.event_date) > request_date]

    canonical: list[CanonicalEvent] = []

    # A user's income is rarely one blob: "Base salary" and "Performance
    # commission" are two different series with different cadences (a
    # single mixed group's gaps are irregular -> no cadence ever detected).
    # So income gets the exact same per-(category, description) cadence
    # detection as every other category, just with kind=income_confirmed
    # and direction=credit -- only a description with its OWN clean
    # cadence ever gets projected forward; a variable/irregular bonus or
    # commission never does, matching "detect recurrence only when history
    # supports it" and design.md's "cadence observed in that user's own
    # historical salary rows" (a specific series, not the income total).
    groups: dict[tuple[str, str], list[RawEvent]] = {}
    for e in past:
        groups.setdefault(_series_key(e), []).append(e)

    recurring_group_keys: set[tuple[str, str]] = set()

    for key, members in groups.items():
        category = key[0]
        if category in var_cats and _rule_for(category)["occurrence_rule"] != "observed_cadence":
            # This category's occurrence_rule replaces per-description
            # detection with one category-level aggregate below -- skip it
            # here so it isn't double-projected.
            continue

        members_sorted = sorted(members, key=lambda e: e.settlement_date or e.event_date)
        dates = [e.settlement_date or e.event_date for e in members_sorted]
        cadence = detect_cadence(dates)
        anchor_event = members_sorted[-1]
        is_income = anchor_event.event_type in INCOME_EVENT_TYPES

        if is_income and income_mode == "confirmed_only":
            # Strict reading: salary lands once, on its settlement date --
            # no cadence projection even if history would support one.
            continue

        if cadence:
            interval_kind, interval_n = cadence
            recurring_group_keys.add(key)

            if is_income:
                kind = "income_confirmed"
                flexible = False
                # Conservative direction for income is the OPPOSITE of an
                # expense: use the last observed amount, never a historical
                # maximum, so projected income is never overstated.
                amount = anchor_event.amount_home
            else:
                flexible = anchor_event.flexibility != "fixed"
                kind = "recurring_flexible" if flexible else "recurring_essential"
                if category in var_cats:
                    # Configurable conservative-amount choice (fit.py grid).
                    amount = _amount_from_rule(
                        [e.amount_home for e in members_sorted], _rule_for(category)["amount_rule"], k_stdev_dec
                    )
                else:
                    # Fixed bills (rent, utilities, ...): forecast a
                    # variable-amount recurring bill conservatively
                    # (AGENTS.md \S6.3) -- the highest historical amount in
                    # the series, not just the last occurrence.
                    amount = max(e.amount_home for e in members_sorted)

            canonical.append(
                CanonicalEvent(
                    event_id=f"{anchor_event.event_id}_series",
                    raw_event_id=anchor_event.event_id,
                    user_id=profile.user_id,
                    kind=kind,
                    category=anchor_event.category,
                    description=anchor_event.description,
                    direction=anchor_event.direction,
                    amount=amount,
                    flexible=flexible,
                    flexibility=anchor_event.flexibility,
                    minimum_allowed_amount=anchor_event.minimum_allowed_amount,
                    anchor_date=anchor_event.settlement_date or anchor_event.event_date,
                    interval_kind=interval_kind,
                    interval_n=interval_n,
                    provenance=tuple(e.event_id for e in members_sorted),
                    amount_was_blank=anchor_event.amount_was_blank,
                )
            )
        # else: fewer than MIN_OCCURRENCES_FOR_CADENCE occurrences, or an
        # irregular gap -- not enough history to call this line item
        # recurring at all (design principle: detect recurrence only when
        # history supports it). Already baked into current_available_balance
        # if in the past; does not project forward.

    # groceries/transport/dining are real recurring essential spend (500+
    # raw rows each across the sample users) but almost never form a clean
    # per-description cadence, so per-description detection above under-
    # represents them for any category configured with an occurrence_rule
    # other than "observed_cadence". This builds ONE category-wide
    # aggregate event instead, using the configured amount_rule /
    # occurrence_rule (code/evaluation/fit.py grid-searches this
    # combination against the 25 samples; see evaluation/ablations.md).
    for category in var_cats:
        rule_cfg = _rule_for(category)
        occurrence_rule = rule_cfg["occurrence_rule"]
        if occurrence_rule == "observed_cadence":
            continue  # already handled per-description above

        cat_events = [e for e in past if e.category == category and e.direction == "debit"]
        if len(cat_events) < MIN_TOTAL_FOR_CATEGORY_AGGREGATE:
            continue
        cat_events_sorted = sorted(cat_events, key=lambda e: e.settlement_date or e.event_date)
        dates = [e.settlement_date or e.event_date for e in cat_events_sorted]

        if occurrence_rule == "daily_drip":
            # A smoothed daily rate, not an amount_rule pick: total
            # historical spend over its observed span, scaled by
            # daily_drip_fraction (evaluation/ablations.md \S9/\S12 --
            # the mechanism this closed 3-rule grid's own winner scored
            # worse than; reinstated as a fourth rule on review).
            span_days = max(1, (request_date - dates[0]).days)
            total = sum((e.amount_home for e in cat_events_sorted), Decimal(0))
            fraction = Decimal(str(rule_cfg.get("daily_drip_fraction", daily_drip_fraction)))
            amount = (total / span_days) * fraction
            anchor, interval_kind, interval_n = request_date, "days", 1
        else:
            plan = _occurrence_plan_from_rule(dates, occurrence_rule)
            if plan is None:
                continue
            anchor, interval_kind, interval_n = plan
            amount = _amount_from_rule(
                [e.amount_home for e in cat_events_sorted], rule_cfg["amount_rule"], k_stdev_dec
            )
        if amount <= 0:
            continue

        flexibility_counts: dict[str, int] = {}
        for e in cat_events_sorted:
            flexibility_counts[e.flexibility] = flexibility_counts.get(e.flexibility, 0) + 1
        modal_flexibility = max(flexibility_counts, key=flexibility_counts.get)
        flexible = modal_flexibility != "fixed"
        anchor_event = cat_events_sorted[-1]
        canonical.append(
            CanonicalEvent(
                event_id=f"{category}_{profile.user_id}_variable",
                raw_event_id=anchor_event.event_id,
                user_id=profile.user_id,
                kind="recurring_flexible" if flexible else "recurring_essential",
                category=category,
                # Human-facing label only -- roadmap-review finding: this
                # used to be f"{category} (daily_drip, fraction=0.2)", the
                # model's OWN config parameters, and that string flowed
                # straight into decision_explanation via
                # explain/factsheet.py::driving_event_description, leaking
                # an internal hyperparameter to the end user on every row
                # whose binding constraint was this synthetic aggregate
                # event. The modelling choice (amount_rule/occurrence_rule/
                # daily_drip_fraction) stays fully recoverable from
                # `provenance` + config.yaml for anyone auditing the run;
                # it just doesn't belong in a sentence a user reads.
                description=f"everyday {category.replace('_', ' ')}",
                direction="debit",
                amount=amount,
                flexible=flexible,
                flexibility=modal_flexibility,
                minimum_allowed_amount=None,
                anchor_date=anchor,
                interval_kind=interval_kind,
                interval_n=interval_n,
                provenance=tuple(e.event_id for e in cat_events_sorted),
                amount_was_blank=False,
            )
        )

    # --- Future one-time rows not covered by a detected recurring series
    for e in future:
        key = _series_key(e)
        category = key[0]
        if key in recurring_group_keys:
            exclusion_log.append(
                {
                    "event_id": e.event_id,
                    "reason": "covered_by_recurring_series",
                    "detail": f"{key}",
                }
            )
            continue
        if category in var_cats and _rule_for(category)["occurrence_rule"] != "observed_cadence":
            exclusion_log.append(
                {
                    "event_id": e.event_id,
                    "reason": "covered_by_variable_expense_aggregate",
                    "detail": category,
                }
            )
            continue
        is_income = e.event_type in INCOME_EVENT_TYPES
        canonical.append(
            CanonicalEvent(
                event_id=e.event_id,
                raw_event_id=e.event_id,
                user_id=e.user_id,
                kind="income_confirmed" if is_income else "one_time_confirmed",
                category=e.category,
                description=e.description,
                direction="credit" if is_income else e.direction,
                amount=e.amount_home,
                flexible=False if is_income else e.flexibility != "fixed",
                flexibility=e.flexibility,
                minimum_allowed_amount=e.minimum_allowed_amount,
                anchor_date=e.settlement_date or e.event_date,
                interval_kind="once",
                interval_n=None,
                provenance=(e.event_id,),
                amount_was_blank=e.amount_was_blank,
            )
        )

    # A user can carry more than one recurring income series in the raw
    # history (a job change, a second household earner, a payroll record
    # amended mid-stream) -- messages.csv is the intended way to resolve
    # which one is current (Phase 8), but absent that evidence the least
    # arbitrary reading is "the most recently active stream is the current
    # job": keep only the recurring income series with the latest anchor
    # date, and log the rest as superseded rather than summing every
    # income stream the user has ever had.
    recurring_income = [
        c for c in canonical if c.kind == "income_confirmed" and c.interval_kind != "once"
    ]
    if len(recurring_income) > 1:
        keep = max(recurring_income, key=lambda c: c.anchor_date)
        for c in recurring_income:
            if c.event_id != keep.event_id:
                canonical.remove(c)
                exclusion_log.append(
                    {
                        "event_id": c.raw_event_id,
                        "reason": "superseded_by_more_recent_income_series",
                        "detail": keep.raw_event_id,
                    }
                )

    # A more recent income event than the kept series' own anchor, carrying
    # employment-ended language (a description change the per-description
    # grouping can't see, since it's a different description with too few
    # occurrences to form its own series), signals the job actually ended --
    # projecting the old cadence forward past that point would invent income
    # that stopped arriving. No income is invented for a description that
    # doesn't say so; this only ever SUPPRESSES a projection already found.
    TERMINATION_MARKERS = ("final employer", "before leave", "severance", "resigned", "terminated")
    if recurring_income:
        series = recurring_income[0] if len(recurring_income) == 1 else max(
            recurring_income, key=lambda c: c.anchor_date
        )
        if series in canonical:
            all_past_income = [
                e for e in past
                if e.event_type in INCOME_EVENT_TYPES
                and (e.settlement_date or e.event_date) > series.anchor_date
            ]
            if all_past_income:
                latest = max(all_past_income, key=lambda e: e.settlement_date or e.event_date)
                if any(marker in latest.description.lower() for marker in TERMINATION_MARKERS):
                    canonical.remove(series)
                    exclusion_log.append(
                        {
                            "event_id": series.raw_event_id,
                            "reason": "income_series_stopped_by_termination_marker",
                            "detail": latest.event_id,
                        }
                    )

    # A future one-time income row (e.g. a "Next confirmed salary" marker)
    # can be the SAME upcoming occurrence a recurring income series would
    # already project for its next date -- without deduping, that payday
    # gets credited twice. If a future one-time income event's amount
    # matches the kept series within 1%, treat it as the authoritative
    # confirmation of that next occurrence: re-anchor the series there
    # (amount and date from the confirmed row) and drop the standalone
    # duplicate, rather than double-crediting the same payday.
    recurring_income_final = [
        c for c in canonical if c.kind == "income_confirmed" and c.interval_kind != "once"
    ]
    if recurring_income_final:
        series = recurring_income_final[0]
        one_time_income = [
            c for c in canonical if c.kind == "income_confirmed" and c.interval_kind == "once"
        ]
        matches = [
            c for c in one_time_income
            if c.anchor_date > series.anchor_date
            and abs(c.amount - series.amount) <= series.amount * Decimal("0.01")
        ]
        if matches:
            confirmed = min(matches, key=lambda c: c.anchor_date)
            canonical.remove(confirmed)
            canonical.remove(series)
            canonical.append(
                dataclasses_replace(
                    series, anchor_date=confirmed.anchor_date, amount=confirmed.amount
                )
            )
            exclusion_log.append(
                {
                    "event_id": confirmed.raw_event_id,
                    "reason": "folded_into_recurring_income_series_as_new_anchor",
                    "detail": series.raw_event_id,
                }
            )

    return canonical
