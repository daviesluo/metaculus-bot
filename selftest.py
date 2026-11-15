"""
Offline checks of the bot's own arithmetic: no key, no network. `python run_bot.py --mode selftest`.
Each one is a case worked by hand.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from types import SimpleNamespace


def main() -> int:
    import bot_config as cfg
    import markets
    import metering
    from backtest import peer_points, score_binary, score_cdf, score_options, summarise
    from forecaster import clamp_binary, floor_options, probability_from_text
    from forecasting_tools import PredictedOption, PredictedOptionList
    from run_bot import MAX_ATTEMPTS, mask, plan_questions

    checks: list[tuple[str, bool]] = []
    ok = lambda name, cond: checks.append((name, bool(cond)))  # noqa: E731
    close = lambda a, b: abs(a - b) < 1e-9  # noqa: E731

    # The written answer: the LAST probability counts; nothing readable is None.
    ok("probability: last one wins", probability_from_text("Probability: 30%\n...revised\nProbability: 42.5%") == 0.425)
    ok("probability: bold and spaces", probability_from_text("**Probability:** 7 %") == 0.07)
    ok("probability: none", probability_from_text("I think it is likely.") is None)
    ok("probability: over 100 refused", probability_from_text("Probability: 250%") is None)
    ok("clamp", clamp_binary(0.999) == cfg.BINARY_CEILING and clamp_binary(0.0) == cfg.BINARY_FLOOR and clamp_binary(0.4) == 0.4)

    # Options: none under the floor, the total 1, the order kept.
    floored = floor_options(PredictedOptionList(predicted_options=[
        PredictedOption(option_name="A", probability=0.999), PredictedOption(option_name="B", probability=0.001), PredictedOption(option_name="C", probability=0.0)]))
    ps = [o.probability for o in floored.predicted_options]
    ok("options: floor and total", min(ps) >= cfg.OPTION_FLOOR - 1e-9 and abs(sum(ps) - 1) < 1e-6 and ps[0] > 0.96)

    # Scores. Binary: 80 % on a Yes is ln 0.8 and Brier 0.04; on a No, ln 0.2 and 0.64.
    ok("score: binary yes", close(score_binary(0.8, True)["log"], math.log(0.8)) and close(score_binary(0.8, True)["brier"], 0.04))
    ok("score: binary no", close(score_binary(0.8, False)["log"], math.log(0.2)) and close(score_binary(0.8, False)["brier"], 0.64))
    ok("score: options", close(score_options({"A": 0.5, "B": 0.5}, "B")["log"], math.log(0.5)))
    xs = [i / 10 for i in range(11)]  # 10 bins over 0..1
    flat = [0.05 + 0.9 * i / 10 for i in range(11)]  # 5 % below, 5 % above, flat inside
    ok("score: flat cdf is 0.9 of flat", close(score_cdf(xs, flat, 0.55)["log"], math.log(0.09 * 10)))
    ok("score: tails against 5 %", close(score_cdf(xs, flat, "above_upper_bound")["log"], 0.0) and close(score_cdf(xs, flat, -1.0)["log"], 0.0))
    sharp = [0.0] * 5 + [1.0] * 6  # everything in the bin 0.4..0.5
    ok("score: sharp and right", close(score_cdf(xs, sharp, 0.45)["log"], math.log(10)))
    ok("score: sharp and wrong is floored", score_cdf(xs, sharp, 0.85)["log"] < -10)
    ok("peer points", close(peer_points(math.log(0.8), math.log(0.4)), 100 * math.log(2)))
    rows = [{"prize": {"log": math.log(0.8), "brier": 0.04, "usd": 0.02}, "template": {"log": math.log(0.4), "brier": 0.36, "usd": 0.1}},
            {"prize": {"log": math.log(0.5), "usd": 0.02}, "template": {"error": "x", "usd": 0.05}}]
    s = summarise(rows, ["prize", "template"])
    ok("summary", s["prize"]["scored"] == 2 and s["template"]["failed"] == 1 and s["head_to_head"]["n"] == 1
       and close(s["head_to_head"]["first_minus_second_points"], round(100 * math.log(2), 2)) and s["template"]["usd"] == 0.15)

    # Markets: only open Yes/No markets; the anchor only from Polymarket with volume, never at the rails; the blend.
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    payload = {"events": [{"title": "E", "slug": "e", "endDate": "2026-12-31T00:00:00Z", "markets": [
        {"question": "Open one?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.30", "0.70"]', "volume": "50000", "active": True, "closed": False, "bestBid": 0.31, "bestAsk": 0.33, "endDate": "2026-12-31T00:00:00Z"},
        {"question": "Closed one?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0", "1"]', "volume": "9", "active": True, "closed": True},
        {"question": "Ended one?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.5", "0.5"]', "active": True, "closed": False, "endDate": "2026-01-01T00:00:00Z"},
        {"question": "Not yes/no", "outcomes": '["Up", "Down"]', "outcomePrices": '["0.5", "0.5"]', "active": True, "closed": False}]}]}
    pm = markets.parse_polymarket(payload, now)
    ok("polymarket: one open market, at the mid", len(pm) == 1 and close(pm[0].probability, 0.32) and pm[0].volume_usd == 50000)
    mf = markets.parse_manifold([{"question": "Q?", "probability": 0.6, "outcomeType": "BINARY", "uniqueBettorCount": 20, "closeTime": 1800000000000, "url": "u"},
                                 {"question": "MC", "outcomeType": "MULTIPLE_CHOICE"}])
    ok("manifold: binary only", len(mf) == 1 and mf[0].volume_usd is None)
    both = pm + mf
    ok("match: named, inverted", markets.parse_match('{"match": 1, "inverted": true, "why": "x"}', both) == (pm[0], True))
    ok("match: none, junk, out of range", markets.parse_match('{"match": null}', both) is None and markets.parse_match("no json", both) is None
       and markets.parse_match('{"match": 9}', both) is None and markets.parse_match('{"match": true}', both) is None)
    ok("anchor: polymarket with volume", close(markets.anchor_probability((pm[0], False)), 0.32) and close(markets.anchor_probability((pm[0], True)), 0.68))
    ok("anchor: never manifold, never thin, never at the rails", markets.anchor_probability((mf[0], False)) is None
       and markets.anchor_probability((markets.Market("Polymarket", "t", 0.3, 100.0, None, None, "u"), False)) is None
       and markets.anchor_probability((markets.Market("Polymarket", "t", 0.999, 1e6, None, None, "u"), False)) is None
       and markets.anchor_probability(None) is None)
    ok("blend", close(markets.blend(0.6, 0.2), cfg.MARKET_WEIGHT * 0.2 + (1 - cfg.MARKET_WEIGHT) * 0.6) and markets.blend(0.6, None) == 0.6)
    ok("queries", markets.parse_queries("1. Fed rate cut December 2026\n- \"FOMC decision\"\n\n", "fallback") == ["Fed rate cut December 2026", "FOMC decision"]
       and markets.parse_queries("", "the fallback title") == ["the fallback title"])

    # Money: a call's list-price cost, a profile's worst case, and the plan under a budget.
    metering._prices = dict(metering._FALLBACK_PRICES)
    ok("cost: tokens", close(metering.call_cost("openrouter/openai/gpt-6.1-sol", 5000, 8000), 5000 * 2e-6 + 8000 * 10e-6))
    ok("cost: a search model adds its request fee", close(metering.call_cost("openrouter/perplexity/sonar", 1000, 1000, 2), 0.002 + 0.01))
    lean_worst = metering.worst_case_question_cost(cfg.PROFILES["lean"])
    ok("cost: lean worst case under 5 cents", 0.01 < lean_worst < 0.05)
    q = lambda i: SimpleNamespace(id_of_question=i)  # noqa: E731
    cfg_profile = dict(cfg.TOURNAMENT_PROFILE)
    cfg.TOURNAMENT_PROFILE.update({"minibench": "lean", "season": "lean"})
    todo = {"minibench": [q(1), q(2)], "season": [q(3), q(4), q(5)]}
    taken, left = plan_questions(todo, {"2": MAX_ATTEMPTS}, cfg.MIN_KEY_REMAINING_USD + 2.5 * lean_worst)
    ok("plan: MiniBench first, a twice-failed question left, the budget's last question left",
       [x.id_of_question for x in taken["minibench"]] == [1] and [x.id_of_question for x in taken["season"]] == [3]
       and left == {"gave_up": 1, "run_full": 0, "no_budget": 2})
    taken, left = plan_questions(todo, {}, None)
    ok("plan: no meter, everything within the run's size", sum(len(v) for v in taken.values()) == 5 and left["no_budget"] == 0)
    taken, left = plan_questions({"minibench": [q(i) for i in range(40)], "season": []}, {}, None)
    ok("plan: a run is at most MAX_QUESTIONS_PER_RUN", len(taken["minibench"]) == cfg.MAX_QUESTIONS_PER_RUN and left["run_full"] == 40 - cfg.MAX_QUESTIONS_PER_RUN)
    cfg.TOURNAMENT_PROFILE.update(cfg_profile)
    ok("mask: no digit of a forecast reaches a public log", mask("Probability: 73% \n on 2026") == "Probability: ##% on ####")

    bad = [name for name, good in checks if not good]
    for name, good in checks:
        print(("ok   " if good else "FAIL ") + name)
    print(f"{len(checks) - len(bad)} of {len(checks)} passed")
    return 1 if bad else 0
