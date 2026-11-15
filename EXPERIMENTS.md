# Experiments

What was tested, on what, and what it showed. No secret appears here. Tests use closed questions only (resolved
tournament questions, or Metaculus' bot-testing area).

## 1. Offline checks (2026-11-15)

`python run_bot.py --mode selftest`: 30 checks of the bot's arithmetic, each a case worked by hand, all passing:
reading a written probability, the clamps, the option floor, the three scores, the market parsers, the same-question
check's answer, the anchor's conditions, the blend, list-price cost, a profile's worst case, and the run's plan under a
budget (MiniBench first, a twice-failed question left, nothing the budget does not cover).

The whole flow was also run with stand-in models and no network (research, three forecasts, the median, the anchor, a
numeric curve, a multiple-choice list): a binary median of 30 % with a matching market at 10 % gives 20 %, as designed.

## 2. Closed questions against the template's default bot

**How.** "Test Bot", mode `backtest`: resolved questions of the season's tournament, a fixed spread by question id
(about 60 % binary, 25 % numeric, 15 % multiple choice). Each is forecast as of one hour after it opened: the news
search is cut off at that instant; the web search and the markets cannot be cut off, so both are left out. Both bots get
the same news text. Scores: the log of the probability given to what happened (for a numeric question, of the bin the
value fell in, against a flat forecast), the Brier score on binary questions, and the difference between the two bots in
Metaculus' peer-score unit (100 x the difference of logs). Cost: tokens by model at OpenRouter's list prices, beside the
key's own meter.

**What it can and cannot show.** It compares the forecasters (models, prompts, clamps, the median) under the same
news. It does not test the web research or the market anchor, which need the present. A dozen questions cannot separate
two bots statistically; the test's first job is to prove every question type end to end and to measure the cost.

**Results.** Not run yet: GitHub Actions are off on this fork until its owner enables them.

## 3. The publishing path

"Test Bot", mode `test_questions`: the bot posts on the bot-testing area. Not run yet (as above).
