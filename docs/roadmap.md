# roadmap.md

Companion to PRD.md and design.md. This is the when and where: local execution order, files touched per phase, and the gate that has to pass before the next phase starts.

Target repo: github.com/interviewstreet/hackerrank-orchestrate-september26 (clone, work on a branch, never modify dataset/).

## 0. Schema facts that change the design docs

I probed the real starter repo. Six things in PRD.md/design.md are now either closed or wrong, and they change the build order. Fix the docs before writing code.

| # | Finding | Consequence |
|---|---|---|
| 1 | `request_payment_options.csv` has `payment_amount` (per-payment), `number_of_payments`, `first_payment_date`, `payment_frequency_days`, `financing_fee`, `total_payable_amount` | design.md decision 6 is closed: no split convention. Dates are `first_payment_date + k * payment_frequency_days`; amount is `payment_amount` repeated verbatim. `installments.split` config key is deleted, not measured. Verified on request_02, request_12, request_19 against their sample labels. |
| 2 | `financial_profiles.csv` has `max_installment_months`, blank when the user will not consider installments | New hard filter on installment candidates, absent from both docs. Blank ⇒ drop every installment option. Non-blank ⇒ drop options where the schedule runs past the cap. 119 of 275 users are blank. |
| 3 | Spending changes are gated by two keys: event `flexibility` ∈ {fixed, reducible, stoppable, reducible_or_stoppable} and the profile's `expense_categories_to_protect` / `..._willing_to_reduce` / `..._willing_to_stop` | design.md §8 "restricted to recurring_flexible events" is too loose. `stop` needs `stoppable` and category in the stop list and category not protected. `reduce_to` needs `reducible` and the reduce list. |
| 4 | `financial_events.csv` has `minimum_allowed_amount` (populated on 2,907 rows) | `reduce_to` is a clamped closed form, not the free formula in design.md §8: `reduce_to = max(minimum_allowed_amount, current − ceil(D/k))`, and if that still doesn't clear the deficit the candidate is infeasible rather than degenerating to `stop`. |
| 5 | `sample_requests.csv` is request_01–request_25; `requests.csv` is request_26–request_275. Disjoint. `dataset/output.csv` is the template, pre-seeded with the 250 IDs and empty fields | Sample scoring never leaks into the run. The template gives the exact header, quoting, and row order — read the order from it, don't sort. |
| 6 | Exactly 16 blank amount rows, exactly 16 images, one-to-one via `images.csv.related_event_id` | The mandatory VLM path is 16 calls total, not a bulk perception layer. It can be hand-audited in full, so it moves to Phase 4 with confidence and never becomes a time sink. |

Other confirmations worth having in hand: statuses are `settled` / `pending` / `scheduled` / `cancelled` / `failed` / `unrealized`; direction is `debit` / `credit` / `non_cash`; FX rows are matched on settlement date with the stated `from_currency` → `to_currency` direction (AGENTS.md §6.1); `event_type` includes `investment_purchase`, `investment_valuation`, `investment_sale`, `refund`, `debt_payment`, `subscription`.

Sample label distribution, which is the yardstick for the Phase 5 sanity check: statuses 9 `affordable_with_plan`, 7 `not_affordable`, 6 `affordable_later`, 3 `affordable_now`; methods 7 `not_recommended`, 6 `full_payment`, 6 `wait`, 5 `installments`, 1 `partial_payment`; 7 of 25 rows have an empty `earliest_date_for_full_payment`; 3 of 25 carry spending changes.

Two formatting rules read off the sample labels, to be locked by measurement in Phase 2:

`amount_safe_to_pay` — value rounded down at 2dp with trailing zeros stripped, the same in every currency (25256, 603.3, 87170.56, 12510645).
`payment_plan` amounts — installment rows copy `payment_amount` verbatim; `full_payment` / `wait` / `partial_payment` rows format the amount per currency, 2dp for EUR and USD (2026-01-03:620.40), 0dp for INR, ZAR, IDR (2024-03-03:25256).

Also note request_12: `amount_safe_to_pay == requested_amount` and the recommendation is still `installments`, because the profile doesn't accept `full_payment`. And request_06: `full_payment` with a `stop:` change, status `affordable_with_plan`, earliest after `request_date` — which confirms earliest is computed without spending changes and that `affordable_now` is reserved for the no-changes case.

## 1. Local setup (before Phase 1, ~20 min)

```bash
git clone https://github.com/interviewstreet/hackerrank-orchestrate-september26.git
cd hackerrank-orchestrate-september26
git checkout -b buy-or-wait

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # created in this step

cp .env.example .env                   # add GROQ_API_KEY, optional GEMINI_API_KEY
touch log.txt
```

`.gitignore` additions: `.venv/`, `__pycache__/`, `.cache/`, `.env`, `log.txt`, `code/audit/`, `output.csv`.

Rules for the whole build: `dataset/` is read-only, `code/main.py` stays the single entry point, `code/evaluation/main.py` stays the scorer entry point (both exist as empty stubs in the starter — grow them, don't relocate them), and `log.txt` gets a SESSION START entry plus one entry per turn per AGENTS.md §5.

### Repo structure to maintain

```
.                                  # starter files: do not move or rewrite
├── AGENTS.md                      # untouched
├── CLAUDE.md                      # untouched
├── README.md                      # untouched (my README goes in code/)
├── problem_statement.md           # untouched
├── log.txt                        # appended every turn, gitignored, submitted
├── output.csv                      ← Phase 1 writes this (root is the graded path)
├── requirements.txt                ← Phase 1
├── .env.example                    ← Phase 1
├── dataset/                       # READ ONLY, except dataset/output.csv is the template
│   └── … nine inputs + media/images/
└── code/
    ├── main.py                    # starter stub → CLI entry
    ├── config.yaml
    ├── README.md                  # setup + design summary for the zip
    ├── io_layer/     loader.py  fx.py  probe.py  template.py
    ├── ledger/       canonical.py  precedence.py  classify.py  recurrence.py
    ├── engine/       forecast.py  closed_form.py  candidates.py  reductions.py
    │                 simulate.py  rank.py  status.py  options.py
    ├── perception/   vlm.py  messages.py  schemas.py  cache.py
    ├── explain/      factsheet.py  generate.py  templates.py
    ├── verify/       invariants.py
    ├── writer.py
    ├── audit/                     # per-request JSON traces, gitignored, not shipped
    ├── tests/                     # pytest, Phase 5 but written throughout
    └── evaluation/
        ├── main.py                # starter stub → per-field scorer on the 25 samples
        ├── sanity.py              # unlabelled distribution check on the 250
        ├── usage_report.md        # starter stub → filled from the final run
        ├── error_analysis.md
        └── ablations.md
```

`code/evaluation/` is the path the README asks for in the zip, and the starter already scaffolds `code/evaluation/main.py` and `code/evaluation/usage_report.md`. Keep those two filenames exactly.
