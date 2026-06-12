"""Tier-3 extraction: LLM-assisted fallback via OpenAI gpt-4o-mini.

Rules:
  - Each function accepts an optional ``client`` parameter for mock injection
    (Arthur's tests call with a mock; production uses default OpenAI()).
  - Each function returns (value, confidence) where confidence ∈ [0.0, 1.0].
  - Responses are cached in _CACHE (memory-only; cleared on process restart).
  - JSON mode is used for structured output.
  - Failures are caught; never crash the pipeline.
  - Only called when the regex tier produced no result for a field.

2026-06-12
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("mechpm.extractor.llm")

# In-memory response cache.  Key: "{field_type}:{text_prefix}".
_CACHE: dict[str, Any] = {}
_MODEL = "gpt-4o-mini"


def _get_client(client: Any | None = None) -> Any:
    """Return an OpenAI client.  Accepts a mock for unit testing."""
    if client is not None:
        return client
    try:
        from openai import OpenAI  # type: ignore[import]
        return OpenAI()  # reads OPENAI_API_KEY from environment
    except ImportError:
        raise RuntimeError(
            "openai package not installed. "
            "Add 'openai>=1.0' to pyproject.toml dependencies."
        )


def extract_rate_from_prose(
    text: str,
    client: Any | None = None,
) -> tuple[dict | None, float]:
    """Extract day/hour rate from free-text prose.

    Returns: ({"min": float, "max": float|null, "period": "day"|"hour",
               "currency": "GBP"}, confidence).
    Returns (None, 0.0) when no rate is present or on error.
    """
    cache_key = f"rate:{text[:300]}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    prompt = (
        "Extract the contractor day rate or hourly rate from this UK job description. "
        'Return JSON: {"min": number_or_null, "max": number_or_null, '
        '"period": "day" or "hour", "currency": "GBP"}. '
        'If no rate is present return {"min": null}.\n\n'
        f"Text: {text[:800]}"
    )
    try:
        c = _get_client(client)
        resp = c.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": "You are a data extraction assistant. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=150,
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content)
        result: tuple[dict | None, float] = (
            (data, 0.75) if data.get("min") is not None else (None, 0.0)
        )
    except Exception as exc:
        logger.warning("LLM rate extraction failed: %s", exc)
        result = (None, 0.0)

    _CACHE[cache_key] = result
    return result


def extract_date_from_prose(
    text: str,
    client: Any | None = None,
) -> tuple[str | None, float]:
    """Extract contract start date from free-text prose.

    Returns: ("YYYY-MM-DD" or "asap", confidence).
    Returns (None, 0.0) when absent or on error.
    """
    cache_key = f"date:{text[:300]}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    prompt = (
        "Extract the contract start date from this UK job description. "
        'Return JSON: {"start_date": "YYYY-MM-DD" or "asap" or null}.\n\n'
        f"Text: {text[:600]}"
    )
    try:
        c = _get_client(client)
        resp = c.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": "You are a data extraction assistant. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=60,
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content)
        val = data.get("start_date")
        result: tuple[str | None, float] = (val, 0.8) if val else (None, 0.0)
    except Exception as exc:
        logger.warning("LLM date extraction failed: %s", exc)
        result = (None, 0.0)

    _CACHE[cache_key] = result
    return result


def extract_ir35_from_body(
    text: str,
    client: Any | None = None,
) -> tuple[str | None, float]:
    """Infer IR35 status from job-description prose when regex finds nothing.

    Returns: ("inside"|"outside"|"undetermined", confidence).
    Returns (None, 0.0) on error or when genuinely absent.
    Prefers 'undetermined' over guessing — only returns inside/outside when
    the body contains strong signals.
    """
    cache_key = f"ir35:{text[:300]}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    prompt = (
        "Determine the IR35 status implied by this UK contract job description. "
        'Return JSON: {"ir35": "inside" or "outside" or "undetermined"}. '
        "Use 'undetermined' if genuinely unclear; never guess.\n\n"
        f"Text: {text[:800]}"
    )
    try:
        c = _get_client(client)
        resp = c.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": "You are a data extraction assistant. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=60,
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content)
        val = data.get("ir35", "undetermined")
        result: tuple[str | None, float] = (val, 0.65)
    except Exception as exc:
        logger.warning("LLM IR35 extraction failed: %s", exc)
        result = (None, 0.0)

    _CACHE[cache_key] = result
    return result


def disambiguate_location(
    location_raw: str | None,
    description: str | None = None,
    client: Any | None = None,
) -> tuple[str | None, float]:
    """Resolve ambiguous location string to a canonical UK city/region.

    Returns: ("City, Region", confidence).
    Returns (None, 0.0) when location_raw is absent or on error.
    """
    if not location_raw:
        return None, 0.0

    cache_key = f"loc:{location_raw}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    prompt = (
        "Resolve this UK job location to the nearest major city or region. "
        'Return JSON: {"location": "City, Region" or null}. '
        "Clean up spacing and capitalisation; do not invent locations.\n\n"
        f"Location: {location_raw}\n"
        f"Context: {(description or '')[:200]}"
    )
    try:
        c = _get_client(client)
        resp = c.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": "You are a UK geography assistant. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=60,
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content)
        val = data.get("location")
        result: tuple[str | None, float] = (val, 0.85) if val else (None, 0.0)
    except Exception as exc:
        logger.warning("LLM location disambiguation failed: %s", exc)
        result = (None, 0.0)

    _CACHE[cache_key] = result
    return result


def clear_cache() -> None:
    """Clear the in-memory LLM response cache (useful in tests)."""
    _CACHE.clear()
