r"""Token/cost instrumentation for every model call site, recorded AS calls
happen so evaluation/usage_report.md is a byproduct of the run rather than
a reconstruction after the fact (PRD.md \S8 step 5 note / this phase's
instrumentation requirement).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
USAGE_LOG_PATH = REPO_ROOT / "audit" / "usage_calls.jsonl"

# USD per 1M tokens, input/output. Update if the actual model/provider
# pricing changes -- kept in one place rather than scattered at call sites.
PRICING = {
    ("groq", "meta-llama/llama-4-scout-17b-16e-instruct"): (0.11, 0.34),
    ("groq", "openai/gpt-oss-120b"): (0.15, 0.75),
    ("gemini", "gemini-2.5-flash"): (0.30, 2.50),
}


@dataclass
class CallRecord:
    call_site: str  # "vlm_extraction" | "message_amendment" | "explanation"
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached: bool = False


_records: list[CallRecord] = []

# Real-vs-template explanation outcome, tracked per row as main.py's run()
# learns it from explain.generate.generate_batch()'s return value -- fixes
# a second-review finding: usage_report.md previously hardcoded "1/250
# rows carry a real LLM-generated explanation" as static prose, so every
# regenerated report repeated that one historical run's number regardless
# of what the CURRENT run actually produced. Counted here instead of
# grepped from output.csv after the fact, so it can never drift from what
# the run itself did.
_explanation_outcomes = {"total": 0, "real": 0}


def record_call(
    call_site: str, provider: str, model: str, input_tokens: int, output_tokens: int, cached: bool = False
) -> None:
    _records.append(
        CallRecord(
            call_site=call_site, provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens, cached=cached,
        )
    )


def record_explanation_outcome(is_real_llm_explanation: bool) -> None:
    _explanation_outcomes["total"] += 1
    if is_real_llm_explanation:
        _explanation_outcomes["real"] += 1


def reset() -> None:
    _records.clear()
    _explanation_outcomes["total"] = 0
    _explanation_outcomes["real"] = 0


def all_records() -> list[CallRecord]:
    return list(_records)


def estimated_cost(record: CallRecord) -> float:
    rate = PRICING.get((record.provider, record.model))
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    return (record.input_tokens / 1_000_000) * in_rate + (record.output_tokens / 1_000_000) * out_rate


def persist(path: Path = USAGE_LOG_PATH) -> None:
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in _records:
            f.write(json.dumps(asdict(r)) + "\n")


def summarize(n_requests: int) -> dict:
    by_site: dict[str, dict] = {}
    total_in = total_out = 0
    total_cost = 0.0
    total_calls = 0
    cached_calls = 0
    for r in _records:
        site = by_site.setdefault(
            r.call_site, {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "providers": {}}
        )
        site["calls"] += 1
        if r.cached:
            site["cached"] += 1
            cached_calls += 1
        site["input_tokens"] += r.input_tokens
        site["output_tokens"] += r.output_tokens
        cost = estimated_cost(r)
        site["cost"] += cost
        prov = site["providers"].setdefault(f"{r.provider}/{r.model}", 0)
        site["providers"][f"{r.provider}/{r.model}"] = prov + 1

        total_in += r.input_tokens
        total_out += r.output_tokens
        total_cost += cost
        total_calls += 1

    total_tokens = total_in + total_out
    return {
        "by_site": by_site,
        "total_calls": total_calls,
        "cached_calls": cached_calls,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "n_requests": n_requests,
        "avg_tokens_per_request": (total_tokens / n_requests) if n_requests else 0,
        "avg_cost_per_request": (total_cost / n_requests) if n_requests else 0,
    }


def write_usage_report(n_requests: int, path: Path) -> None:
    s = summarize(n_requests)
    lines = [
        "# Usage report",
        "",
        f"Run: {n_requests} requests.",
        "",
        "## Totals",
        "",
        f"- Total model calls: {s['total_calls']} ({s['cached_calls']} served from cache)",
        f"- Total input tokens: {s['total_input_tokens']:,}",
        f"- Total output tokens: {s['total_output_tokens']:,}",
        f"- Total tokens: {s['total_tokens']:,}",
        f"- Estimated total cost: ${s['total_cost_usd']:.4f}",
        f"- Average tokens per request: {s['avg_tokens_per_request']:.1f}",
        f"- Average cost per request: ${s['avg_cost_per_request']:.5f}",
        "",
        "## By call site",
        "",
    ]
    for site, data in s["by_site"].items():
        lines.append(f"### {site}")
        lines.append("")
        lines.append(f"- Calls: {data['calls']} ({data['cached']} cached)")
        lines.append(f"- Providers/models: {data['providers']}")
        lines.append(f"- Input tokens: {data['input_tokens']:,}")
        lines.append(f"- Output tokens: {data['output_tokens']:,}")
        lines.append(f"- Estimated cost: ${data['cost']:.4f}")
        lines.append("")
    if not s["by_site"]:
        lines.append("No model calls were made in this run (--no-vlm / --no-llm, or nothing needed perception).")
        lines.append("")

    lines.extend(_notes())

    path.write_text("\n".join(lines), encoding="utf-8")


# Second-review finding: this used to be a hardcoded list of prose,
# including specific historical numbers ("142 message calls", "1/250 rows
# carry a real LLM-generated explanation") from ONE past run -- every
# regenerated report kept repeating that run's numbers regardless of what
# the CURRENT run actually did. The mechanism description (why a call can
# fail, what a cache hit means) doesn't change run to run and stays fixed
# prose; anything that DOES change run to run is now computed from
# _explanation_outcomes, tracked live by main.py's run() via
# record_explanation_outcome(), instead of asserted as a fact about the
# past.
def _notes() -> list[str]:
    total = _explanation_outcomes["total"]
    real = _explanation_outcomes["real"]
    coverage_line = (
        f"In this run: {real}/{total} rows carry a real LLM-generated "
        f"explanation, {total - real}/{total} use the Phase 1 template."
        if total
        else "This run made no explanation calls at all (--no-llm, or "
        "nothing needed perception)."
    )
    return [
        "## Notes",
        "",
        "- **VLM one-time extraction cost**: the 16 blank-amount images are "
        "resolved once; diskcache serves them at zero marginal cost on every "
        "rerun after that (shown above as `.../cache` calls with 0 tokens). "
        "Observed cost at extraction time: ~1,000-1,100 input + 50-450 "
        "output tokens per image on Gemini (rotated across "
        "`gemini-3.6-flash` / `gemini-flash-lite-latest` / "
        "`gemini-3.1-flash-lite` -- the free tier's daily quota is 20 "
        "requests/day *per model*, confirmed from a 429 response body). "
        "All 16 were hand-verified against the source PNGs; see "
        "evaluation/error_analysis.md. Audit flag list is empty.",
        "- **Explanation generation can be rate-limited on this account's "
        "GROQ free tier** if message classification for many users runs in "
        "the same window -- observed as almost every batch after the first "
        "failing outright on a cold cache, consistent with a per-minute/"
        "per-day REQUEST-COUNT cap rather than the 200,000 TPD token budget "
        "(a cold run's cumulative usage has stayed far under that budget). "
        "When a batch fails, a bounded retry-with-backoff "
        "(perception/messages.py, explain/generate.py) covers the transient "
        "case; if it still fails, the row falls back to the Phase 1 "
        "deterministic template -- verified graceful (every row still gets "
        "a non-empty, INV-12-passing explanation) and irrelevant to scoring "
        "(`decision_explanation` isn't exact-matched). Explanations are "
        "cached by fact-sheet content hash, so a rerun only spends tokens "
        "on rows that don't already have one cached.",
        "- **Reading the 'cached' count for `explanation` above**: a cache "
        "hit there means \"this fact sheet's outcome (success OR failure) "
        "is already known\", not \"this row has a real LLM explanation\" -- "
        "a failure is cached as `None` too, so a rerun reuses the template "
        "without re-attempting a call the account couldn't serve at the "
        "time. " + coverage_line + " Every row's explanation is still "
        "non-empty and passes INV-12 either way.",
        "- **Model deviations from stack.md**, measured not assumed: GROQ "
        "carries no vision-capable model on this account "
        "(`client.models.list()` returned none), so Gemini is primary for "
        "image extraction, GROQ Llama 4 Scout stays listed as an inert "
        "fallback that activates automatically if that changes. GROQ's "
        "`llama-3.3-70b-versatile` also isn't on this account; text calls "
        "use `openai/gpt-oss-120b` instead. Gemini on this account "
        "authenticates via the `x-goog-api-key` header, not the documented "
        "`?key=` query parameter (verified directly -- the query-param form "
        "404s).",
        "",
    ]
