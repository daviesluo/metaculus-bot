"""
Everything a person might change about the bot, in one place.

The rule of the tournament: a change here is made from general reasoning or from the results of CLOSED questions only,
never after looking at the bot's forecasts on open questions. Every change is recorded in DECISIONS.md.
"""
from __future__ import annotations

# The schedule forecasts on the tournaments only while this is True. It is switched on by a commit, once the tests on
# closed questions in EXPERIMENTS.md are in. Off, the scheduled run reports "not launched" and spends nothing.
LIVE = False

# Forecasts never go outside this band on a binary question, nor under the floor on any option of a multiple choice
# question. A log score punishes a confident miss far more than it rewards the last point of confidence: 99 % against
# 98 % earns one point when right and loses 69 when wrong.
BINARY_FLOOR, BINARY_CEILING = 0.02, 0.98
OPTION_FLOOR = 0.01

# A prediction market that asks the same question is the best single forecast there is. When one is found, the final
# binary forecast is this share of the market's price and the rest the models' median.
MARKET_WEIGHT = 0.5
# What counts as a market worth anchoring on: dollars traded on Polymarket. Manifold is play money: it is shown to the
# models as evidence and never anchored on.
MARKET_MIN_VOLUME_USD = 20_000

# The parser turns a model's written answer into numbers; the scout writes search queries and judges market matches.
PARSER_MODEL = "openrouter/openai/gpt-4o-mini"
SCOUT_MODEL = "openrouter/openai/gpt-6-luna"


def _f(model: str, effort: str = "high", max_tokens: int = 12_000, reasoning_tokens: int | None = None) -> dict:
    return {"model": f"openrouter/{model}", "effort": effort, "max_tokens": max_tokens, "reasoning_tokens": reasoning_tokens}


# A profile is what is spent on one question: the web researcher and the forecasters, whose median is the forecast.
# `max_tokens` caps a call's output, reasoning included, so the worst cost of a question is known before it is asked.
PROFILES: dict[str, dict] = {
    # A few cents a question: for a small budget, and for the season the bot joined half way. The web researcher is the
    # better one all the same: with no news search it is the only research there is, and a forecast is as good as it.
    "lean": {
        "web_researcher": "openrouter/perplexity/sonar-pro",
        "forecasters": [
            _f("openai/gpt-6-luna"),
            _f("anthropic/claude-haiku-5.5"),
            _f("openai/gpt-6-luna"),
        ],
    },
    # Frontier models from three labs at high reasoning. Gemini's reasoning has its own cap: at "high" it spent a whole
    # 12,000-token output on reasoning and gave no answer in 3 calls of 5 (EXPERIMENTS.md); Grok 4.7 at "high" cost five
    # times what the others did, and is left out.
    "standard": {
        "web_researcher": "openrouter/perplexity/sonar-pro",
        "forecasters": [
            _f("openai/gpt-6.1-sol"),
            _f("anthropic/claude-opus-5.5"),
            _f("google/gemini-4-carbon", max_tokens=9_000, reasoning_tokens=5_000),
            _f("anthropic/claude-sonnet-5.5"),
            _f("openai/gpt-6.1-terra"),
        ],
    },
}

# Which profile each tournament gets. MiniBench is a fresh contest every two weeks; the season was joined half way,
# where a prize is out of reach (a prize is the square of the summed score), so it gets the cheap profile.
TOURNAMENT_PROFILE = {"minibench": "lean", "season": "lean"}

# Budget guards. The OpenRouter key has its own hard cap; these keep a run from reaching it unnoticed.
MAX_QUESTIONS_PER_RUN = 25
# A run stops opening questions when the key has less than this left, and fails so that it is seen.
MIN_KEY_REMAINING_USD = 0.25
# The season is forecast only while the key holds more than this as well: about what one MiniBench round costs on its
# profile (60 questions). MiniBench is where a prize can be won, so the season never spends its money. A season
# question held back for this is a warning on the run, not a failure.
SEASON_RESERVE_USD = 1.50
