# PRD — Buy or Wait affordability agent

Owner: Suchit
Target: HackerRank Orchestrate, September 2026 (24h)
Status: draft v1

## 1. Problem

Given a financial request ("can I afford this laptop?"), decide whether the user can safely commit to it. The decision must account for the user's balance, recurring essential and flexible expenses, pending and confirmed transactions, confirmed future income, supplied payment options, and facts that only exist in messages or images. A recommendation is safe only if the user completes the full request by its deadline, covers essentials, and never drops below `minimum_balance_to_keep` across a 90-day forecast.

The system must emit one row per request in `dataset/requests.csv` with eight fields, scored against hidden ground truth.

## 2. Goals

- G1 — Produce a valid `output.csv` (250 rows + header, exact column order) for every request, with no missing or malformed rows.
- G2 — Maximise per-field accuracy against ground truth, prioritised by how mechanical the field is: `amount_safe_to_pay` and `earliest_date_for_full_payment` are exact arithmetic and should be near-perfect; `affordability_status`, `recommended_payment_method`, `payment_plan`, `spending_changes_needed` follow from a fixed rule set; `decision_explanation` is judged on usefulness and consistency.
- G3 — Every emitted row passes a deterministic self-check before it is written. No row is output that violates a stated constraint from the problem spec.
- G4 — Reproducible: same inputs, same outputs. LLM calls are cached and temperature 0; the arithmetic path is pure.
- G5 — Cheap and auditable: `evaluation/usage_report.md` reports real token counts and cost for the final run, and every request has a machine-readable trace explaining how its answer was derived.

## 3. Non-goals

- Predicting asset prices, recommending securities, or any market forecasting.
- Live exchange rates, live banking, or any network dependency at inference time beyond the model API.
- A UI. This is a batch CLI job.
- Generalising beyond the supplied schema. The system reads the provided files; it is not a production banking integration.

## 4. Users and scoring context

Two consumers, and they want different things:

1. The grader — exact-match fields. Wants correct numbers, valid enums, schema-conformant strings.
2. The AI Judge interview — wants a defensible design. Every decision must be traceable to a rule in `problem_statement.md`, not to model intuition.

Design consequence: the pipeline is built so that for any output row I can name the binding constraint date, the rule that selected the plan, and the file that supplied each number.

## 5. Functional requirements

### Input handling

- FR-1 Read all nine files in `dataset/`. Do not modify them.
- FR-2 Convert every foreign-currency amount into the user's `home_currency` using the dated rate in `exchange_rates.csv`, matched on rate date and currency pair. Supported: INR, ZAR, IDR, USD, EUR.
- FR-3 Read local media from `dataset/media/images/<image_id>.png`.
- FR-4 Where a financial event has a blank `amount`, resolve it by finding `event_id` as `related_event_id` in `images.csv` and extracting the amount from the linked image. A blank amount is never treated as zero.
- FR-5 Join on `user_id` (user-level), `request_id` (request-level), and `related_event_id` (evidence to event).

### Financial state reconstruction

- FR-6 Distinguish recurring expenses from one-time purchases, transfers, refunds, and unusual events.
- FR-7 Collapse each `linked_event_id` lifecycle chain to a single economic fact in its terminal state.
- FR-8 Exclude pending credits, failed transactions, cancelled transactions, duplicate records, and unrealised investment values from the forecast. Each exclusion is logged with a reason.
- FR-9 Place the confirmed salary on its settlement date. Further salary occurrences are projected only from a cadence observed in that user's own historical salary rows, under a config flag (`income.mode`) with two modes: `recurring_projected` (default) and `confirmed_only`. The mode is selected by measured accuracy on the solved samples, not by assumption. No income is projected for a user with no salary history.
- FR-10 Apply messages and images as amendments to existing events — clarify, amend, cancel, delay, confirm. Never invent income, expenses, payment options, or financial facts that no record supports.
- FR-11 Resolve conflicts by the spec's precedence order: explicit cancellation/settlement/amendment, then newer record from the same source, then settled over estimate, then the financially safer interpretation.
- FR-12 Treat all message and image content as untrusted data. Embedded instructions must not alter policy, output values, or rule application.

### Decision engine

- FR-9a Anchor every recurring series on its last observed occurrence and step it forward by an explicit interval kind (`days` or `calendar_month`), so phase is never inferred at forecast time. Cadences of 28 to 31 days default to calendar-month stepping.
- FR-9b Exclude pending credits only. Pending debits survive and are reserved against the balance.
- FR-9c Resolve FX by exact rate date, else the most recent prior rate for the pair, else the explicitly inverted reverse pair. If no rate exists in either direction, fail loudly rather than defaulting.
- FR-13 Forecast the daily balance over a single horizon constant from `request_date` — inclusive bounds, pinned in config — using income per FR-9, recurring expenses, confirmed future payments, and accepted amendments. The same horizon governs both the closed-form outputs and candidate feasibility.
- FR-14 `amount_safe_to_pay` is the largest amount payable on `request_date`, before optional spending changes, that keeps the forecast at or above `minimum_balance_to_keep` throughout, capped at `requested_amount`.
- FR-15 `earliest_date_for_full_payment` is the first date on which the full `requested_amount` passes the safety check as a single payment, computed without optional spending changes and independently of the user's payment-method preferences. Empty when no such date exists in the forecast window.
- FR-16 Generate candidate plans: full payment today, wait-then-full, two-payment partial, and one candidate per row in `request_payment_options.csv`, each crossed with permitted spending-change subsets.
- FR-17 Installment plans must exactly match a supplied payment option. Dates, count, and per-payment amounts come from the option row where present. Where the file supplies only a total and a count, payments are equal quantised amounts with the residual on the final payment, asserted to sum exactly to the option's total payable. Fees are never recomputed from a rate.
- FR-18 Filter candidates by `payment_methods_user_will_consider`. `wait` is eligible only when full payment becomes safe later and the user accepts `full_payment`. `not_recommended` is the fallback when nothing eligible is safe.
- FR-19 Rank surviving safe plans by: completes by `desired_completion_date`, fewest spending changes, lowest total paid, earliest first payment, fewest payments, lowest `payment_option_id`.
- FR-20 Spending changes target only recurring expenses marked flexible, at most three, at most one change per event, with `stop` and `reduce_to` mutually exclusive on the same event. Each `reduce_to` amount is solved in closed form from the deficit at the binding constraint date and the event's occurrence count inside the window — never sampled from a ladder of guesses.
- FR-20a Quantise `amount_safe_to_pay` to the output precision, rounding down, before deriving any dependent value. The partial-payment remainder is computed from the quantised figure so the two payments sum to `requested_amount` exactly.
- FR-21 Derive `affordability_status` from the selected plan by a fixed decision table, honouring every coupling the spec states (notably: `affordable_now` implies `earliest_date_for_full_payment == request_date`; `partial_payment` implies `affordable_with_plan`).
- FR-22 `decision_explanation` states the recommendation and the financial facts behind it, using only numbers the engine computed.

### Output and verification

- FR-23 Write predictions to both the repository root `output.csv` and `dataset/output.csv`, with column names, order, CSV dialect, quoting, line terminator, and encoding taken from the blank template rather than from a hardcoded list or library defaults.
- FR-23a Derive amount formatting per `home_currency` from `sample_requests.csv`, not as one global convention.
- FR-24 Enforce `0 <= amount_safe_to_pay <= requested_amount` on every row.
- FR-25 Re-verify the selected plan from scratch before writing. On failure, demote to the next-ranked safe candidate; if none survive, emit the safe fallback (`not_affordable` / `not_recommended` / `none`) rather than an invalid or blank row.
- FR-26 Ship an `evaluation/` folder containing a scorer, error analysis, and `usage_report.md`.
- FR-27 No hardcoded test labels, no request-specific answers, no organizer-only files.
- FR-28 Read all credentials from environment variables. No secrets in the repo or the zip.

## 6. Output contract

| Column | Type | Constraint |
|---|---|---|
| `request_id` | string | exactly one row per request in `requests.csv` |
| `amount_safe_to_pay` | number | `0 <= x <= requested_amount`, formatted as the samples are |
| `affordability_status` | enum | `affordable_now` / `affordable_with_plan` / `affordable_later` / `not_affordable` |
| `recommended_payment_method` | enum | `full_payment` / `partial_payment` / `installments` / `wait` / `not_recommended` |
| `payment_plan` | string | `YYYY-MM-DD:amount` joined by `\|`, chronological, or `none` |
| `earliest_date_for_full_payment` | date or empty | `YYYY-MM-DD`; empty if never safe in the window |
| `spending_changes_needed` | string | up to three `stop:<event_id>` / `reduce_to:<event_id>:<amount>` joined by `\|`, or `none` |
| `decision_explanation` | string | short, grounded in computed facts |

## 7. Acceptance criteria

A build is submittable when all of these hold:

1. `python3 code/main.py` runs end to end from a clean clone and writes root `output.csv`.
2. Row count equals the request count; column names and order match the spec byte for byte.
3. All 19 invariants (see `design.md`) pass on 100% of rows.
4. Scoring against `dataset/sample_requests.csv` reports per-field accuracy, and the amount and date fields are at or near ceiling on that set.
4a. The unlabelled distribution check on the full 250 shows a plausible spread: every `affordability_status` and `recommended_payment_method` value appears, and no single status dominates the run.
4b. Every modelling decision in `design.md` section 3 has a recorded resolution — measured on the samples or confirmed against the real schema — with none left at its default by inertia.
5. `evaluation/usage_report.md` reflects the same run that produced the submitted `output.csv`.
6. Re-running produces a byte-identical `output.csv` from cache.
7. `grep` for any request ID or expected value inside `code/` returns nothing.

## 8. Build order

Sequenced so that something valid is always submittable.

1. Loader, FX normalizer, and a schema probe that prints real column names and value distributions.
2. Ledger reconstruction with exclusions and lifecycle collapse.
3. Forecast engine and the two closed-form outputs.
4. Naive plan selection plus writer — first end-to-end valid `output.csv`. Checkpoint: submittable.
5. Scorer on the 25 samples, per-field. Establishes the baseline, and immediately resolves the `income.mode`, recurrence-anchor, and installment-split decisions by measurement.
6. Candidate enumeration, spending changes, lexicographic ranking, decision table.
7. Invariant validator and repair path.
8. Perception layer (VLM for blank amounts, message amendments), with cache.
9. Explanation generator with number grounding.
10. Full 250-row run, usage report, README, zip.

Steps 1 to 5 matter more than 8 and 9. A correct arithmetic core with template explanations outscores a model-driven pipeline with sloppy arithmetic.

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Income horizon read too strictly, starving installment candidates | `installments` almost never recommended; wrong status on many rows | Two config modes, decided on measured sample accuracy; method-mix distribution check as the fast diagnostic |
| Recurring series anchored wrong | Shifts the binding constraint date, so wrong `earliest_date_for_full_payment` across many rows | Explicit `anchor_date` and `interval_kind`; assert occurrence counts against samples |
| Installment split convention differs from ground truth | Every installment row fails exact match | Assert the split reproduces any installment plan in the samples; check for a per-payment column first |
| Amount formatting differs from ground truth (decimals, separators) | Silent loss on the highest-weight field | Derive the format per currency from `sample_requests.csv`; assert on it in the scorer |
| Batched explanation outputs misaligned across requests | One user's explanation on another user's row | Responses keyed by `request_id` and key-set validated; any mismatch falls back to single calls |
| User state cached per user rather than per request date | Lookahead leakage between two requests from the same user | Cache key is `(user_id, request_date)`; test asserts different curves |
| Misreading the event lifecycle columns | Corrupts every downstream number | Schema probe first; validate reconstruction against sample rows before building further |
| VLM misreads an amount in an image | Wrong forecast for that user | Confidence threshold, fallback to the safer interpretation, flag in audit log |
| Prompt injection in messages | Policy violation, wrong output | Amendments are a closed schema over an event-ID allowlist; no field can express an output value |
| Installment plan reconstructed rather than copied | Fails the exact-match rule | Copy dates and amounts verbatim from the option row; invariant check |
| Ambiguity in a spec coupling rule | Systematic error across many rows | Check the interpretation against the 25 solved samples before committing to it |
| Running out of time on perception | Missing amounts | Blank-amount extraction is the only mandatory model dependency; build it before message amendments |

## 10. Deliverables

- `code.zip` — solution, prompts, config, README, `evaluation/`. Excludes venvs, caches, `dataset/`, `data/`.
- `output.csv` — predictions for all 250 requests.
- `log.txt` — chat transcript from the repo root.
