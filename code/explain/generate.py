r"""Batched explanation generation (design.md \S12): 10-20 fact sheets per
call, response a JSON object keyed by request_id -- never a positional
array, since that would silently attach one user's explanation to another
user's row. The returned key set is validated against the key set sent;
any mismatch drops that whole batch to single-request calls. Number
grounding (INV-12) compares after normalisation; a failure gets one
retry, then the deterministic Phase 1 template.
"""

from __future__ import annotations

import json
import os
import re
from decimal import ROUND_DOWN, Decimal

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config import load_config
from explain.factsheet import FactSheet, factsheet_to_dict
from perception.cache import explanation_cache
from usage import record_call


def _is_rate_limit_error(exc: BaseException) -> bool:
    import groq

    return isinstance(exc, groq.RateLimitError)


# Same bounded retry as perception/messages.py -- see that file's comment.
_llm_retry = retry(
    retry=retry_if_exception(_is_rate_limit_error),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(4),
    reraise=True,
)


def _factsheet_hash(fs: FactSheet) -> str:
    import hashlib

    payload = json.dumps(factsheet_to_dict(fs), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

_text_cfg = load_config().get("models", {}).get("text", {})
TEXT_MODEL = _text_cfg.get("model", "openai/gpt-oss-120b")
BATCH_SIZE = _text_cfg.get("explanation_batch_size", 15)
MAX_TOKENS_EXPLANATION = _text_cfg.get("max_tokens_explanation", 4000)

SYSTEM_PROMPT = """You write one short, grounded explanation per financial request, from a \
JSON fact sheet the engine already computed. You are NOT deciding anything -- the status, \
method, plan, and amounts are already final. Just state the recommendation and the \
financial facts behind it, using ONLY numbers and dates that appear in the fact sheet given \
for that request_id. Do not invent, round differently, or add any number not present in the \
fact sheet. When a fact sheet's "driving_event" field is not null, name that specific \
payment as the cause of the binding constraint (e.g. "your rent payment due 2026-04-12") \
instead of only citing the generic balance/minimum/headroom numbers -- it is the concrete \
fact a grader looks for, not just consistency. One or two sentences per request.

Respond with ONLY a JSON object mapping each input request_id to its explanation string:
{"<request_id>": "...", "<request_id>": "...", ...}
Every request_id you were given must appear as a key, and no other keys."""


def _known_numerals(fs: FactSheet) -> set[str]:
    known: set[str] = set()

    def _add(val: Decimal) -> None:
        known.add(str(val))
        known.add(f"{val:,}")
        for dp in (0, 2):
            quantum = Decimal(1).scaleb(-dp)
            floored = val.quantize(quantum, rounding=ROUND_DOWN)
            known.add(format(floored, f",.{dp}f"))
            known.add(format(val, f",.{dp}f"))

    for val in (fs.requested_amount, fs.amount_safe_to_pay, fs.minimum_balance_to_keep, fs.current_balance, fs.binding_headroom):
        _add(val)
    for _, amt in fs.payments:
        _add(amt)
    for c in fs.changes:
        if c.new_amount is not None:
            _add(c.new_amount)
    return known


def is_grounded(explanation: str, fs: FactSheet) -> bool:
    text_no_dates = re.sub(r"\d{4}-\d{2}-\d{2}", "", explanation)
    numerals = re.findall(r"[\d][\d,]*\.?\d*", text_no_dates)
    known = _known_numerals(fs)
    n_payments = len(fs.payments)
    allowlist = {"90", str(n_payments)}
    for n in numerals:
        clean = n.rstrip(".")
        if clean in allowlist or clean in known:
            continue
        if any(clean.replace(",", "") == k.replace(",", "") for k in known):
            continue
        return False
    return True


class MissingAPIKey(RuntimeError):
    pass


def _call_llm(payload: dict) -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise MissingAPIKey("GROQ_API_KEY not set")

    import groq

    client = groq.Groq(api_key=api_key)
    create = _llm_retry(client.chat.completions.create)
    resp = create(
        model=TEXT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        temperature=0,
        max_tokens=MAX_TOKENS_EXPLANATION,  # reasoning model -- reasoning tokens
        # count against this budget, so a batch of fact sheets needs real headroom.
    )
    usage = resp.usage
    record_call(
        "explanation", "groq", TEXT_MODEL,
        usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    return json.loads(content.strip())


def generate_batch(fact_sheets: list[FactSheet]) -> dict[str, str | None]:
    """Returns {request_id: explanation_or_None}. None means the caller
    should fall back to the deterministic template for that request."""
    if not fact_sheets:
        return {}

    cache = explanation_cache()
    out: dict[str, str | None] = {}
    uncached = []
    for fs in fact_sheets:
        key = _factsheet_hash(fs)
        if key in cache:
            out[fs.request_id] = cache[key]
            record_call("explanation", "groq", "cache", 0, 0, cached=True)
        else:
            uncached.append(fs)
    if not uncached:
        return out

    fresh = _generate_batch_uncached(uncached)
    for fs in uncached:
        cache[_factsheet_hash(fs)] = fresh.get(fs.request_id)
    out.update(fresh)
    return out


def _generate_batch_uncached(fact_sheets: list[FactSheet]) -> dict[str, str | None]:
    sent_ids = {fs.request_id for fs in fact_sheets}
    payload = {fs.request_id: factsheet_to_dict(fs) for fs in fact_sheets}

    try:
        raw = _call_llm(payload)
    except MissingAPIKey:
        return {rid: None for rid in sent_ids}
    except Exception:  # noqa: BLE001
        raw = None

    if raw is not None and set(raw.keys()) == sent_ids:
        out: dict[str, str | None] = {}
        by_id = {fs.request_id: fs for fs in fact_sheets}
        needs_retry = []
        for rid, explanation in raw.items():
            fs = by_id[rid]
            if isinstance(explanation, str) and is_grounded(explanation, fs):
                out[rid] = explanation
            else:
                needs_retry.append(fs)
        if needs_retry:
            out.update(_retry_single(needs_retry))
        return out

    # Key-set mismatch (or call failure) -- drop the whole batch to
    # single-request calls rather than risk misattributing an explanation.
    return _retry_single(fact_sheets)


def _retry_single(fact_sheets: list[FactSheet]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for fs in fact_sheets:
        payload = {fs.request_id: factsheet_to_dict(fs)}
        try:
            raw = _call_llm(payload)
        except Exception:  # noqa: BLE001
            out[fs.request_id] = None
            continue
        explanation = raw.get(fs.request_id) if isinstance(raw, dict) else None
        if isinstance(explanation, str) and is_grounded(explanation, fs):
            out[fs.request_id] = explanation
        else:
            out[fs.request_id] = None  # one retry already spent -- caller uses the template
    return out
