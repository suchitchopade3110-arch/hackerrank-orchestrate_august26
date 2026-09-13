# code/ — Buy or Wait affordability agent

See repo-root `README.md` for the challenge overview. This file documents
setup and design for the submission zip; filled in as phases land.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add GROQ_API_KEY, optional GEMINI_API_KEY
```

## Running

```bash
python code/io_layer/probe.py            # print real columns and distributions
python code/main.py --limit 25 --samples # run the solved samples
python code/evaluation/main.py           # per-field accuracy
python code/main.py                      # full run, writes root output.csv
python code/evaluation/sanity.py         # unlabelled distribution check on the 250
```

Useful flags on `code/main.py`: `--no-vlm` skips image extraction (blank
amounts fall back to the conservative same-category-max placeholder,
flagged in the audit trace); `--no-llm` skips both message classification
and LLM explanations (deterministic templates only); `--request-id ID`
runs one request with a verbose trace; `--limit N` caps the run.

Both flags are for real: `python code/main.py --no-vlm --no-llm` still
produces a complete, valid `output.csv` with zero model calls.

## Perception (Phase 4)

Three model call sites, each with a deterministic fallback -- the
arithmetic core never depends on a model call succeeding:

1. **Image amount extraction** (`perception/vlm.py`) -- the 16 blank-amount
   events. Ladder: primary VLM provider with a plausibility check, then a
   second provider, then the conservative placeholder (flagged in the
   audit trace). On this build's API keys, GROQ carries no vision-capable
   model (verified via `client.models.list()`), so Gemini is primary and
   GROQ is the (currently inert) fallback -- provider order is a
   `config.yaml` list, not a code change, so this activates automatically
   if that changes. All 16 images were eyeballed by hand against the
   source PNGs; the flag list is empty (all 16 resolved via VLM, none fell
   through to the placeholder).
2. **Message-to-amendment classification** (`perception/messages.py` +
   `perception/schemas.py`) -- batched per user, validated against a
   closed pydantic schema over that user's own event-ID allowlist. A
   message's embedded instructions cannot alter any output field: the
   schema has no slot for one, `extra="forbid"` rejects anything else
   outright, and `code/tests/test_injection.py` verifies this both
   structurally and against one live adversarial fixture message.
   Individually config-flaggable (`perception.enable_message_amendments`
   in `config.yaml`) separately from explanation generation, per the
   ablation in `evaluation/ablations.md`.
3. **Explanation generation** (`explain/factsheet.py` + `generate.py`) --
   batched 10-20 fact sheets per call, JSON object keyed by `request_id`
   (never a positional array), key-set validated, one retry on a
   grounding failure (INV-12), then the Phase 1 deterministic template.

`GROQ_API_KEY` (required for message classification and explanations) and
`GEMINI_API_KEY` (used for image extraction) go in `.env` -- never commit
them; `.env` is gitignored. Both are read via `python-dotenv`, loaded at
the top of `code/main.py`.

## Testing

```bash
pytest code/tests -q
```

~90 tests across recurrence, FX, ledger exclusions/linking, the
per-(user_id, request_date) lookahead-leak guard, closed-form arithmetic,
installment schedule expansion, closed-form reductions, candidate
eligibility (including the request_12 regression fixture), ranking, one
test per INV code plus the INV-16/INV-17 fixture rows, the injection
boundary (structural + one live adversarial message), the deterministic
core's byte-identical rerun, and writer formatting against the sample
labels. A handful of tests that exercise the full CSV-backed pipeline are
slow (each reloads and FX-converts all 25,342 events) rather than flaky;
budget a couple of minutes for the full suite, not seconds.

## Full run, verification, and packaging

```bash
pytest code/tests -q
python code/main.py --samples && python code/evaluation/main.py   # final per-field score
python code/main.py                                               # full 250
python code/evaluation/sanity.py                                  # distribution check
python code/main.py                                               # warm-cache rerun
git diff --stat output.csv                                        # -> byte-identical, no diff
```

## Design summary and agentic structure

Nine-stage deterministic pipeline (L0-L8), three bounded model call sites
(image amount extraction, message-to-amendment classification, explanation
generation). See `evaluation/usage_report.md` for token/cost accounting on
the final run.

No graph framework (LangGraph etc.) is used, deliberately. The control
flow is a fixed pipeline with one bounded repair loop -- there's no
dynamic routing or an agent deciding what to do next, because the problem
doesn't need one: it needs a correct simulator with two perception hooks.
That said, the brief does ask for an "AI agent," so the agentic structure
is named explicitly rather than left for a reviewer to infer from its
absence:

- **The sensing loop** is L1 perception (`perception/vlm.py`,
  `perception/messages.py`) -- the agent's two points of contact with
  unstructured reality (document images, free-text messages), each
  behind a closed, validated schema before anything downstream sees it.
- **The decision loop** is L6 (`verify/invariants.py`,
  `main.py::select_with_repair`) -- verify the top-ranked candidate from
  scratch, demote and re-verify on failure, bounded at two repairs, then
  the safe fallback. This is the part of the system that behaves like an
  agent deciding what to do when its first answer turns out to be wrong,
  not a framework abstraction sitting on top of plain function calls.
- **The audit trace** (`code/audit/<request_id>.json`) is the agent's
  working memory: inputs, exclusions with reasons, the binding
  constraint and its headroom, every candidate considered and why it
  won or lost, every invariant result, every demotion. A wrong answer is
  debuggable from this file in under a minute without re-deriving the
  whole pipeline by hand.

What's absent is a graph *framework*, not agentic structure -- the
engineering argument (a correct simulator needs no framework) and the
vocabulary above both hold at once.

## Problems found and fixed (two independent review passes)

The pipeline above is the end state. It got there through two rounds of
external code review against a working submission, each surfacing real
defects a self-review had missed. Recorded here with the actual root cause
and fix, not just the headline, because the debugging is as much a part of
this submission as the arithmetic:

**Spending changes only ever fired on ~1 event, never a combination.**
`engine/candidates.py`'s subset builder sized every `stop`/`reduce_to`
change to close the *entire* deficit on its own, so a 3-event combination
was only reachable if one single event alone could close the gap --
`spending_changes.max_changes: 3` was effectively `1`. Fixed by
`engine/reductions.py::build_subset_changes`, which splits the remaining
deficit across a subset's members instead of re-solving the whole deficit
per member, and by enumerating `stop` and `reduce_to` as sibling
alternatives for the same event rather than a fallback chain
(`candidates.py::_candidate_change_slots`).

**Partial-payment legs didn't sum to the requested amount.** Each leg was
floored to display precision independently -- for a 0dp currency this can
under-sum by 1 unit (`42234.73`/`7145.27` independently floored to
`42234`/`7145`, summing to `49379` instead of the true `49380`, a direct
INV-04/spec-text violation on 3/5 partial-payment rows). Fixed in
`writer.py::partial_payment_legs`: quantize leg 1 down, then derive leg 2
as the exact remainder against the full-precision total -- never re-round
leg 2 independently. `decision_explanation` used to call the same
computation a second time with its own independent floor, landing one unit
below the plan's leg 2 (`request_273`: plan said `...5444849`, its own
explanation said `5,444,848`) -- fixed by having `main.py` reuse the one
`partial_payment_legs` result for both the plan string and the
explanation, so the two fields can never disagree again.

**A synthetic event's own model hyperparameters leaked into
`decision_explanation`.** The category-level variable-expense aggregate
event (`ledger/canonical.py`) was labelled with its own config, e.g.
`"groceries (daily_drip, fraction=0.2)"` -- and that label flowed straight
into the explanation whenever that event was the binding constraint (~103
rows citing an internal knob to the end user, e.g. *"...the groceries
(daily_drip, fraction=0.2) payment (due 2024-06-14) is the tightest
point"*). Fixed by giving the synthetic event a human label (`"everyday
groceries"`); the modelling choice stays fully recoverable from
`provenance` + `config.yaml` for anyone auditing the run, it just doesn't
belong in a sentence a user reads.

**`not_recommended` rows could still name a safe full-payment date --
reading as a contradiction.** `earliest_date_for_full_payment` is
capacity-only by design (`engine/closed_form.py`) and gets written
unconditionally, while the "wait" candidate that would *use* that date is
only enumerated when `full_payment` is an accepted payment method. Two
distinct bugs produced the same visible symptom:
1. `engine/simulate.py`'s feasibility check scanned every day from
   `request_date` for a minimum-balance breach, including days *before* a
   candidate's own payment date. A pre-existing baseline dip that has
   nothing to do with the decision (essential spending alone, unrelated to
   this request) could reject an otherwise-safe `wait` candidate. Fixed by
   only counting a breach on/after the candidate's own earliest payment
   date. Confirmed concretely on `request_31`: the baseline dips for 11
   days from 2024-10-04 (unrelated pre-existing fact), recovers with large
   headroom by 2024-11-15 -- `closed_form` correctly named 2024-11-15 as
   safe, but the old simulator rejected the wait candidate over the
   *earlier*, irrelevant dip anyway.
2. When a user's accepted payment methods exclude `full_payment` entirely,
   no `wait` candidate is ever enumerated regardless of capacity, so the
   row falls back to `not_recommended` -- while still carrying the
   preference-blind capacity date. Every one of the 25 labelled samples'
   `not_recommended` rows carries an *empty* `earliest_date_for_full_payment`,
   so `main.py::build_row_fields` now blanks that field whenever the
   selected method is `not_recommended`, matching the ground-truth
   convention instead of leaving an unactionable capacity number attached
   to "do not proceed."

Together these fixed 13/250 rows (5.2%) that paired `not_recommended` with
a real earliest-payment date -- 6 resolved into a correct `wait`/
`affordable_later` row (bug 1), the other 6 kept `not_recommended` but with
the contradictory date now correctly blanked (bug 2).

**Config file half unread, half undocumented.** A duplicate `perception:`
key in `config.yaml` silently discarded its own first block (YAML
last-key-wins) -- `vlm_provider_order`/`vlm_confidence_threshold` were
never actually loaded despite reading like live settings. Merged the
duplicate, then swept every `load_config()` call site and either wired
every remaining key to real code (`horizon.days`, `spending_changes.
max_changes`, every `perception`/`models`/`cache` key) or moved the
genuinely closed facts that had been living there as unread "reference
only" prose (the installment split convention, the reductions formula,
output dialect/formatting, two hardcoded constants) into `docs/design.md`
§17 -- so every key left in `config.yaml` is a real, live switch.

**`usage_report.md` reported one run's numbers regardless of which run
produced them.** The token/cost totals were computed fresh each run, but
the hand-written "Notes" section hardcoded specific historical figures
("142 message calls...", "1/250 rows carry a real LLM-generated
explanation") from one past account state -- a warm-cache rerun kept
repeating those numbers even though it made zero calls of its own.
Rewrote `usage.py` so the real-vs-template explanation count is tracked
live during the run (`record_explanation_outcome`, called once per row as
`main.py` learns the outcome) and rendered into the Notes dynamically,
instead of asserted as a fact about the past. Separately increased
`explanation_batch_size` (15→25, fewer total requests against what looks
like a request-count rate limit on this account's free tier, not the
token budget) and added a bounded retry-with-backoff on `RateLimitError`
for both message classification and explanation calls -- together moving
real (non-template) explanation coverage from 1/250 to as high as
46/250 across reruns, cache-dependent.

**A plausible-sounding self-diagnosis turned out to be wrong.** After a
review named `amount_rule: max` (charging every user their historical
maximum in three categories, every day via `daily_drip`) as the likely
cause of an elevated `not_affordable` share, a full-250-row diagnostic
(`evaluation/ablations.md` §14) found `max`, `mean`, and `median` produce
*byte-identical* status distributions -- the amount_rule was never the
lever. The `config.yaml` comment repeating that unverified guess was
corrected once the sweep actually ran. The honest remaining open question
(same section): message amendments, not the variable-expense model, are
the more likely driver of the gap between a perception-off diagnostic and
a full run.
