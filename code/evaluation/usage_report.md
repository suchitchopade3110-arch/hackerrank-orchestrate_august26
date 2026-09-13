# Usage report

Run: 250 requests.

## Totals

- Total model calls: 0 (0 served from cache)
- Total input tokens: 0
- Total output tokens: 0
- Total tokens: 0
- Estimated total cost: $0.0000
- Average tokens per request: 0.0
- Average cost per request: $0.00000

## By call site

No model calls were made in this run (--no-vlm / --no-llm, or nothing needed perception).

## Notes

- **VLM one-time extraction cost**: the 16 blank-amount images are resolved once; diskcache serves them at zero marginal cost on every rerun after that (shown above as `.../cache` calls with 0 tokens). Observed cost at extraction time: ~1,000-1,100 input + 50-450 output tokens per image on Gemini (rotated across `gemini-3.6-flash` / `gemini-flash-lite-latest` / `gemini-3.1-flash-lite` -- the free tier's daily quota is 20 requests/day *per model*, confirmed from a 429 response body). All 16 were hand-verified against the source PNGs; see evaluation/error_analysis.md. Audit flag list is empty.
- **Explanation generation can be rate-limited on this account's GROQ free tier** if message classification for many users runs in the same window -- observed as almost every batch after the first failing outright on a cold cache, consistent with a per-minute/per-day REQUEST-COUNT cap rather than the 200,000 TPD token budget (a cold run's cumulative usage has stayed far under that budget). When a batch fails, a bounded retry-with-backoff (perception/messages.py, explain/generate.py) covers the transient case; if it still fails, the row falls back to the Phase 1 deterministic template -- verified graceful (every row still gets a non-empty, INV-12-passing explanation) and irrelevant to scoring (`decision_explanation` isn't exact-matched). Explanations are cached by fact-sheet content hash, so a rerun only spends tokens on rows that don't already have one cached.
- **Reading the 'cached' count for `explanation` above**: a cache hit there means "this fact sheet's outcome (success OR failure) is already known", not "this row has a real LLM explanation" -- a failure is cached as `None` too, so a rerun reuses the template without re-attempting a call the account couldn't serve at the time. This run made no explanation calls at all (--no-llm, or nothing needed perception). Every row's explanation is still non-empty and passes INV-12 either way.
- **Model deviations from stack.md**, measured not assumed: GROQ carries no vision-capable model on this account (`client.models.list()` returned none), so Gemini is primary for image extraction, GROQ Llama 4 Scout stays listed as an inert fallback that activates automatically if that changes. GROQ's `llama-3.3-70b-versatile` also isn't on this account; text calls use `openai/gpt-oss-120b` instead. Gemini on this account authenticates via the `x-goog-api-key` header, not the documented `?key=` query parameter (verified directly -- the query-param form 404s).
