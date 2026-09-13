# design.md — Buy or Wait affordability agent

Companion to `PRD.md`. This is the how. v2, after design review.

## 1. Design thesis

Seven of the eight output fields are a deterministic function of a correctly reconstructed ledger. The spec even hands over the tie-breaker order for plan selection, so there is nothing for a model to decide. An LLM in the arithmetic path adds variance and cost without adding accuracy.

The model is used in exactly three places:

1. Reading amounts out of images (no alternative — the data is only in pixels).
2. Turning free-text messages into typed amendments over known events.
3. Writing `decision_explanation` from a fact sheet the engine computed.

Everything else is pure Python. Deterministic layers decide; model layers perceive and explain.

## 2. Layer stack

| Layer | Responsibility | Nature |
|---|---|---|
| L0 | Load CSVs, normalise currency on dated rates, index media paths | I/O |
| L1 | VLM amount extraction, message to amendment classification | probabilistic |
| L2 | Ledger reconstruction: lifecycle collapse, dedupe, exclusions, precedence, classification | deterministic |
| L3 | Daily balance curve, suffix minima, the two closed forms | deterministic |
| L4 | Candidate plan enumeration including spending-change subsets | deterministic |
| L5 | Feasibility simulation and lexicographic ranking | deterministic |
| L6 | Invariant validation, repair, demotion | deterministic |
| L7 | Fact sheet to explanation, with number grounding | probabilistic |
| L8 | Write output and per-request audit trace | I/O |

L1 is the only layer with network access. L2 through L6 are pure functions, unit-testable without a model.

## 3. Pinned modelling decisions

Every one of these is a place where an unstated choice silently moves an exact-match field. Each has a default, a config key, and a way to confirm it against the 25 solved samples. They live in `config.yaml`, not in code.

| # | Ambiguity | Default | Config key | How it gets confirmed |
|---|---|---|---|---|
| 1 | Income beyond the confirmed salary | project recurring salary on inferred cadence | `income.mode: recurring_projected \| confirmed_only` | run both modes on the samples, pick on per-field accuracy |
| 2 | Recurring series phase | anchor on last observed occurrence | `recurring.anchor: last_observed` | assert occurrence count per event over the window against samples |
| 3 | Cadence 28–31 days | treat as calendar-month | `recurring.month_like_days: [28,31]` | compare a 3-cycle expansion against any sample with a month-like event |
| 4 | Forecast window bounds | inclusive `[request_date, request_date + 90]`, 91 dates | `horizon.days: 90`, `horizon.inclusive: true` | check a sample whose earliest date lands near day 90 |
| 5 | Payments past the window | safety enforced only inside the window; tail breaches logged not penalised | `horizon.enforce_outside: false` | check any sample recommending a long installment plan |
| 6 | Installment per-payment split | equal quantised payments, residual on the final payment | `installments.split: equal_residual_last` | assert the split reproduces any installment plan present in the samples |
| 7 | Amount formatting | derived per `home_currency` from the samples | `output.format_by_currency` (learned, not hand-written) | INV-14 |
| 8 | CSV dialect | copied from the blank template and sample file | `output.dialect: from_template` | byte-diff the header |

Decisions 1, 2 and 6 move many rows. The rest move one or two. All eight are resolved by measurement, not argument.

## 4. Data flow

```
dataset/*.csv ──► L0 ──► RawTables (FX-normalised)
                          │
dataset/media/*.png ──► L1 VLM ──┐
messages.csv ────────► L1 LLM ──┴──► Amendment[]  (allowlisted event IDs only)
                          │
                          ▼
                    L2 ──► UserFinancialState   keyed (user_id, request_date)
                          │   profile, CanonicalEvent[], exclusion log
                          ▼
        + request row, payment options,
          request-scoped messages and images ──► RequestContext
                          │
                          ▼
                    L3 ──► BalanceCurve, amount_safe_to_pay, earliest_date
                          │
                          ▼
                    L4 ──► Candidate[]
                          │
                          ▼
                    L5 ──► ranked Candidate[] ──► selected
                          │
                          ▼
                    L6 ──► VerifiedDecision  (or demote and retry)
                          │
                          ▼
                    L7 ──► decision_explanation
                          │
                          ▼
                    L8 ──► output.csv (root + dataset/) + audit/<request_id>.json
```

Stage contracts:

- `RawTables` — every amount already in `home_currency`; original value, rate, and rate source date retained for the audit trace.
- `UserFinancialState` — cached on `(user_id, request_date)`, never on `user_id` alone. Two requests from the same user on different dates must produce different curves, because a settlement dated between them is future for one and past for the other. Enforced by a test.
- `RequestContext` — the per-request view. Request-scoped messages and images resolve here, never into the shared user state, so no request can leak evidence into another.
- `VerifiedDecision` — the eight output fields plus the binding constraint date, the ranking criterion that decided the selection, and the invariant results.

## 5. Core data model

```python
@dataclass(frozen=True)
class CanonicalEvent:
    event_id: str
    kind: Literal["recurring_essential", "recurring_flexible",
                  "one_time_confirmed", "income_confirmed"]
    amount: Decimal                # home_currency, positive = outflow
    anchor_date: date              # last observed occurrence, or the one-time date
    interval_kind: Literal["once", "days", "calendar_month"]
    interval_n: int | None         # step size for days / calendar_month
    flexible: bool
    provenance: list[str]          # source rows, amendments, image IDs

    def occurrences(self, start: date, end: date) -> list[date]: ...
```

Phase is explicit. A recurring series is anchored on its last observed occurrence in the data and stepped forward by `interval_kind`, so rent on the 5th stays on the 5th instead of drifting the way a flat 30-day step does after two cycles. `occurrences()` is the only place the calendar is walked, and it has its own unit tests.

```python
@dataclass(frozen=True)
class Candidate:
    method: Literal["full_payment", "partial_payment", "installments",
                    "wait", "not_recommended"]
    payments: tuple[tuple[date, Decimal], ...]
    changes: tuple[SpendingChange, ...]
    option_id: str | None
    total_paid: Decimal
    total_reduction: Decimal        # sum of cash freed by changes, for criterion 7
    completes_by_deadline: bool
    tail_breach: bool               # breach strictly outside the window; logged only
```

All money is `Decimal`. No floats in the money path — float drift at IDR scale is a silent loss on the highest-weight field.

## 6. L2 — ledger reconstruction

1. Group rows by lifecycle root, following `linked_event_id` transitively.
2. Collapse each group to its terminal state. Settled beats pending beats forecast. A refund linked to a debit nets against that debit rather than becoming a separate credit.
3. Drop rows with a logged reason: `pending_credit`, `failed`, `cancelled`, `duplicate`, `unrealized_investment`. The spec excludes pending *credits* only — a pending debit survives and is reserved against the balance. There is a dedicated unit test asserting exactly that, because a generic "drop anything pending" filter would inflate every amount in the dataset.
4. Apply amendments from L1. An amendment can cancel, reschedule, amend the amount of, or confirm an event that already exists. It cannot create one.
5. Resolve remaining conflicts with a scored comparator: explicit cancellation/settlement/amendment (4) beats newer-same-source (3) beats settled-over-estimate (2) beats safer-interpretation (1). "Safer" means keep the debit, drop the credit, take the earlier debit date and the later credit date.
6. Classify survivors into the four kinds, with `anchor_date` and `interval_kind` set per decision 2 and 3 above.
7. Fill blank amounts from images (section 11).

Every drop, merge, and amendment lands in the audit trace. That trace is what makes a wrong answer debuggable in ten seconds instead of an hour.

### Income projection

The spec says the forecast uses "recurring income and expenses" and the dataset supplies "the next confirmed salary." Those pull in opposite directions: if the only income in a 90-day window is one confirmed salary, the curve trends down and almost every multi-month installment schedule fails feasibility — which would make `installments` nearly unrecommendable, an outcome the allowed-values list clearly does not intend.

So income has two modes behind `income.mode`:

- `recurring_projected` (default) — the confirmed salary is placed on its settlement date, and further salary occurrences are projected from the cadence inferred from that user's historical salary events. Nothing is invented: the cadence comes from observed rows, and no projection happens for a user with no salary history.
- `confirmed_only` — the strict reading. Salary lands once, on its settlement date.

Both modes run against the samples and the winner is chosen on measured per-field accuracy. The share of rows recommending `installments` under each mode is the fastest diagnostic.

## 7. L3 — the forecast engine

Build `bal(t)` for `t` in the window by expanding every `CanonicalEvent` through `occurrences()`, applying income per the configured mode, and adding confirmed one-offs. Essentials are ordinary outflows in this curve, which is how "cover essential expenses" gets enforced without a separate rule.

One horizon constant, `HORIZON`, is used by the closed forms and by candidate feasibility. There is no second window.

A payment of `X` on date `d` lowers every balance from `d` onward by exactly `X`. That linearity removes the need for any search:

```python
headroom = min(bal(t) - min_balance for t in window)
amount_safe_to_pay = quantise_down(clamp(headroom, 0, requested_amount), currency)

suffix_min[d] = min(bal(t) for t in [d, window_end])
earliest = first d in window where suffix_min[d] - min_balance >= requested_amount
```

Quantisation happens here, at the end of L3, not at write time. `partial_payment` requires the two payments to sum to `requested_amount` exactly, so the remainder must be derived from the already-quantised `amount_safe_to_pay`. Rounding is *down* — rounding up could push the plan below the minimum balance by a cent, which is the wrong direction to be wrong in.

Two traps to respect:

- `earliest_date_for_full_payment` measures capacity. It is computed with no reference to `payment_methods_user_will_consider`, before and independently of plan selection, and can equal `request_date` on a row whose recommendation is `installments`.
- An empty `earliest_date_for_full_payment` does *not* imply `not_affordable`. An installment plan never requires the full amount to be safe as a single payment, so `affordable_with_plan` with an empty earliest date is a valid row. See INV-16.

## 8. L4/L5 — candidates and ranking

Candidates per request:

- `full_payment` on `request_date`.
- `wait`: the full amount on `earliest_date_for_full_payment`.
- `partial_payment`: exactly two payments — `amount_safe_to_pay` on `request_date`, `requested_amount - amount_safe_to_pay` on `earliest_date_for_full_payment`. Only when the request allows partial, the user accepts it, `0 < amount_safe_to_pay < requested_amount`, and `earliest <= desired_completion_date`.
- One candidate per row in `request_payment_options.csv`.
- Each of the above crossed with spending-change subsets.

### Installment schedules

Dates come from the option's start date and interval, count from the option, and the amounts from the option's own per-payment column if one exists. If the file gives only `total_payable` and a count, the split convention is equal quantised payments with the residual on the final payment, asserted to sum exactly to `total_payable`. This is a stated convention rather than a guess, and it is checked against any installment plan appearing in the solved samples before the full run. Fees are inside `total_payable`; nothing is recomputed from a rate.

### Spending changes

Restricted to `recurring_flexible` events, at most three, one change per event, `stop` and `reduce_to` mutually exclusive on the same event.

`reduce_to` amounts are solved, not sampled. Given the deficit `D` at the binding constraint date and `k` occurrences of that event inside the window up to that date, the minimum viable new amount is

```
reduce_to = max(0, current_amount - ceil_to_precision(D / k))
```

with `stop` as the degenerate case when that lands at or below zero. Exact arithmetic, one candidate per event instead of a ladder, and no arbitrary tie-breaking between reduction sizes.

Subset search: rank flexible events by cash freed inside the window, keep the top six, enumerate subsets of size 0 to 3. A few hundred simulations per request, which is milliseconds.

### Feasibility

Overlay the schedule and change deltas on the baseline curve and require `bal(t) >= min_balance` for every `t` inside `HORIZON`. Payments that fall past the window are still simulated, but a breach strictly outside the window sets `tail_breach` and is recorded in the audit trace rather than demoting the candidate — the spec defines safety over the 90-day forecast, and enforcing beyond it would silently reject long installment options the dataset went out of its way to supply. If sample scoring says otherwise, `horizon.enforce_outside` flips.

### Eligibility and ranking

An immediate method must appear in `payment_methods_user_will_consider`. `wait` needs the user to accept `full_payment`. `not_recommended` is the fallback when nothing eligible is safe.

```python
key = (not completes_by_deadline,   # 1 complete by desired_completion_date
       len(changes),                 # 2 require no spending changes
       total_paid,                   # 3 minimise total paid
       first_payment_date,           # 4 start earlier
       len(payments),                # 5 fewer payments
       option_id or "",              # 6 lowest payment_option_id — spec's final tie-break
       total_reduction)              # 7 local: smallest total reduction
```

Criterion 7 is mine, not the spec's, and it sits *after* the spec's stated final tie-breaker so it can never reorder two candidates the spec would have separated. It only ever fires between plans that are identical except for reduction size, which share an `option_id` — so its position is equivalent to sitting at 5.5, without touching the documented order.

Criterion 2 counts changes where the spec says "require no spending changes," which may be binary. I keep the count and log which criterion decided each selection, so the samples can confirm or refute it. Criterion 3 is why a fee-bearing installment plan should rarely beat a safe full payment; that has its own regression test.

## 9. Status decision table

| Condition | status | method |
|---|---|---|
| Full amount safe on `request_date` and user accepts `full_payment` | `affordable_now` | `full_payment` |
| Full request completed via partial, installments, or permitted changes | `affordable_with_plan` | that method |
| Nothing safe now, full amount safe later, user accepts `full_payment` | `affordable_later` | `wait` |
| No eligible safe plan in the window | `not_affordable` | `not_recommended` |

`affordable_now` forces `earliest == request_date`. `not_recommended` forces `payment_plan == none`.

## 10. L6 — invariants

Every row passes all of these before it is written. Each emits a code into the audit trace.

```
INV-01  0 <= amount_safe_to_pay <= requested_amount
INV-02  status and method are in their allowed enums
INV-03  status == affordable_now  =>  earliest == request_date and method == full_payment
INV-04  method == partial_payment => status == affordable_with_plan, exactly two payments,
        p1.date == request_date, p1.amount == amount_safe_to_pay,
        p1 + p2 == requested_amount, p2.date == earliest <= desired_completion_date
INV-05  method == installments => dates and count match a supplied option, and the
        payments sum exactly to that option's total_payable
INV-06  method == wait => plan is the full amount on earliest, user accepts full_payment
INV-07  method == not_recommended => payment_plan == "none"
INV-08  plan dates non-decreasing; format YYYY-MM-DD:<amount> joined by |
INV-09  every spending change targets a flexible recurring event
INV-10  at most three changes; no repeated event; stop and reduce_to never share an event
INV-11  the selected plan re-simulates safe inside HORIZON in a freshly built simulator
INV-12  every numeral in decision_explanation resolves against the fact sheet after
        normalisation, with window constants and fact-sheet dates allowlisted
INV-13  exactly one row per request_id; column order taken from the blank template header
INV-14  amount formatting matches the sample convention for that home_currency
INV-15  status == affordable_later => earliest is non-empty and strictly after request_date
INV-16  an empty earliest does NOT constrain status; asserted by a fixture row where
        installments are safe and no single-payment date is ever safe
INV-17  amount_safe_to_pay is independent of the selected method; a not_recommended row
        may carry a nonzero value, asserted by a fixture row
INV-18  every FX-converted amount has a recorded rate and rate source date
INV-19  no event with a blank source amount reaches the forecast with a substituted value
        unless the request is flagged in the audit trace
```

INV-11 is the self-correction hook: independent re-verification rather than trusting the cached feasibility result. On failure, demote to the next-ranked candidate and log the demotion. If every candidate fails, emit `not_affordable` / `not_recommended` / `none`. Never a blank row.

INV-16 exists because the tempting-but-wrong inverse — treating an empty earliest date as proof of `not_affordable` — would demote legitimate installment rows. The fixture locks the correct behaviour against a future refactor.

## 11. L1 — perception, FX, and the injection boundary

### Injection boundary

The defence against untrusted message content is structural, not an instruction. The classifier can only return this:

```json
{"amendments": [
  {"kind": "cancel|reschedule|amend_amount|confirm|no_op",
   "target_event_id": "event_14",
   "new_amount": 1200.0,
   "new_date": "2026-10-03",
   "message_id": "msg_88",
   "confidence": 0.0}
]}
```

`target_event_id` is validated against the event IDs joined to that user; unknown IDs are dropped. No field can request a payment method, a status, an output amount, or a policy change. The worst a hostile message can do is propose an amendment to an event that already exists, which then goes through the same precedence resolution as everything else. Sub-threshold confidence is dropped and logged.

Every `no_op` is logged with its full text into the audit trace, and that list gets one manual pass against the sample rows. A message that carried real signal and was classified `no_op` is exactly the failure that never surfaces in error analysis on its own.

### FX policy

Exact date match first; failing that, the most recent prior rate for the pair. If only the reverse pair exists, invert it explicitly. If neither direction exists at any date, raise — never default to 1.0, which would silently mix currencies. Rate and rate source date go into the audit trace (INV-18).

### Blank amounts

The ladder, in order, for an event whose `amount` is blank:

1. VLM extraction from the linked image, with a numeric-plausibility check against the event's category and the user's currency scale.
2. Retry with the fallback provider on a cropped region.
3. Conservative substitution — the highest amount among that user's comparable events in the same category — and the request is flagged in the audit trace (INV-19).

Step 3 is a last resort and is deliberately conservative rather than neutral, because zero is explicitly forbidden and an underestimate inflates every amount downstream. The real mitigation is making step 1 and 2 not fail; the flag list is reviewed after the sample run, and if it is non-empty on 250 rows, extraction gets fixed rather than papered over.

### Caching

Temperature 0 throughout. Disk cache keyed by `image_id` for images and by `user_id` plus a message-set hash for messages.

## 12. L7 — grounded explanation

The fact sheet per request: balance today, minimum balance, the binding constraint date and its headroom, total essentials in the window, next income date and amount, the chosen plan, the changes, and the two closed-form outputs.

Batched 10 to 20 fact sheets per call, and the response is **a JSON object keyed by `request_id`**, never a positional array. The returned key set is validated against the key set sent; any mismatch drops that whole batch to single-request calls. Positional matching would silently attach one user's explanation to another user's row, which is invisible in per-field scoring and fatal in the interview.

Number grounding (INV-12) compares after normalisation: numerals are parsed to `Decimal`, dates are matched in any rendering that resolves to a fact-sheet date, and window constants (90, the horizon) plus ordinals are allowlisted. Comparison uses the output rounding tolerance. Without that normalisation the check would fire on almost every row and burn the retry budget for nothing.

Failure gets one retry, then a deterministic template. A template naming the binding constraint scores fine on "usefulness and consistency"; a fluent sentence with an invented number does not.

## 13. Development loop

Inner, per request:

```
load -> perceive (cached) -> reconstruct -> forecast -> enumerate
     -> rank -> validate -> [repair, max 2] -> explain -> ground-check -> emit + audit
```

Outer:

```
run on sample_requests.csv (25 labelled)
  -> evaluation/score.py: per-field accuracy, not row accuracy
  -> bucket errors by field x request_type x status
  -> open the audit JSON for the worst bucket, find the divergence layer
  -> change one rule or one config decision from section 3
  -> re-run (perception from cache, so iteration is seconds)
plateau -> run all 250 -> evaluation/sanity.py distribution check
```

25 rows is too thin to detect a plateau on its own — two rows can decide whether an interpretation looks right. So the full run also gets an unlabelled distribution check: status mix, method mix, share of empty earliest dates, share of rows with spending changes, share of rows where `amount_safe_to_pay == requested_amount`. A run where 80% of rows come out `not_affordable`, or where `installments` never appears, is wrong without needing a single label, and that signal arrives faster than any sample-set delta. Each config decision from section 3 gets its distribution signature recorded so mode comparisons are visible at a glance.

## 14. Repo layout

```
code/
├── main.py                   # CLI entry: python3 code/main.py
├── config.yaml               # the section 3 decisions, thresholds, models, budgets
├── io_layer/loader.py
├── io_layer/fx.py
├── io_layer/probe.py         # prints real columns and value distributions
├── perception/vlm.py
├── perception/messages.py
├── perception/schemas.py     # the injection boundary
├── perception/cache.py
├── ledger/canonical.py
├── ledger/precedence.py
├── ledger/classify.py
├── ledger/recurrence.py      # occurrences(), anchor and calendar-month stepping
├── engine/forecast.py
├── engine/closed_form.py
├── engine/candidates.py
├── engine/reductions.py      # closed-form reduce_to
├── engine/simulate.py
├── engine/rank.py
├── engine/status.py
├── verify/invariants.py
├── explain/factsheet.py
├── explain/generate.py
├── writer.py                 # writes root output.csv and dataset/output.csv
├── audit/
└── evaluation/
    ├── score.py              # per-field metrics on the solved samples
    ├── sanity.py             # unlabelled distribution check on the full run
    ├── error_analysis.md
    ├── ablations.md          # income mode, no-VLM, no-messages, template-only
    └── usage_report.md
```

The writer emits both output paths — the problem statement points at `dataset/output.csv` while the README and submission table point at root `output.csv` — and takes column names and order from the blank template's header rather than a hardcoded list.

## 15. Token and cost profile

One batched message call per user, one VLM call per distinct needed image (cached), one batched explanation call per ~15 requests. For 250 requests that is low hundreds of calls, and the arithmetic path costs zero tokens. That makes `usage_report.md` an argument for the design rather than an apology for it.

## 16. Open questions, to close against the real data

1. Does `request_payment_options.csv` carry a per-payment amount column? If yes, decision 6 becomes a copy rather than a split.
2. Column semantics in `financial_events.csv`: what marks recurring versus one-time, what the cadence field is named, the status vocabulary, how `flexible` is flagged, whether salary history is present per user.
3. Which `income.mode` wins on the samples. This is the highest-leverage unknown in the document.
4. Whether any solved sample shows `affordable_with_plan` with an empty earliest date (confirms INV-16), or `affordable_now` with a non-`request_date` earliest date (would refute INV-03).

## 17. Closed implementation facts (moved out of config.yaml)

A second review flagged `config.yaml` for carrying "reference only" blocks
that read like live switches but are never loaded -- a config file where
the reader can't tell a wired key from documentation is worse than no
config file. These six facts are genuinely closed (asserted by a specific
module + test, not swept), so they live here instead. `config.yaml` itself
now carries only keys an actual `load_config()` call site reads; each
still gets a one-line pointer back to this section rather than the prose.

1. **`recurring.month_like_days`** — `ledger/recurrence.py`'s
   `MONTH_LIKE_DAYS = range(28, 32)` hardcodes the `[28, 31]` tolerance
   band directly; not read from config.
2. **`horizon.inclusive`** — `engine/forecast.py`'s `ProjectedBalance`
   always treats `[request_date, horizon_end]` as closed on both ends; no
   strict-exclusive code path exists to switch to.
3. **`installments` split convention** (roadmap doc §0 finding 1) —
   `request_payment_options.csv` carries `payment_amount` /
   `number_of_payments` / `first_payment_date` / `payment_frequency_days` /
   `financing_fee` / `total_payable_amount` directly; dates are
   `first_payment_date + k * payment_frequency_days`, amounts are
   `payment_amount` copied verbatim. Asserted by `engine/options.py`'s own
   sum-to-`total_payable_amount` check and `code/tests/test_options.py`.
4. **`spending_changes` eligibility gate** (roadmap doc §0 finding 3) —
   the boolean logic lives in `ledger/classify.py::stop_eligible` /
   `reduce_eligible`: flexibility in `{stoppable, reducible_or_stoppable}`
   for stop / `{reducible, reducible_or_stoppable}` for reduce, crossed
   with the profile's `expense_categories_to_protect` /
   `..._willing_to_reduce` / `..._willing_to_stop`. A fixed rule, not a
   value worth sweeping. (`spending_changes.max_changes` IS wired --
   `engine/candidates.py` reads it. `max_per_event` is structurally
   guaranteed at 1 by construction, not a switch.)
5. **`reductions` formula** (roadmap doc §0 finding 4) —
   `reduce_to = max(minimum_allowed_amount, current - ceil(D/k))`, subset
   members each covering a share of the deficit. Lives in
   `engine/reductions.py`, asserted by `code/tests/test_reductions.py`.
6. **`output` dialect and formatting** — the CSV dialect comes from
   `io_layer/template.py` reading `dataset/output.csv`'s own header at
   runtime; amount formatting (`writer.py`'s `PLAN_DECIMAL_PLACES`,
   `format_amount_safe_to_pay`) was learned from
   `dataset/sample_requests.csv` and is asserted exactly by
   `code/tests/test_writer.py`. Neither is worth exposing as a config
   switch since changing either would just break the assertions.
