"""
Tests on CLOSED questions: the bot against the template's default bot, scored against the resolutions.

The tournament's rule allows testing only on closed or non-tournament questions, so this reads questions that have
already RESOLVED and forecasts each as of an hour after it opened: the news search is cut off at that instant, and the
web search and the markets (which cannot be cut off) are left out for both bots. Nothing is published.
Both bots are given the same news text, so the comparison is of the forecasters, not of the luck of a search.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from forecasting_tools import (
    ApiFilter,
    BinaryQuestion,
    MetaculusClient,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericQuestion,
)

import main as template_module
import metering
from forecaster import PrizeBot
from main import FallTemplateBot2026

logger = logging.getLogger(__name__)
_EPS = 1e-6


# ---------------------------------------------------------------------- scores (pure)

def score_binary(p: float, resolved_yes: bool) -> dict[str, float]:
    """Log score (natural log of the probability given to what happened) and Brier score."""
    q = p if resolved_yes else 1 - p
    return {"log": math.log(max(q, _EPS)), "brier": (p - (1.0 if resolved_yes else 0.0)) ** 2}


def score_options(probabilities: dict[str, float], resolution: str) -> dict[str, float]:
    return {"log": math.log(max(probabilities.get(resolution, 0.0), _EPS))}


def score_cdf(xs: list[float], cdf: list[float], resolution: float | str) -> dict[str, float]:
    """
    Log of the probability given to the bin the value fell in, against a flat forecast over the range (0 is as good as
    flat). `xs` are the CDF's values, `cdf` its heights; a resolution "above"/"below" the range reads the tail's mass
    against 5 %.
    """
    if isinstance(resolution, str):
        mass = (1 - cdf[-1]) if "above" in resolution else cdf[0]
        return {"log": math.log(max(mass, _EPS) / 0.05)}
    if resolution < xs[0]:
        return {"log": math.log(max(cdf[0], _EPS) / 0.05)}
    if resolution > xs[-1]:
        return {"log": math.log(max(1 - cdf[-1], _EPS) / 0.05)}
    i = max(0, min(len(xs) - 2, next((k for k in range(len(xs) - 1) if xs[k] <= resolution <= xs[k + 1]), len(xs) - 2)))
    mass = cdf[i + 1] - cdf[i]
    return {"log": math.log(max(mass, _EPS) * (len(xs) - 1))}


def peer_points(log_mine: float, log_other: float) -> float:
    """Metaculus' peer-score unit against one other forecast: 100 x the difference of log scores."""
    return 100 * (log_mine - log_other)


def summarise(rows: list[dict[str, Any]], bots: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"questions": len(rows)}
    for bot in bots:
        got = [r[bot] for r in rows if r.get(bot) and "log" in r[bot]]
        binary = [r[bot] for r in rows if r.get(bot) and "brier" in r[bot]]
        out[bot] = {
            "scored": len(got), "failed": sum(1 for r in rows if r.get(bot) and "error" in r[bot]),
            "mean_log": round(sum(g["log"] for g in got) / len(got), 4) if got else None,
            "mean_brier_binary": round(sum(g["brier"] for g in binary) / len(binary), 4) if binary else None,
            "usd": round(sum(r[bot].get("usd", 0) for r in rows if r.get(bot)), 4),
            "usd_per_question": round(sum(r[bot].get("usd", 0) for r in rows if r.get(bot)) / max(1, len([r for r in rows if r.get(bot)])), 4),
        }
    if len(bots) == 2:
        both = [r for r in rows if all(r.get(b) and "log" in r[b] for b in bots)]
        diffs = [peer_points(r[bots[0]]["log"], r[bots[1]]["log"]) for r in both]
        if diffs:
            mean = sum(diffs) / len(diffs)
            sd = (sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)) ** 0.5 if len(diffs) > 1 else 0.0
            out["head_to_head"] = {
                "first_minus_second_points": round(mean, 2), "n": len(diffs), "sd": round(sd, 2),
                "t": round(mean / (sd / len(diffs) ** 0.5), 2) if sd else None,
                "first_better_on": sum(1 for d in diffs if d > 0), "second_better_on": sum(1 for d in diffs if d < 0),
            }
    return out


# ---------------------------------------------------------------------- the harness

def pick(questions: list[MetaculusQuestion], n: int) -> list[MetaculusQuestion]:
    """A fixed, spread-out choice: by question id, the kinds in turn, so a re-run tests the same questions."""
    usable = [q for q in questions if q.open_time and q.resolution_string and q.typed_resolution is not None
              and type(q.typed_resolution).__name__ != "CanceledResolution"
              and isinstance(q, (BinaryQuestion, NumericQuestion, MultipleChoiceQuestion))]
    usable.sort(key=lambda q: q.id_of_question or 0)
    kinds = [[q for q in usable if isinstance(q, k)] for k in (BinaryQuestion, NumericQuestion, MultipleChoiceQuestion)]
    quotas = [max(1, round(n * 0.6)), max(1, round(n * 0.25)), max(1, round(n * 0.15))]
    chosen: list[MetaculusQuestion] = []
    for group, quota in zip(kinds, quotas):
        if not group:
            continue
        step = max(1, len(group) // quota)
        chosen += group[::step][:quota]
    return chosen[:n]


def _score(question: MetaculusQuestion, prediction: Any) -> dict[str, Any]:
    resolution = question.typed_resolution
    if isinstance(question, BinaryQuestion):
        return {"forecast": round(float(prediction), 4), **score_binary(float(prediction), bool(resolution))}
    if isinstance(question, MultipleChoiceQuestion):
        probabilities = {o.option_name: o.probability for o in prediction.predicted_options}
        return {"forecast": {k: round(v, 3) for k, v in probabilities.items()}, **score_options(probabilities, str(resolution))}
    cdf = prediction.get_cdf()
    res = resolution if isinstance(resolution, (int, float)) else str(getattr(resolution, "value", resolution))
    xs, heights = [p.value for p in cdf], [p.percentile for p in cdf]
    quantiles = {level: next((x for x, h in zip(xs, heights) if h >= level), xs[-1]) for level in (0.05, 0.25, 0.5, 0.75, 0.95)}
    return {"forecast": {f"p{int(k * 100)}": round(v, 4) for k, v in quantiles.items()}, **score_cdf(xs, heights, res)}


class _Frozen(datetime):
    """`datetime` whose now() is the test's as-of instant, for the template's prompts ("Today is ...")."""
    at: datetime = datetime.now(timezone.utc)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.at.astimezone(tz) if tz else cls.at.replace(tzinfo=None)


class TemplateBaseline(FallTemplateBot2026):
    """The template's default bot, given the test's shared news text instead of searching for itself."""
    shared: dict[str, str] = {}

    async def run_research(self, question: MetaculusQuestion) -> str:
        return self.shared.get(str(question.id_of_question or question.page_url), "")


def _baseline_bot(shared: dict[str, str]) -> TemplateBaseline:
    # What forecasting-tools picks for the template when only an OpenRouter and an AskNews key are set.
    TemplateBaseline.shared = shared
    return TemplateBaseline(
        research_reports_per_question=1, predictions_per_research_report=5, use_research_summary_to_forecast=False,
        publish_reports_to_metaculus=False, skip_previously_forecasted_questions=False,
        llms={
            "default": metering.MeteredLlm(model="openrouter/openai/gpt-4o", temperature=0.3),
            "summarizer": metering.MeteredLlm(model="openrouter/openai/gpt-4o-mini", temperature=0.3),
            "researcher": "asknews/news-summaries",
            "parser": metering.MeteredLlm(model="openrouter/openai/gpt-4o-mini", temperature=0.3),
        },
    )


async def run(tournament: str | int, n: int, profile: str, with_baseline: bool, max_usd: float) -> dict[str, Any]:
    client = MetaculusClient()
    api_filter = ApiFilter(allowed_statuses=["resolved"], allowed_tournaments=[tournament], group_question_mode="exclude")
    questions = await client.get_questions_matching_filter(api_filter, num_questions=150, error_if_question_target_missed=False)
    chosen = pick(questions, n)
    from collections import Counter
    print("what was read:", dict(Counter(type(q).__name__ for q in questions)),
          "| with an opening time:", sum(1 for q in questions if q.open_time),
          "| resolutions:", dict(Counter(str(q.resolution_string)[:20] for q in questions).most_common(8)))
    print(f"closed questions read: {len(questions)}; chosen: {len(chosen)} "
          f"({sum(isinstance(q, BinaryQuestion) for q in chosen)} binary, {sum(isinstance(q, NumericQuestion) for q in chosen)} numeric, "
          f"{sum(isinstance(q, MultipleChoiceQuestion) for q in chosen)} multiple choice)")

    shared: dict[str, str] = {}
    key_before = metering.key_status()
    start = metering.snapshot()
    rows: list[dict[str, Any]] = []
    for q in chosen:
        spent, _ = metering.spent_since(start)
        if spent >= max_usd:
            print(f"stopping: ${spent:.3f} spent, the test's limit is ${max_usd:.2f}")
            break
        as_of = q.open_time.astimezone(timezone.utc) + timedelta(hours=1)
        row: dict[str, Any] = {"url": q.page_url, "kind": type(q).__name__, "as_of": as_of.isoformat(), "resolution": str(q.resolution_string)}
        mine = PrizeBot(profile_name=profile, as_of=as_of, shared_research=shared, publish_reports_to_metaculus=False, skip_previously_forecasted_questions=False)
        row["prize"] = await _one(mine, q)
        row["news_chars"] = len(shared.get(str(q.id_of_question or q.page_url), ""))
        if with_baseline:
            _Frozen.at = as_of
            template_module.datetime = _Frozen  # the template's prompts read the date here
            row["template"] = await _one(_baseline_bot(shared), q)
        rows.append(row)
        print(json.dumps(row, default=str))

    bots = ["prize", "template"] if with_baseline else ["prize"]
    total, by_model = metering.spent_since(start)
    key_after = metering.key_status()
    summary = summarise(rows, bots)
    summary.update({
        "profile": profile, "tournament": str(tournament), "usd_at_list_prices": round(total, 4), "by_model": by_model,
        "key_meter_usd": None if not (key_before and key_after and key_before["usage"] is not None and key_after["usage"] is not None)
        else round(key_after["usage"] - key_before["usage"], 4),
        "key_remaining_usd": key_after["remaining"] if key_after else None,
    })
    return {"summary": summary, "rows": rows}


async def _one(bot: FallTemplateBot2026, question: MetaculusQuestion) -> dict[str, Any]:
    before = metering.snapshot()
    try:
        report = await bot.forecast_question(question)
        result = _score(question, report.prediction)
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    usd, models = metering.spent_since(before)
    result["usd"] = round(usd, 5)
    result["tokens"] = {m["model"]: [m["calls"], m["prompt_tokens"], m["completion_tokens"]] for m in models}
    return result


def main(tournament: str | int, n: int, profile: str, with_baseline: bool, max_usd: float, out: str) -> int:
    result = asyncio.run(run(tournament, n, profile, with_baseline, max_usd))
    with open(out, "w") as f:
        json.dump(result, f, indent=1, default=str)
    print("\n=== summary ===")
    print(json.dumps(result["summary"], indent=1, default=str))
    return 0 if result["rows"] else 1
