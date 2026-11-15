"""
The bot's entry point.

  python run_bot.py --mode tournament        the scheduled run: forecast new questions of MiniBench and the season
  python run_bot.py --mode test_questions    publish on Metaculus' bot-testing area (not a prize tournament)
  python run_bot.py --mode backtest          forecast CLOSED questions as of their opening and score them (no publishing)
  python run_bot.py --mode selftest          offline checks of the bot's own arithmetic (no key, no network)

The scheduled run prints which questions it forecast and what it spent, never a forecast or its reasoning: the
repository's logs are public, and the tournament forbids its maker from previewing forecasts on open questions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from bot_helpers import check_environment, silence_noisy_dependencies

silence_noisy_dependencies()

import bot_config as cfg  # noqa: E402
import metering  # noqa: E402

STATE_FILE = Path(".state/attempts.json")
MAX_ATTEMPTS = 2  # a question that failed twice is left alone (and reported), so a broken one cannot spend all night


def mask(text: str, length: int = 160) -> str:
    """An error message fit for a public log: one line, short, every digit hidden (it may quote a forecast)."""
    return re.sub(r"\d", "#", " ".join(text.split()))[:length]


def _load_attempts() -> dict[str, int]:
    try:
        return {str(k): int(v) for k, v in json.loads(STATE_FILE.read_text()).items()}
    except Exception:
        return {}


def _save_attempts(attempts: dict[str, int], before: dict[str, int]) -> None:
    if attempts == before:
        return
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(attempts, sort_keys=True))
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write("state_changed=true\n")


def plan_questions(todo: dict[str, list], attempts: dict[str, int], remaining_usd: float | None) -> tuple[dict[str, list], dict[str, int]]:
    """
    Which of the unforecast questions this run takes, by tournament, and what it leaves and why.
    MiniBench first. A question is left when it has already failed MAX_ATTEMPTS times, when the run is full, or when
    the key's remaining budget (less a reserve) does not cover its profile's worst case.
    """
    taken: dict[str, list] = {name: [] for name in todo}
    left = {"gave_up": 0, "run_full": 0, "no_budget": 0}
    budget = None if remaining_usd is None else remaining_usd - cfg.MIN_KEY_REMAINING_USD
    count = 0
    for name, questions in todo.items():
        worst = metering.worst_case_question_cost(cfg.PROFILES[cfg.TOURNAMENT_PROFILE[name]])
        for q in questions:
            if attempts.get(str(q.id_of_question), 0) >= MAX_ATTEMPTS:
                left["gave_up"] += 1
            elif count >= cfg.MAX_QUESTIONS_PER_RUN:
                left["run_full"] += 1
            elif budget is not None and budget < worst:
                left["no_budget"] += 1
            else:
                taken[name].append(q)
                count += 1
                if budget is not None:
                    budget -= worst
    return taken, left


def run_tournament() -> int:
    if not cfg.LIVE:
        print("Not launched: bot_config.LIVE is False. Nothing read, nothing spent.")
        return 0
    check_environment(strict=True)
    from forecasting_tools import ForecastReport, MetaculusClient

    from forecaster import PrizeBot

    client = MetaculusClient()
    tournaments = {"minibench": client.CURRENT_MINIBENCH_ID, "season": client.CURRENT_AI_COMPETITION_ID}
    todo = {name: [q for q in client.get_all_open_questions_from_tournament(tid) if not q.already_forecasted] for name, tid in tournaments.items()}
    key = metering.key_status()
    remaining = key["remaining"] if key else None
    if key is None:
        print("WARNING: the OpenRouter key's meter could not be read; this run is limited by MAX_QUESTIONS_PER_RUN and the key's own cap.")
    before_attempts = _load_attempts()
    attempts = dict(before_attempts)
    taken, left = plan_questions(todo, attempts, remaining)
    for questions in taken.values():
        for q in questions:
            attempts[str(q.id_of_question)] = attempts.get(str(q.id_of_question), 0) + 1
    _save_attempts(attempts, before_attempts)  # before any money is spent: a crash still counts as an attempt

    start = metering.snapshot()

    async def forecast_all() -> dict[str, list]:
        results: dict[str, list] = {}
        for name, questions in taken.items():
            if not questions:
                results[name] = []
                continue
            bot = PrizeBot(profile_name=cfg.TOURNAMENT_PROFILE[name], publish_reports_to_metaculus=True,
                           skip_previously_forecasted_questions=True, extra_metadata_in_explanation=True)
            results[name] = await bot.forecast_questions(questions, return_exceptions=True)
            results[name + "_anchored"] = [bot.anchored, dict(bot.gaps)]
        return results

    results = asyncio.run(forecast_all())
    failed = 0
    after_attempts = dict(attempts)
    print("=" * 80)
    for name in tournaments:
        reports = results.get(name, [])
        ok = [r for r in reports if isinstance(r, ForecastReport)]
        bad = [r for r in reports if isinstance(r, BaseException)]
        failed += len(bad)
        extra = results.get(name + "_anchored") or [0, {}]
        print(f"{name}: {len(todo[name])} unforecast, {len(taken[name])} taken, {len(ok)} forecast, {len(bad)} failed"
              f" (profile {cfg.TOURNAMENT_PROFILE[name]}; anchored on a market: {extra[0]}; research that failed, by source: {extra[1]})")
        for r in ok:
            print(f"  ok   {r.question.page_url}")
            after_attempts.pop(str(r.question.id_of_question), None)
        for r in bad:
            print(f"  FAIL {type(r).__name__}: {mask(str(r))}")
    _save_attempts(after_attempts, attempts)
    spent, by_model = metering.spent_since(start)
    done = sum(1 for name in tournaments for r in results.get(name, []) if isinstance(r, ForecastReport))
    print(f"spent at list prices: ${spent:.4f}" + (f" (${spent / done:.4f} a question)" if done else ""))
    for m in by_model:
        print(f"  {m['model']}: {m['calls']} calls, {m['prompt_tokens']} in, {m['completion_tokens']} out, ${m['usd']}")
    key_after = metering.key_status()
    if key_after:
        print(f"OpenRouter key: ${key_after['usage']} used of {key_after['limit']} (remaining {key_after['remaining']})")
    print(f"left for later: {left}")
    print("=" * 80)
    if failed or left["no_budget"] or left["gave_up"]:
        if left["no_budget"]:
            print("FAILING THE RUN: the OpenRouter budget does not cover the questions left. Raise the key's cap.")
        if left["gave_up"]:
            print(f"FAILING THE RUN: {left['gave_up']} question(s) failed {MAX_ATTEMPTS} times and are no longer tried.")
        return 1
    return 0


def run_test_questions(limit: int) -> int:
    check_environment(strict=True)
    from forecasting_tools import ForecastReport, MetaculusClient

    from forecaster import PrizeBot

    client = MetaculusClient()
    questions = client.get_all_open_questions_from_tournament("bot-testing-area")[:limit]
    start = metering.snapshot()
    bot = PrizeBot(profile_name="lean", publish_reports_to_metaculus=True, skip_previously_forecasted_questions=False)
    reports = asyncio.run(bot.forecast_questions(questions, return_exceptions=True))
    for q, r in zip(questions, reports):
        print(("ok   " if isinstance(r, ForecastReport) else f"FAIL {type(r).__name__}: {str(r)[:300]} ") + f"{type(q).__name__} {q.page_url}")
    spent, by_model = metering.spent_since(start)
    print(f"spent at list prices: ${spent:.4f}; by model: {json.dumps(by_model)}")
    return 0 if reports and all(isinstance(r, ForecastReport) for r in reports) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the forecasting bot")
    parser.add_argument("--mode", choices=["tournament", "test_questions", "backtest", "selftest"], default="tournament")
    parser.add_argument("--n", type=int, default=6, help="backtest / test_questions: how many questions")
    parser.add_argument("--profile", default="lean", choices=sorted(cfg.PROFILES), help="backtest: the bot's profile")
    parser.add_argument("--tournament", default="season,minibench,cup,summer",
                        help="backtest: tournaments tried in turn until enough closed questions show their resolution: "
                             "'season', 'minibench', 'cup', 'summer', or ids or slugs, comma-separated")
    parser.add_argument("--opened-after", default="2026-08-15", help="backtest: only questions opened after this day (later than the models' training data)")
    parser.add_argument("--no-baseline", action="store_true", help="backtest: skip the template's default bot")
    parser.add_argument("--max-usd", type=float, default=1.0, help="backtest: stop opening questions past this spend")
    parser.add_argument("--out", default="backtest_results.json")
    args = parser.parse_args()

    if args.mode == "selftest":
        import selftest

        sys.exit(selftest.main())
    live = args.mode == "tournament"
    logging.basicConfig(level=logging.WARNING if live else logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if live:
        # The library logs research, reasoning and forecasts, and quotes them in its warnings; none of that belongs in
        # a public log. The run's own summary says what failed, with digits hidden.
        for name in ("forecasting_tools", "main", "forecaster", "markets", "litellm", "LiteLLM"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        sys.exit(run_tournament())
    if args.mode == "test_questions":
        sys.exit(run_test_questions(args.n))
    check_environment(strict=True)
    import backtest
    from forecasting_tools import MetaculusClient

    from datetime import datetime, timezone

    ids = {"season": MetaculusClient.CURRENT_AI_COMPETITION_ID, "minibench": MetaculusClient.CURRENT_MINIBENCH_ID,
           "cup": MetaculusClient.CURRENT_METACULUS_CUP_ID, "summer": MetaculusClient.FE_SUMMER_2026_ID}
    wanted = [ids.get(t.strip(), t.strip()) for t in args.tournament.split(",") if t.strip()]
    opened_after = datetime.fromisoformat(args.opened_after).replace(tzinfo=timezone.utc)
    sys.exit(backtest.main(wanted, args.n, args.profile, not args.no_baseline, args.max_usd, args.out, opened_after))
