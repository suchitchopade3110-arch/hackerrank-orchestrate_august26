# stack.md — Buy or Wait affordability agent

## 1. Choices at a glance

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Judges run `python3 code/main.py`; no build step |
| Data loading | `pandas` | Nine joined CSVs, and the schema probe is much faster to write |
| Money | `decimal.Decimal` | Exact-match scoring on IDR-scale amounts; float drift is a silent point loss |
| Validation | `pydantic` v2 | The model output schemas *are* the injection boundary |
| Config | `pyyaml` | Horizon, thresholds, model names, retry budgets out of the code |
| Orchestration | plain Python functions | See below |
| VLM | GROQ vision model, Gemini Flash fallback | Only needs an amount and a date off a bill or payroll letter |
| Text LLM | GROQ `llama-3.3-70b` class, temperature 0 | Message classification and explanation are both small, bounded tasks |
| Caching | `diskcache` | Keyed by `image_id` and by user + message-set hash; makes the dev loop seconds |
| Retries | `tenacity` | Exponential backoff on 429 and 5xx |
| Tests | `pytest` | The arithmetic core is pure and deserves real unit tests |
| Progress | `tqdm` | 250 requests, want to see it move |

## 2. No agent framework

I normally reach for LangGraph, and I am deliberately not using it here.

The control flow is a fixed nine-stage pipeline with one bounded repair loop. There is no dynamic routing, no tool selection, no agent deciding what to do next. A graph framework would add a dependency, obscure the call stack in tracebacks, and make the deterministic layers harder to unit-test — for no behaviour I don't get from function composition. The three model calls are direct HTTP through the provider SDK.

That is also the honest answer for the interview: the problem doesn't need an agent, it needs a correct simulator with two perception hooks.

The brief does say "AI agent," though, so the README names the agentic structure explicitly rather than leaving the reviewer to infer it from an absence: the L1 perception hooks are the sensing loop, the L6 verify-demote-retry cycle is the decision loop with a bounded retry budget, and the audit trace is the agent's working memory. What's absent is a graph *framework*, not agentic structure. Keep the engineering argument; supply the vocabulary alongside it.

## 3. Models

Three call sites, each with its own budget:

| Call site | Model class | Temp | Max tokens out | Batching |
|---|---|---|---|---|
| Image amount extraction | vision, GROQ Llama 4 Scout class | 0 | 128 | one image per call, cached by `image_id` |
| Message to amendments | text, 70B class | 0 | 512 | all of a user's messages in one call |
| Explanation | text, 70B class | 0 | 900 | 10 to 20 fact sheets per call |

The second vision provider (Gemini 2.0 Flash) is not optional — it is step 2 of the blank-amount ladder in `design.md` §11, retried on a cropped region before any conservative substitution. The extraction interface is one function returning `{amount, currency, date, doc_type, confidence}`, so provider order is a config list, not a code change. `usage_report.md` reports per-model and overall totals.

Explanation responses are JSON objects keyed by `request_id`, validated against the key set sent. Positional array responses are rejected outright.

Every response is parsed into a pydantic model. A parse failure is a dropped amendment or a flagged extraction, never a free-text value entering the ledger.

## 4. Dependencies

```
pandas==2.2.3
pydantic==2.9.2
pyyaml==6.0.2
python-dotenv==1.0.1
diskcache==5.6.3
tenacity==9.0.0
groq==0.13.0
tqdm==4.67.1
pytest==8.3.3
```

Pinned. Standard library handles dates (`datetime`), money (`decimal`), and CSV writing.

## 5. Environment

```bash
GROQ_API_KEY=...              # required
GEMINI_API_KEY=...            # optional, extraction fallback only
BUYORWAIT_CACHE_DIR=.cache    # optional, defaults to ./.cache
BUYORWAIT_MAX_WORKERS=8       # optional, perception concurrency
```

Read via `python-dotenv` from a local `.env` that is gitignored and excluded from the zip. No key, no default, no fallback literal anywhere in the source.

## 6. Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your key

python3 code/io_layer/probe.py            # print real columns and distributions
python3 code/main.py --limit 25 --samples # run the solved samples
python3 code/evaluation/score.py          # per-field accuracy
python3 code/main.py                      # full run, writes root + dataset/ output.csv
python3 code/evaluation/sanity.py         # unlabelled distribution check on the 250
```

Useful flags: `--no-llm` runs template explanations only, `--no-vlm` skips image extraction so the arithmetic core can be tested in isolation, `--request-id` runs one row with a verbose trace.

## 7. Determinism

- Temperature 0 everywhere, and every model response cached on disk.
- No randomness in the engine: candidate enumeration iterates sorted collections, and the ranking key is total with `payment_option_id` as the final tie-breaker.
- `Decimal` throughout the money path, quantised at the end of L3 (rounding down) rather than at write time, so partial-payment remainders are derived from the already-quantised figure and sum exactly.
- Every modelling ambiguity from `design.md` §3 is a `config.yaml` key with a recorded resolution: `income.mode`, `recurring.anchor`, `recurring.month_like_days`, `horizon.days`, `horizon.inclusive`, `horizon.enforce_outside`, `installments.split`, `output.format_by_currency`, `output.dialect`. Flipping an interpretation is a config edit plus a re-score, not a refactor.
- A second run with a warm cache produces a byte-identical `output.csv`. This is asserted in CI-style fashion by a test that runs the sample set twice and diffs.

## 8. Zip hygiene

Included: `code/` with prompts and `config.yaml`, `requirements.txt`, `.env.example`, `README.md`, `evaluation/` with the scorer, error analysis, ablations, and `usage_report.md`.

Excluded: `.venv/`, `__pycache__/`, `.cache/`, `dataset/`, `data/`, `.env`, `log.txt`, `audit/` traces (kept locally for debugging, too large and too noisy to ship).
