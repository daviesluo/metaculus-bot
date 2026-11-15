"""
What the bot spends: tokens by model, OpenRouter's public prices, and the key's own meter.

Nothing here prints or returns a secret. The key is read from the environment and sent only to OpenRouter.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

from forecasting_tools import GeneralLlm

logger = logging.getLogger(__name__)

# Tokens by model for this process: calls, prompt tokens, completion tokens (reasoning included).
USAGE: dict[str, dict[str, float]] = {}

# Dollars per token and per web-search request, by OpenRouter model id. Filled from OpenRouter's public list; these
# are the fallback (read 2026-11-15).
_FALLBACK_PRICES: dict[str, tuple[float, float, float]] = {
    "openai/gpt-4o": (2.5e-6, 10e-6, 0.0),
    "openai/gpt-4o-mini": (0.15e-6, 0.6e-6, 0.0),
    "openai/gpt-6-luna": (0.1e-6, 0.5e-6, 0.01),
    "openai/gpt-6.1-terra": (1e-6, 5e-6, 0.01),
    "openai/gpt-6.1-sol": (2e-6, 10e-6, 0.01),
    "anthropic/claude-haiku-5.5": (0.1e-6, 0.5e-6, 0.01),
    "anthropic/claude-sonnet-5.5": (2e-6, 10e-6, 0.01),
    "anthropic/claude-opus-5.5": (4e-6, 20e-6, 0.01),
    "google/gemini-4-carbon": (2e-6, 10e-6, 0.014),
    "x-ai/grok-4.7": (2e-6, 6e-6, 0.005),
    "perplexity/sonar": (1e-6, 1e-6, 0.005),
    "perplexity/sonar-pro": (3e-6, 15e-6, 0.005),
}
_prices: dict[str, tuple[float, float, float]] | None = None
# Models that search the web on every call: their per-request fee is part of a call's cost.
_SEARCH_EVERY_CALL = ("perplexity/",)


def _short(model: str) -> str:
    return model.removeprefix("openrouter/")


def prices() -> dict[str, tuple[float, float, float]]:
    """OpenRouter's public price list (no key needed), or the fallback table when it cannot be read."""
    global _prices
    if _prices is None:
        table = dict(_FALLBACK_PRICES)
        try:
            data = requests.get("https://openrouter.ai/api/v1/models", timeout=20).json()["data"]
            for m in data:
                p = m.get("pricing") or {}
                table[m["id"]] = (float(p.get("prompt") or 0), float(p.get("completion") or 0), float(p.get("web_search") or 0))
        except Exception as e:  # the fallback is good enough to guard a budget
            logger.warning(f"OpenRouter's price list could not be read ({type(e).__name__}); using the built-in table")
        _prices = table
    return _prices


def call_cost(model: str, prompt_tokens: float, completion_tokens: float, calls: float = 1) -> float:
    p_in, p_out, p_search = prices().get(_short(model), (0.0, 0.0, 0.0))
    search = p_search * calls if _short(model).startswith(_SEARCH_EVERY_CALL) else 0.0
    return prompt_tokens * p_in + completion_tokens * p_out + search


def record(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    u = USAGE.setdefault(_short(model), {"calls": 0, "prompt": 0, "completion": 0})
    u["calls"] += 1
    u["prompt"] += prompt_tokens or 0
    u["completion"] += completion_tokens or 0


def snapshot() -> dict[str, dict[str, float]]:
    return {m: dict(u) for m, u in USAGE.items()}


def spent_since(before: dict[str, dict[str, float]]) -> tuple[float, list[dict[str, Any]]]:
    """Dollars (at list prices) and a per-model table for what was used since `before`."""
    rows, total = [], 0.0
    for model, now in sorted(USAGE.items()):
        was = before.get(model, {"calls": 0, "prompt": 0, "completion": 0})
        calls, p, c = now["calls"] - was["calls"], now["prompt"] - was["prompt"], now["completion"] - was["completion"]
        if not calls:
            continue
        cost = call_cost(model, p, c, calls)
        total += cost
        rows.append({"model": model, "calls": int(calls), "prompt_tokens": int(p), "completion_tokens": int(c), "usd": round(cost, 5)})
    return total, rows


def worst_case_question_cost(profile: dict, prompt_tokens: int = 12_000) -> float:
    """The most one question can cost under a profile: every forecaster filling its output cap on a long prompt."""
    total = 0.0
    for f in profile["forecasters"]:
        total += call_cost(f["model"], prompt_tokens, f["max_tokens"])
    if profile.get("web_researcher"):
        total += call_cost(profile["web_researcher"], 2_000, 1_500)
    return total + 0.01  # the scout's and the parser's calls


def key_status() -> dict[str, float | None] | None:
    """The OpenRouter key's own meter: dollars used, its cap and what is left. None when it cannot be read."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        r = requests.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        r.raise_for_status()
        d = r.json()["data"]
        usage, limit = d.get("usage"), d.get("limit")
        remaining = d.get("limit_remaining")
        if remaining is None and limit is not None and usage is not None:
            remaining = limit - usage
        return {"usage": usage, "limit": limit, "remaining": remaining}
    except Exception as e:
        logger.warning(f"The OpenRouter key's meter could not be read ({type(e).__name__})")
        return None


class MeteredLlm(GeneralLlm):
    """A GeneralLlm that counts its tokens into USAGE."""

    async def _call_litellm(self, prompt):  # type: ignore[override]
        response = await super()._call_litellm(prompt)
        usage = getattr(response, "usage", None)
        if usage is not None:
            record(self.model, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)
        if not has_answer(response):
            # The library would hand back the model's private reasoning as if it were the answer, and a parser then
            # reads a number out of half a thought (a test read 0 % that way). No answer is an error.
            raise RuntimeError(f"{_short(self.model)} gave no answer: its output cap was spent on reasoning")
        return response


def has_answer(response: Any) -> bool:
    """Whether a chat response carries a written answer (not only reasoning)."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return False
    return isinstance(content, str) and bool(content.strip())


def llm(model: str, effort: str | None = None, max_tokens: int | None = None, timeout: int = 300, tries: int = 2,
        reasoning_tokens: int | None = None) -> MeteredLlm:
    """
    A metered model. `max_tokens` caps output, reasoning included. `effort` asks OpenRouter for that reasoning effort;
    `reasoning_tokens` instead gives the reasoning its own cap, for models that otherwise spend the whole output on it.
    """
    kwargs: dict[str, Any] = {}
    if reasoning_tokens:
        kwargs["extra_body"] = {"reasoning": {"max_tokens": reasoning_tokens}}
    elif effort:
        kwargs["extra_body"] = {"reasoning": {"effort": effort}}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    return MeteredLlm(model=model, temperature=None, timeout=timeout, allowed_tries=tries, **kwargs)
