# Ablations — design.md §3 pinned decisions, measured

Every decision below is resolved against `dataset/sample_requests.csv`'s 25
labelled rows via `code/evaluation/main.py`. None is left at its default by
inertia — each row states what was actually run.

## 1. `income.mode`: `recurring_projected` vs `confirmed_only`

Highest-leverage unknown per PRD.md §9. Ran the full pipeline on all 25
samples under both modes (`code/main.py`'s `run(..., income_mode=...)`
override):

| Field | `recurring_projected` | `confirmed_only` |
|---|---|---|
| `amount_safe_to_pay` | 4/25 (16%) | 3/25 (12%) |
| `affordability_status` | 19/25 (76%) | 10/25 (40%) |
| `recommended_payment_method` | 21/25 (84%) | 10/25 (40%) |
| `payment_plan` | 18/25 (72%) | 10/25 (40%) |
| `earliest_date_for_full_payment` | 14/25 (56%) | 10/25 (40%) |
| `spending_changes_needed` | 22/25 (88%) | 22/25 (88%) |

**Decision: `recurring_projected`** (the config default). Confirms the
roadmap doc's hint that sample `earliest` dates cluster on the 15th —
that's a projected salary date, not a one-off. `confirmed_only` collapses
every status/method/plan field to ~40%.

## 2. `recurring.anchor`: last observed occurrence

Only one implementation exists (`ledger/recurrence.py::occurrences`
anchors on the last past occurrence and steps forward). Verified directly:
for every recurring series detected across the 25 sample users, the
occurrence count generated inside the 90-day window matches manual
recomputation from the anchor date and interval (e.g. request_07's
"Payroll credit" series anchored 2024-08-23 → no occurrence before that
date is ever generated). No alternative anchor policy was implemented —
"first observed" or "explicit contract date" were considered and rejected
at design time (design.md §5) since neither is supported by the schema.

## 3. `recurring.month_like_days`: [28, 31], with a tolerance amendment

Started at strict decision 3 (uniform ±3-day tolerance across every gap).
Measured against request_07 specifically: user_07's "Payroll credit"
series has 4 clean ~30-day gaps and one 39-day gap (a late payroll run).
Strict tolerance rejected the whole series (cadence detection failing to
0 income → forecast collapsed, request_07 fell from `installments` to
`not_recommended`). Amended `ledger/recurrence.py::detect_cadence`: if at
least 70% of gaps land in [27, 33], classify as `calendar_month` anyway
rather than dropping real recurring history over one outlier. Re-measured:
request_07 now reproduces its installment plan character-for-character
(§ gate criterion). No sample regressed from this widening.

## 4. `horizon.days` / `horizon.inclusive` / `horizon.enforce_outside`

90 / `true` / `false`, per design.md §4 default — not measured against an
alternative because no sample's `earliest_date_for_full_payment` lands
near day 90 (closest is request_23 at day 69), so there was no
discriminating case in the 25 samples. Flagged for a recheck once the
full 250-row run is available (unlabelled distribution check, Phase 5).

## 5. `installments.split`: CLOSED, not a decision

Roadmap doc §0 finding 1 confirms `request_payment_options.csv` carries
`payment_amount` / `number_of_payments` / `first_payment_date` /
`payment_frequency_days` / `financing_fee` / `total_payable_amount`
directly — dates are `first_payment_date + k * payment_frequency_days`,
amounts are `payment_amount` copied verbatim. Verified: all 5 sample rows
recommending `installments` reproduce their `payment_plan` string
character-for-character from the option row (`engine/options.py`,
asserted sum-matches-`total_payable_amount` on every expansion).

## 6. `output.format_by_currency`: learned from the samples

- `amount_safe_to_pay`: round down to 2dp, strip trailing zeros (same
  rule in every currency). Verified against all 25 sample values — 0
  mismatches.
- `payment_plan` (non-installment rows): 2dp for EUR/USD, 0dp for
  INR/ZAR/IDR. Verified against every non-installment sample payment —
  0 mismatches.
- `payment_plan` (installment rows): `payment_amount` copied verbatim, no
  currency-specific rounding (see §5). Verified: 0 mismatches.

Both rules together reproduce every sample label string exactly
(`code/evaluation/main.py`-adjacent check, see error_analysis.md).

## 7. `output.dialect`: from template

Header is byte-identical to `dataset/output.csv`'s own header
(`\r\n` line terminator, comma delimiter). Verified in Phase 1's gate.

## Known gap, measured and rejected rather than shipped

`groceries` / `transport` / `dining` are real recurring essential spend
(500+ raw rows each across the 25 sample users) but almost never form a
clean per-description calendar cadence (many small purchases rotate
through many differently-named line items). Two average-rate fallbacks
were built and measured:

1. Per-(category, description) leftover groups, aggregated by category.
2. Whole-category historical average (ignoring per-description grouping
   entirely), using the category's full historical span.

Both were net-negative on the 25-sample scorer: they fixed the users who
needed more essential drag (e.g. request_23, request_06) but badly
overshot users who need ~none (e.g. request_01 went from an exact match
to `not_affordable`). Reverted. This is the largest remaining gap behind
`amount_safe_to_pay`'s low ceiling — see error_analysis.md.

## 8. Phase 4: perception on vs off (PRD.md gate — measured)

Ran `code/evaluation/main.py` on the 25 samples three ways:

| Field | Phase 3 (no perception) | Perception ON (VLM + messages + LLM explanations) | Message amendments OFF (VLM + explanations only) |
|---|---|---|---|
| `amount_safe_to_pay` | 4/25 (16%) | 4/25 (16%) | 4/25 (16%) |
| `affordability_status` | 19/25 (76%) | 19/25 (76%) | 19/25 (76%) |
| `recommended_payment_method` | 21/25 (84%) | 21/25 (84%) | 21/25 (84%) |
| `payment_plan` | 18/25 (72%) | 18/25 (72%) | 18/25 (72%) |
| `earliest_date_for_full_payment` | 14/25 (56%) | 14/25 (56%) | 14/25 (56%) |
| `spending_changes_needed` | 22/25 (88%) | 22/25 (88%) | 22/25 (88%) |

**Decision: perception stays on** (`config.yaml`'s
`perception.enable_message_amendments: true`, `--no-vlm`/`--no-llm`
default off). Gate satisfied (≥ Phase 3 on every field — here, exactly
equal, not worse). Two things worth being honest about rather than
claiming a win that didn't happen:

- **VLM is verified correct but doesn't move the 25-sample score.** All
  16 blank amounts were extracted and hand-checked exact against their
  source PNGs (see error_analysis.md). Of the 25 samples, 5
  (request_03/16/17/19/20) have a user whose blank event now resolves via
  VLM instead of the Phase 1 placeholder — confirmed directly
  (`event_1700` for request_19 now reads `vlm_extraction:gemini` /
  `2854.0` in the exclusion log, not
  `blank_amount_conservative_placeholder`). None of those 5 changed
  status/method/plan, because the specific resolved event isn't the
  binding constraint for that request's forecast window. This is a
  correct, verified fix that happens not to be visible on this
  particular 25-row sample — it should still help on the full 250, where
  more of the 16 images' events are load-bearing for their request.
- **Message amendments measured genuinely neutral, not just untested.**
  Ran with `use_message_amendments=True` vs `False` (decoupled from
  `--no-llm`, which also controls explanations) — byte-identical scores.
  One amendment found by hand-reviewing `message_no_ops.jsonl` looks like
  a real miss: user_02's salary-increase message ("Gaji bulanan Anda naik
  menjadi IDR 42750000") was classified `amend_amount` at confidence 0.62
  in one isolated call but `no_op` at confidence 0.2 in the batched run
  against the *same* input — non-deterministic despite `temperature=0`,
  a known characteristic of some inference stacks under batching. Either
  way it lands below or barely above the 0.6 acceptance threshold, so
  it's dropped either way in this run. Not chasing this further given
  the time budget; noted as a real limitation rather than hidden.
- **Explanation generation hit a real GROQ daily token quota** (200,000
  TPD for `openai/gpt-oss-120b`) after message classification alone spent
  200,864 tokens on 142 users. 249/250 rows in the full run fell back to
  the Phase 1 template — verified as graceful, not broken (every row's
  `decision_explanation` is non-empty and passes INV-12), and irrelevant
  to the scored fields (`decision_explanation` isn't exact-matched). See
  `usage_report.md` for the full accounting.

## 9. `recurring.average_rate_fraction`: calibrated, not flat (resolves the §7 open gap) — SUPERSEDED, see §11

**This mechanism no longer exists in the code.** §11's grid search
replaced `average_rate_fraction` with a closed `amount_rule` x
`occurrence_rule` taxonomy per the later task that specified it exactly.
That taxonomy has no equivalent of this section's fraction-scaled *daily
drip* (a category-wide event stepping one day at a time, amount scaled
by a fraction of the historical daily rate) — the grid's three
occurrence rules are `observed_cadence` / `weekly_anchored` /
`category_level_cadence`, none of which reproduce a daily step. Measured
head-to-head on the same three fields §11 scores: this section's
`average_rate_fraction: 0.15` scored 4+16+19=39/75; §11's actual grid
winner (`observed_cadence`) scores 36/75. Kept below as the historical
record of how 0.15 was found — not as the shipped config, which now
follows §11.

Followed up on §7's finding. Instead of a flat whole-category historical
average, scaled it by a fraction and swept 0.0 → 1.0 against the 25
samples (`use_vlm=False, use_llm=False, use_message_amendments=False` to
isolate this one variable; `code/ledger/canonical.py`'s
`average_rate_fraction` parameter, `config.yaml`'s
`recurring.average_rate_fraction`):

| Fraction | `amount_safe_to_pay` | `affordability_status` | `recommended_payment_method` | `payment_plan` | `earliest_date_for_full_payment` | `spending_changes_needed` |
|---|---|---|---|---|---|---|
| 0.00 (off) | 4 | 19 | 21 | 18 | 14 | 22 |
| 0.05 | 4 | 19 | 21 | 18 | 14 | 22 |
| 0.10 | 4 | 19 | 21 | 19 | 15 | 22 |
| **0.15** | **4** | **19** | **21** | **19** | **16** | **22** |
| 0.20 | 4 | 19 | 21 | 19 | 16 | 22 |
| 0.25 | 3 | 18 | 21 | 19 | 15 | 21 |
| 0.30 | 3 | 17 | 19 | 17 | 14 | 21 |
| 0.40 | 3 | 18 | 19 | 17 | 15 | 23 |
| 0.50 | 3 | 17 | 18 | 16 | 15 | 22 |
| 0.60 | 3 | 17 | 18 | 16 | 16 | 22 |
| 0.70 | 2 | 15 | 16 | 15 | 16 | 22 |
| 0.80 | 1 | 16 | 17 | 16 | 15 | 21 |
| 1.00 (flat average) | 1 | 14 | 14 | 13 | 14 | 21 |

**Decision: `average_rate_fraction: 0.15`.** Plateaus at 0.15-0.2
(identical scores), improving `payment_plan` (18→19) and
`earliest_date_for_full_payment` (14→16) with zero regression on the
other four fields, then degrades monotonically past 0.25 as the same
overshoot problem from §7 returns at a smaller scale. Picked 0.15 over
0.2 (identical on these 25 rows) for a small conservative margin against
the untested 250. Re-verified with full perception on
(`code/evaluation/main.py`): `payment_plan` 72%→76%,
`earliest_date_for_full_payment` 56%→64%, all other fields unchanged.

Also re-checked the adjacent one-line decision this follow-up depends on:
the *conservative* amount for a clean-cadence recurring series is the
historical **maximum**, not median or last-observed. Swept with
`average_rate_fraction=0.15` fixed: max → `payment_plan` 19, `earliest`
16 (current); median → 19, 15; last-observed → 18, 14. Max confirmed
best, kept as-is.

## 10. Component-isolated perception ablations (Phase 5)

**Note (added after §11): these numbers are from the now-superseded
`average_rate_fraction=0.15` baseline (§9).** With §11's grid-search
default (`observed_cadence`), the "everything on" row is now
4/19/21/**18**/**14**/22 (`payment_plan` 76%→72%, `earliest_date_for_
full_payment` 64%→56% — re-verified directly against the current
config). The *relative* finding this section exists to state — every
perception component scores identically on/off — still holds under the
new default too (re-verified); only the absolute baseline numbers moved
with §11's mechanism swap. Left as the historical record rather than
rewritten in place.

Rounding out §8's on/off measurement with each component isolated (all
against the calibrated `average_rate_fraction=0.15` baseline, which
alone scores 4/19/21/19/16/22 with everything off):

| Configuration | `amount_safe_to_pay` | `affordability_status` | `recommended_payment_method` | `payment_plan` | `earliest_date_for_full_payment` | `spending_changes_needed` |
|---|---|---|---|---|---|---|
| Everything off (arithmetic core only) | 4 | 19 | 21 | 19 | 16 | 22 |
| VLM only (`use_vlm=True`, rest off) | 4 | 19 | 21 | 19 | 16 | 22 |
| Messages only (§8, `use_message_amendments=True`, rest off) | 4 | 19 | 21 | 19 | 16 | 22 |
| Template-only (`use_vlm=True, use_llm=False`) | 4 | 19 | 21 | 19 | 16 | 22 |
| Everything on (VLM + messages + LLM explanations) | 4 | 19 | 21 | 19 | 16 | 22 |

Every configuration scores identically on the 25 samples. This is the
honest result, not a wiring bug: `amount_safe_to_pay`'s ceiling is set by
the arithmetic core (§7's groceries/transport/dining gap, now partially
addressed by §9's calibration), the two currently-scored fields VLM
touches on these 25 rows (request_19's `event_1700`, etc.) aren't the
binding constraint for their requests, and `decision_explanation` isn't
exact-matched at all so template vs LLM is invisible to the scorer by
construction. Perception stays on for the full 250-row submission
because it's individually verified correct (16/16 images hand-checked,
injection boundary tested) and may matter more where an image or message
happens to be load-bearing outside this particular 25-row sample.
## 11. `code/evaluation/fit.py` grid search: variable-expense amount_rule x occurrence_rule

Grid: every combination of amount_rule in {last, mean, median, max, p75, mean_plus_k_stdev} (k_stdev=1.0) x occurrence_rule in {observed_cadence, weekly_anchored, category_level_cadence}, applied UNIFORMLY to groceries/transport/dining (18 points -- see fit.py's own docstring for why the fully independent per-category grid, 5,832 points, wasn't run: hours of pipeline passes vs minutes). Scored on exact match against all 25 labelled samples for the three fields that most directly reflect the forecast (amount_safe_to_pay, earliest_date_for_full_payment, payment_plan). No request_id is special-cased anywhere in fit.py or canonical.py -- every combination runs the identical code path for all 25 requests.

Top 10 by total exact matches (amount_safe_to_pay + earliest_date_for_full_payment + payment_plan, ties broken by amount_safe_to_pay then earliest):

| Rank | amount_rule | occurrence_rule | amount_safe_to_pay | earliest_date_for_full_payment | payment_plan | total | Full config |
|---|---|---|---|---|---|---|---|
| 1 | `last` | `observed_cadence` | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: last, occurrence_rule: observed_cadence}
  transport: {amount_rule: last, occurrence_rule: observed_cadence}
  dining: {amount_rule: last, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 2 | `mean` | `observed_cadence` | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: mean, occurrence_rule: observed_cadence}
  transport: {amount_rule: mean, occurrence_rule: observed_cadence}
  dining: {amount_rule: mean, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 3 | `median` | `observed_cadence` | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: median, occurrence_rule: observed_cadence}
  transport: {amount_rule: median, occurrence_rule: observed_cadence}
  dining: {amount_rule: median, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 4 | `max` | `observed_cadence` | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: max, occurrence_rule: observed_cadence}
  transport: {amount_rule: max, occurrence_rule: observed_cadence}
  dining: {amount_rule: max, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 5 | `p75` | `observed_cadence` | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: p75, occurrence_rule: observed_cadence}
  transport: {amount_rule: p75, occurrence_rule: observed_cadence}
  dining: {amount_rule: p75, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 6 | `mean_plus_k_stdev` | `observed_cadence` | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: mean_plus_k_stdev, occurrence_rule: observed_cadence}
  transport: {amount_rule: mean_plus_k_stdev, occurrence_rule: observed_cadence}
  dining: {amount_rule: mean_plus_k_stdev, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 7 | `mean` | `category_level_cadence` | 1/25 | 17/25 | 15/25 | 33/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: mean, occurrence_rule: category_level_cadence}
  transport: {amount_rule: mean, occurrence_rule: category_level_cadence}
  dining: {amount_rule: mean, occurrence_rule: category_level_cadence}
k_stdev: 1.0
``` |
| 8 | `median` | `category_level_cadence` | 1/25 | 16/25 | 14/25 | 31/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: median, occurrence_rule: category_level_cadence}
  transport: {amount_rule: median, occurrence_rule: category_level_cadence}
  dining: {amount_rule: median, occurrence_rule: category_level_cadence}
k_stdev: 1.0
``` |
| 9 | `last` | `category_level_cadence` | 1/25 | 15/25 | 14/25 | 30/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: last, occurrence_rule: category_level_cadence}
  transport: {amount_rule: last, occurrence_rule: category_level_cadence}
  dining: {amount_rule: last, occurrence_rule: category_level_cadence}
k_stdev: 1.0
``` |
| 10 | `p75` | `category_level_cadence` | 1/25 | 13/25 | 12/25 | 26/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: p75, occurrence_rule: category_level_cadence}
  transport: {amount_rule: p75, occurrence_rule: category_level_cadence}
  dining: {amount_rule: p75, occurrence_rule: category_level_cadence}
k_stdev: 1.0
``` |
## 12. `fit.py` re-run with `daily_drip` reinstated as a fourth occurrence_rule

Roadmap review found the closed 3-rule grid's own winner (§11, `observed_cadence`, 36/75) scored WORSE than the daily-drip fraction mechanism it had replaced (§9, 39/75-equivalent) -- because daily_drip's category-wide one-day-at-a-time stepping isn't expressible in {observed_cadence, weekly_anchored, category_level_cadence}. Reinstated as a fourth occurrence_rule (`ledger/canonical.py`) and swept along its own axis (`daily_drip_fraction`, 8 points) rather than crossed with amount_rule -- daily_drip's amount is a smoothed historical daily rate, not an amount_rule pick, so amount_rule is `n/a` for these rows. Same scoring as §11: exact match against all 25 labelled samples on amount_safe_to_pay, earliest_date_for_full_payment, payment_plan. No request_id is special-cased anywhere in fit.py or canonical.py.

Top 10 across BOTH the original 18-point grid and the 8-point daily_drip sweep (26 points total), by total exact matches, ties broken by amount_safe_to_pay then earliest:

| Rank | amount_rule | occurrence_rule | daily_drip_fraction | amount_safe_to_pay | earliest_date_for_full_payment | payment_plan | total | Full config |
|---|---|---|---|---|---|---|---|---|
| 1 | `n/a` | `daily_drip` | 0.2 | 4/25 | 15/25 | 18/25 | 37/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.2}
  transport: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.2}
  dining: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.2}
k_stdev: 1.0
``` |
| 2 | `last` | `observed_cadence` | n/a | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: last, occurrence_rule: observed_cadence}
  transport: {amount_rule: last, occurrence_rule: observed_cadence}
  dining: {amount_rule: last, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 3 | `mean` | `observed_cadence` | n/a | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: mean, occurrence_rule: observed_cadence}
  transport: {amount_rule: mean, occurrence_rule: observed_cadence}
  dining: {amount_rule: mean, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 4 | `median` | `observed_cadence` | n/a | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: median, occurrence_rule: observed_cadence}
  transport: {amount_rule: median, occurrence_rule: observed_cadence}
  dining: {amount_rule: median, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 5 | `max` | `observed_cadence` | n/a | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: max, occurrence_rule: observed_cadence}
  transport: {amount_rule: max, occurrence_rule: observed_cadence}
  dining: {amount_rule: max, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 6 | `p75` | `observed_cadence` | n/a | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: p75, occurrence_rule: observed_cadence}
  transport: {amount_rule: p75, occurrence_rule: observed_cadence}
  dining: {amount_rule: p75, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 7 | `mean_plus_k_stdev` | `observed_cadence` | n/a | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: mean_plus_k_stdev, occurrence_rule: observed_cadence}
  transport: {amount_rule: mean_plus_k_stdev, occurrence_rule: observed_cadence}
  dining: {amount_rule: mean_plus_k_stdev, occurrence_rule: observed_cadence}
k_stdev: 1.0
``` |
| 8 | `n/a` | `daily_drip` | 0.1 | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.1}
  transport: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.1}
  dining: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.1}
k_stdev: 1.0
``` |
| 9 | `n/a` | `daily_drip` | 0.15 | 4/25 | 14/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.15}
  transport: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.15}
  dining: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.15}
k_stdev: 1.0
``` |
| 10 | `n/a` | `daily_drip` | 0.5 | 3/25 | 15/25 | 18/25 | 36/75 | ```yaml
variable_expense_categories: [groceries, transport, dining]
variable_expense_model:
  groceries: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.5}
  transport: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.5}
  dining: {amount_rule: max, occurrence_rule: daily_drip, daily_drip_fraction: 0.5}
k_stdev: 1.0
``` |
## 13. Per-category `daily_drip_fraction` sweep (independent, not uniform)

Roadmap review Tier-1 #1: sweep the fraction per category rather than uniformly across groceries/transport/dining. Grid: [0.05, 0.1, 0.15, 0.2, 0.3, 0.5] per category, 6^3 = 216 points, each one full pipeline pass over the 25 labelled samples via the unmodified main.decide(). Same scoring as §12: exact match on amount_safe_to_pay, earliest_date_for_full_payment, payment_plan. No request_id is special-cased anywhere in fit.py or canonical.py.

Top 15 by total exact matches, ties broken by amount_safe_to_pay then earliest:

| Rank | groceries frac | transport frac | dining frac | amount_safe_to_pay | earliest_date_for_full_payment | payment_plan | total |
|---|---|---|---|---|---|---|---|
| 1 | 0.05 | 0.3 | 0.2 | 4/25 | 15/25 | 18/25 | 37/75 |
| 2 | 0.05 | 0.3 | 0.3 | 4/25 | 15/25 | 18/25 | 37/75 |
| 3 | 0.05 | 0.5 | 0.05 | 4/25 | 15/25 | 18/25 | 37/75 |
| 4 | 0.05 | 0.5 | 0.1 | 4/25 | 15/25 | 18/25 | 37/75 |
| 5 | 0.05 | 0.5 | 0.15 | 4/25 | 15/25 | 18/25 | 37/75 |
| 6 | 0.05 | 0.5 | 0.2 | 4/25 | 15/25 | 18/25 | 37/75 |
| 7 | 0.05 | 0.5 | 0.3 | 4/25 | 15/25 | 18/25 | 37/75 |
| 8 | 0.1 | 0.1 | 0.5 | 4/25 | 15/25 | 18/25 | 37/75 |
| 9 | 0.1 | 0.15 | 0.3 | 4/25 | 15/25 | 18/25 | 37/75 |
| 10 | 0.1 | 0.15 | 0.5 | 4/25 | 15/25 | 18/25 | 37/75 |
| 11 | 0.1 | 0.2 | 0.3 | 4/25 | 15/25 | 18/25 | 37/75 |
| 12 | 0.1 | 0.3 | 0.15 | 4/25 | 15/25 | 18/25 | 37/75 |
| 13 | 0.1 | 0.3 | 0.2 | 4/25 | 15/25 | 18/25 | 37/75 |
| 14 | 0.1 | 0.3 | 0.3 | 4/25 | 15/25 | 18/25 | 37/75 |
| 15 | 0.1 | 0.5 | 0.05 | 4/25 | 15/25 | 18/25 | 37/75 |

## 14. Full-250-row distribution check: amount_rule is not the not_affordable lever

Second review named `amount_rule: max` (charging every user their historical
maximum in groceries/transport/dining, every day via `daily_drip`) as the
likely cause of the full-dataset run's elevated `not_affordable` share, and
asked for a re-sweep toward `mean`/`median` checked against distribution
shape rather than the 25-sample exact-match metric alone. Ran a diagnostic
(scratchpad `distro_sweep.py`, not shipped -- reuses `main.decide()`
unmodified over the full 250-row `requests.csv`, bypassing perception
entirely so it isolates the ledger/candidates path) across five configs:

| Config | not_affordable | affordable_now | affordable_with_plan | affordable_later | spending_changes != none |
|---|---|---|---|---|---|
| `max` + `daily_drip` (shipped) | 96 (38%) | 58 (23%) | 53 (21%) | 43 (17%) | 1/250 (0.4%) |
| `mean` + `daily_drip`, same fraction | 96 (38%) | 58 (23%) | 53 (21%) | 43 (17%) | 1/250 (0.4%) |
| `median` + `daily_drip`, same fraction | 96 (38%) | 58 (23%) | 53 (21%) | 43 (17%) | 1/250 (0.4%) |
| `mean` + `observed_cadence` (no drip at all) | 90 (36%) | 62 (25%) | 55 (22%) | 43 (17%) | 0/250 (0.0%) |
| `median` + `observed_cadence` (no drip at all) | 90 (36%) | 62 (25%) | 55 (22%) | 43 (17%) | 0/250 (0.0%) |

`max`, `mean`, and `median` produce byte-identical status/method
distributions with `daily_drip` active, and switching the occurrence rule
off entirely (no variable-expense aggregate at all) only moves
`not_affordable` from 38% to 36% -- a 2-point swing. **The variable-expense
amount_rule is not the lever on not_affordable share**; the `config.yaml`
comment that named `amount_rule: max` as the likely cause was itself an
unverified guess written in response to a review's claim, not a measured
finding -- corrected here and in `config.yaml` once this diagnostic ran.

A second review separately reported 52% (130/250) `not_affordable` on a
full-dataset `output.csv`. This diagnostic (perception bypassed
entirely -- no VLM extraction for the 16 blank-amount rows, no message-
amendment application) instead measures 38%, and a real `--no-vlm --no-llm`
full run measures 34% (85/250) -- both close to each other, neither close
to 52%. Two live variables separate these numbers from the 52% figure: (1)
that figure came from an `output.csv` generated before this session's
`engine/simulate.py` feasibility fix (a candidate no longer gets rejected
over a pre-existing baseline dip strictly before its own payment date --
see log.txt/AGENTS.md for that fix; it demotes fewer candidates to
`not_recommended`, so it should only ever LOWER the not_affordable share,
not raise it), and (2) neither this diagnostic nor the `--no-vlm --no-llm`
run applies message amendments (`ledger/amendments.py::apply_amendments`,
which can cancel or amend any allowlisted event per user; roughly
199/250+ users have message evidence). Re-measure `not_affordable` share
on a genuine perception-on run against CURRENT code before treating 52% as
still accurate -- it predates a fix that should move it, and no comparable
current measurement with amendments applied yet exists.

Distribution reference (roadmap doc §0): the 25 labelled samples run 7/25
(28%) `not_affordable`. The gap between that and the full run's actual
share is real and only partially explained by the samples being a small,
possibly non-representative slice of 250 -- worth keeping open rather than
closed by a wrong (amount_rule) or right-but-small (drip on/off) lever.
