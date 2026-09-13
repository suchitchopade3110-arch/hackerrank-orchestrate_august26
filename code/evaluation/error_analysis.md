# Error analysis — Phase 2/3/4 checkpoint

Measured with `code/evaluation/main.py` against `dataset/sample_requests.csv`
(25 labelled rows), income.mode=recurring_projected (see ablations.md).
Numbers below are unchanged by Phase 4 perception (measured, see
ablations.md §8) — Phase 2's root-cause analysis still stands as the
main driver of remaining error; Phase 4 added a verified-correct but
not-yet-score-moving perception layer on top.

## Current per-field accuracy

| Field | Accuracy |
|---|---|
| `amount_safe_to_pay` | 4/25 (16%) |
| `affordability_status` | 19/25 (76%) |
| `recommended_payment_method` | 21/25 (84%) |
| `payment_plan` | 18/25 (72%) |
| `earliest_date_for_full_payment` | 14/25 (56%) |
| `spending_changes_needed` | 22/25 (88%) |

## Gate criteria status (PRD.md Phase 2 gate)

- ✅ Every installment plan string on the samples reproduced character for
  character from its option row (5/5: request_02, 07, 12, 17, 22).
- ✅ `output.format_by_currency` reproduces all 25 sample label strings
  exactly (both `amount_safe_to_pay` and `payment_plan` formatting rules).
- ✅ `request_12` comes out `installments` (profile rejects `full_payment`) —
  matches exactly, including `amount_safe_to_pay == requested_amount`.
- ❌ `request_06` does **not** come out `full_payment` + `stop:event_476` +
  `affordable_with_plan`. We get `full_payment` + `affordable_now` (no
  changes) — our baseline headroom (681.52) already clears the requested
  amount (620.40), while ground truth's baseline is tighter (603.30),
  which is exactly the missing groceries/transport/dining drag documented
  in ablations.md. The changes-mechanism itself (reductions.py, the
  eligibility gate, candidate crossing) is implemented and exercised by
  unit-level checks, but never fires on any of the 25 samples because our
  baseline is systematically a bit too generous. This is the one named
  regression fixture still failing.
- ❌ `amount_safe_to_pay` and `earliest_date_for_full_payment` are **not**
  yet at or near ceiling (16% / 56%). Root cause below.
- ⚠️ Three `reduce_to`/`stop` sample rows (request_06, request_11,
  request_21) are not yet reproduced — same root cause.

## Root cause, ranked by leverage

1. **Groceries/transport/dining under-representation** (see ablations.md
   §"Known gap"). This is the dominant source of `amount_safe_to_pay`
   error: our forecast is almost always a bit more optimistic than ground
   truth because ~500 raw transactions per category per user, across
   many rotating descriptions, never form a clean per-description cadence
   and get dropped. Two aggregate fallbacks were tried and both
   overshot in the opposite direction on low-spend users. The fix likely
   needs either (a) message-based amendments providing an explicit budget
   signal (Phase 8), or (b) a per-user-calibrated fraction of historical
   average rather than a flat rate, tuned against more than 2-3 discriminating
   samples (the 25-sample set is thin for this specific parameter).
2. **Multi-income-stream narratives** (job changes, "before/after leave",
   secondary earners) — partially fixed: recurring series are now deduped
   to the most-recently-anchored one, and a "Final employer payroll"-style
   termination marker suppresses further projection (fixed request_05).
   Not fixed: a "New employer payroll" or similar continuation marker
   doesn't yet re-anchor the series to the new job (would need the
   opposite of the termination-suppression logic). No sample currently
   exercises this specific gap, so it's unmeasured, not unfixed-and-known-bad.
3. **`amount_safe_to_pay`'s exactness**: because it's the minimum headroom
   over a 91-date window, it is far more sensitive to small forecast
   errors than `affordability_status`/`recommended_payment_method` (which
   only need to land on the correct side of a threshold). A 78-unit
   forecast error changes `amount_safe_to_pay` on every affected row but
   only flips `affordability_status` when it crosses the requested amount
   or the deadline. This is why status/method accuracy (76-84%) is much
   higher than the amount field (16%) even though the underlying forecast
   error is the same root cause.

## What's confirmed correct (worth stating plainly)

- FX resolution: 0 unresolved pairs across all 25,342 events (Phase 1 gate).
- Ledger exclusions (pending_credit/failed/cancelled/unrealized/non_cash)
  and linked-chain refund netting/supersession: exercised on every request,
  logged with reason codes, no assertion failures across 250 requests.
- `max_installment_months` hard filter: request_02's 15-payment option
  (14mo span) is correctly excluded where the profile's cap requires it,
  request_07's 15-payment option (14mo span, cap=12) also correctly excluded.
- Installment schedule construction: verbatim copy from option columns,
  sum-to-`total_payable_amount` assertion never fails across 275 requests'
  worth of options.
- `income.mode` resolution: `recurring_projected` measurably beats
  `confirmed_only` by a wide margin (see ablations.md), not assumed.

## Phase 4: perception layer, verified correct

- **All 16 blank-amount images extracted via VLM and hand-checked exact
  against the source PNGs** (`dataset/media/images/image_01.png`
  through `image_16.png`). 14 were unambiguous exact matches; 2
  (`image_02` a rent receipt, `image_04` a grocery delivery order) had a
  genuinely ambiguous "which total" reading and the model's choice
  (amount actually received/charged, not a larger invoiced-but-unpaid
  figure) is defensible. Zero fell through to the conservative
  placeholder — the audit flag list is empty, as the roadmap doc
  predicted for a set this size.
- One real FX bug found and fixed via this process: `image_12` (a
  CityCab USD receipt for an INR-home user) was initially treated as
  already in home currency. Fixed to route the extracted amount through
  the same FX table every other amount uses, keyed on the event's own
  `currency_original` and settlement date — $33.50 → ₹2,791.555 at the
  dataset's USD→INR rate.
- One plausibility-check bug found and fixed: the initial 0.1x-10x band
  around comparable same-category amounts rejected two exactly-correct
  high-confidence extractions (`image_09` ₹723 water bill, `image_10`
  ₹79,679.26 grocery invoice) as "implausible" purely on magnitude.
  Widened to 0.01x-100x — a real amount that's simply larger or smaller
  than a user's other purchases in the category shouldn't be
  second-guessed by a tight band; the check now only exists to catch a
  wildly wrong OCR digit, not to override a high-confidence correct read.
- Message classification: verified against one live adversarial fixture
  (a message attempting direct prompt injection into output fields) —
  classified `no_op`, confidence 0.1, zero accepted amendments. See
  `code/tests/test_injection.py`.
- None of this moved the 25-sample score (see ablations.md §8) — the
  specific events/users affected aren't the binding constraint for those
  25 requests, and the message amendments found were either correctly
  non-actionable (pending prize claims, unrealized portfolio gains) or
  landed just below/at the 0.6 confidence threshold. Phase 2's root
  causes below remain the actual score ceiling.

## Next steps if more time is available

1. Try calibrating the groceries/transport/dining average rate as a
   *fraction* (e.g. 30-50%) of the flat historical average, sweeping the
   fraction against the 25 samples to find the value that minimizes
   `amount_safe_to_pay` error without flipping any sample's status.
2. Re-run message classification on a fresh GROQ TPD quota window and
   check whether any samples flip once amendments aren't confidence-
   capped by the same-call non-determinism noted in ablations.md §8 (a
   second, independent call might land above 0.6 where the first didn't).
3. The `--limit`/full-250 run now has genuinely productive perception
   (16/16 images resolved); worth a fresh full-250 sanity distribution
   check (Phase 5) now that blank amounts are real numbers, not
   placeholders, for the events that matter to those requests.
