r"""VLM amount extraction for the 16 blank-amount events (design.md \S11
ladder): step 1 GROQ vision + plausibility check, step 2 the fallback
provider (Gemini), step 3 conservative substitution (already implemented
in ledger/canonical.py) with an audit flag. No default, no fallback
literal for a missing key -- a missing key is just another provider
failure the ladder falls through, not a hardcoded value.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import load_config
from perception.cache import image_cache
from perception.schemas import ImageExtraction
from usage import record_call

_cfg = load_config()
_models_cfg = _cfg.get("models", {})
_vlm_cfg = _models_cfg.get("vlm", {})
_vlm_fallback_cfg = _models_cfg.get("vlm_fallback", {})
_perception_cfg = _cfg.get("perception", {})

GROQ_VLM_MODEL = _vlm_fallback_cfg.get("model", "meta-llama/llama-4-scout-17b-16e-instruct")
# The free tier's daily quota is per-model (20 requests/day, observed
# directly from a 429 RESOURCE_EXHAUSTED body), not per-key -- with only
# 16 images total this is still enough runway, but a rotation of fresh
# models absorbs a quota hit on any single one rather than failing the
# whole ladder into the conservative placeholder. config.yaml's
# models.vlm.model is a list for exactly this reason.
_gemini_model_cfg = _vlm_cfg.get("model", "gemini-3.6-flash")
GEMINI_VLM_MODELS = (
    _gemini_model_cfg if isinstance(_gemini_model_cfg, list) else [_gemini_model_cfg]
)
CONFIDENCE_THRESHOLD = _perception_cfg.get("vlm_confidence_threshold", 0.6)
DEFAULT_PROVIDER_ORDER = tuple(_perception_cfg.get("vlm_provider_order", ["gemini", "groq"]))

PROMPT_TEMPLATE = (
    "You are reading a financial document image (a bill, receipt, or payroll slip). "
    "Extract the single monetary AMOUNT shown, its CURRENCY code, the DATE if shown "
    "(YYYY-MM-DD), and a short DOC_TYPE label. The expected category is '{category}' "
    "and the user's home currency is '{currency}'. "
    'Respond with ONLY a JSON object, no prose: '
    '{{"amount": <number>, "currency": "<3-letter code>", "date": "<YYYY-MM-DD or null>", '
    '"doc_type": "<label>", "confidence": <0.0-1.0>}}'
)


class MissingAPIKey(RuntimeError):
    pass


class ExtractionFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class LadderAttempt:
    provider: str
    outcome: str  # "accepted" | "rejected_low_confidence" | "rejected_implausible" | "error"
    detail: str


def _b64_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


@retry(
    retry=retry_if_exception_type(httpx.HTTPStatusError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
)
def _groq_extract(image_path: Path, category: str, currency: str) -> ImageExtraction:
    import groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise MissingAPIKey("GROQ_API_KEY not set")

    client = groq.Groq(api_key=api_key)
    b64 = _b64_image(image_path)
    prompt = PROMPT_TEMPLATE.format(category=category, currency=currency)

    resp = client.chat.completions.create(
        model=GROQ_VLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        temperature=0,
        max_tokens=128,
        response_format={"type": "json_object"},
    )
    usage = resp.usage
    record_call(
        "vlm_extraction", "groq", GROQ_VLM_MODEL,
        usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0,
    )
    content = resp.choices[0].message.content
    return ImageExtraction(**json.loads(content))


def _gemini_extract_one(image_path: Path, category: str, currency: str, model: str) -> ImageExtraction:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise MissingAPIKey("GEMINI_API_KEY not set")

    b64 = _b64_image(image_path)
    prompt = PROMPT_TEMPLATE.format(category=category, currency=currency)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [
            {"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/png", "data": b64}}]}
        ],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 2048},
    }
    # This account's key authenticates via the x-goog-api-key header, not
    # the ?key= query param (verified directly against the API).
    resp = httpx.post(url, json=payload, headers={"x-goog-api-key": api_key}, timeout=30)
    resp.raise_for_status()
    j = resp.json()
    usage_meta = j.get("usageMetadata", {})
    record_call(
        "vlm_extraction", "gemini", model,
        usage_meta.get("promptTokenCount", 0), usage_meta.get("candidatesTokenCount", 0),
    )
    text = j["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return ImageExtraction(**json.loads(text.strip()))


def _gemini_extract(image_path: Path, category: str, currency: str) -> ImageExtraction:
    """Rotates across GEMINI_VLM_MODELS on a 429 (daily free-tier quota is
    per-model) so one model's quota being spent doesn't fail the whole
    provider -- each is still the same underlying Gemini vision capability,
    just a different quota bucket."""
    last_error: Exception | None = None
    for model in GEMINI_VLM_MODELS:
        try:
            return _gemini_extract_one(image_path, category, currency, model)
        except httpx.HTTPStatusError as e:
            last_error = e
            if e.response.status_code == 429:
                continue
            raise
    raise last_error


def _plausible(extraction: ImageExtraction, comparable_amounts: list) -> bool:
    from decimal import Decimal

    if extraction.amount <= 0:
        return False
    if comparable_amounts:
        # Wide band, not a tight one: this only exists to catch a wildly
        # wrong OCR read (an extra/missing digit), not to second-guess a
        # correct total that's simply larger or smaller than this user's
        # other purchases in the category (verified by hand against all 16
        # source images -- a tighter band rejected several exactly-correct
        # extractions on legitimately unusual amounts).
        amt = Decimal(str(extraction.amount))
        lo = min(comparable_amounts) * Decimal("0.01")
        hi = max(comparable_amounts) * Decimal(100)
        if not (lo <= amt <= hi):
            return False
    return True


_PROVIDER_FNS = {"groq": _groq_extract, "gemini": _gemini_extract}


def extract_blank_amount(
    image_path: Path,
    image_id: str,
    category: str,
    currency: str,
    comparable_amounts: list,
    # GROQ carries no vision-capable model on this account as of this build
    # (verified via client.models.list()); Gemini goes first for real
    # accuracy, GROQ stays listed second so it activates automatically the
    # day a vision model appears on the account -- config.yaml's
    # perception.vlm_provider_order, not a code change.
    provider_order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
) -> tuple[ImageExtraction | None, list[LadderAttempt]]:
    """Runs the ladder's steps 1-2 (VLM + fallback provider). Step 3
    (conservative substitution) is the caller's responsibility -- it
    already exists in ledger/canonical.py and doesn't need a model.

    Returns (extraction_or_None, attempts). A cached result (including a
    cached None, meaning both providers failed last time) is replayed
    without a new API call.
    """
    cache = image_cache()
    if image_id in cache:
        cached_extraction, cached_attempts = cache[image_id]
        for a in cached_attempts:
            record_call("vlm_extraction", a.provider, "cache", 0, 0, cached=True)
        return cached_extraction, cached_attempts

    attempts: list[LadderAttempt] = []
    result: ImageExtraction | None = None

    for provider in provider_order:
        fn = _PROVIDER_FNS[provider]
        try:
            extraction = fn(image_path, category, currency)
        except MissingAPIKey as e:
            attempts.append(LadderAttempt(provider, "error", f"missing_api_key: {e}"))
            continue
        except Exception as e:  # noqa: BLE001 -- any provider failure falls through the ladder
            attempts.append(LadderAttempt(provider, "error", str(e)))
            continue

        if extraction.confidence < CONFIDENCE_THRESHOLD:
            attempts.append(LadderAttempt(provider, "rejected_low_confidence", str(extraction.confidence)))
            continue
        if not _plausible(extraction, comparable_amounts):
            attempts.append(LadderAttempt(provider, "rejected_implausible", str(extraction.amount)))
            continue

        attempts.append(LadderAttempt(provider, "accepted", str(extraction.amount)))
        result = extraction
        break

    cache[image_id] = (result, attempts)
    return result, attempts
