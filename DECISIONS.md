# Decisions

Every change to how the bot forecasts, newest first, with its reason. The tournament's rule: a change is made from
general reasoning or from the results of CLOSED questions, never after looking at the bot's forecasts on open questions.

## 2026-11-15: the bot keeps itself running (how it runs, not how it forecasts)

GitHub started no scheduled run in the two and a half hours after the workflow was pushed, through an enable call and a
change of the cron line; runs started by hand worked every time. Others report the same on new repositories and forks,
for days. A question is open for three hours, so a schedule that may not fire is no schedule. A run now looks every 15
minutes for about 50 minutes and starts its successor; the cron line stays as the restart for a broken chain.

Later the same day, after six runs had handed over without a miss and the schedule had still started nothing: the
successor is started near the start of a run and waits behind it, so a run that dies does not end the chain; each look
uses the newest commit, so `LIVE = False` stops the bot within 15 minutes; the budget's end is an error on the run and
its approach a warning; and the news search takes either kind of AskNews credential. No tournament question had been
open since launch, so none had been forecast.

## 2026-11-15: no money is kept from the season (the day of launch, before any forecast)

At launch the season was allowed to spend only above $1.50, kept for a MiniBench round, on the reasoning that the
season's backlog would otherwise take a round's money and that a season joined half way cannot win. The first count of
the tournaments (`--mode status`) showed both wrong:

- every season question is open for exactly three hours, one batch every three hours, every day: there is no backlog,
  and the season spends about four questions a day;
- the money is in the season: $50,000 against $1,000 a MiniBench round. A prize is the square of the summed score, so a
  bot that plays the second half earns about a quarter of what it would have earned over the whole season: less, not
  nothing.

`SEASON_RESERVE_USD` is 0. No forecast had been made.

## 2026-11-15: launched

`LIVE = True`. On what evidence (EXPERIMENTS.md):

- every kind of question went through the live path and was posted on the bot-testing area, with the profile as
  launched (4 of 4, $0.024 a question);
- with a news search the forecasts were level with a real-money market's own price on resolved questions (5 forecasts:
  little, and all there is);
- without research they were far worse, and the bot no longer forecasts without research.

What is not known at launch: whether these forecasts beat the other bots'. The tests could not show it (no Metaculus
resolution is shown to this account; the news search ran out of money after five searches). The stop rule in README.md
is the answer to that: two MiniBench rounds below the middle of the table, read from final results, and it is off.

Launched on `lean` for both tournaments, MiniBench first. From here on, no
change is made from a forecast on an open question.

## 2026-11-15: after the tests on closed questions and the bot-testing area (before launch)

From EXPERIMENTS.md §2 and §3. No tournament question had been forecast.

- **No forecast without research.** Blind forecasts lost 77 points a question to the market's price (10 resolved
  Polymarket markets). If the news search and the web research both come back empty, the question is not forecast: it
  fails, is reported, and is tried once more.
- **A guess stays inside 10 to 90 %** (prompt): the small models answered 3 % and 88 % with nothing to go on, and were
  wrong.
- **The news search is optional.** Its wallet is empty; a refused key is not asked again within a run. Until it is
  funded, the web researcher is the only research, so both profiles use the better one (sonar-pro).
- **An answer must be a written answer.** A reply with reasoning and no answer is an error, and a binary forecast must
  end with its probability; either way that forecaster is left out of the median instead of being guessed at. A
  forecaster is tried once.
- **The standard profile rebuilt**: Gemini 4 Carbon with a reasoning cap of its own (it spent a 12,000-token output on
  reasoning and answered nothing in 3 calls of 5); Grok 4.7 out (five times the others' cost at high reasoning), Opus
  5.5 in. $0.19 a question, from $0.31.
- **Both tournaments on `lean` for now**: the budget in hand covers nothing else. `standard` on MiniBench is the plan
  once the budget allows; that switch needs no new evidence (frontier models are the first of Metaculus' own findings).

## 2026-11-15: the first design (before any forecast)

From general reasoning and Metaculus' own published findings about earlier seasons; no question of the bot's exists yet.

- **Several models, one forecast each, the median.** Metaculus' season reviews: the model matters most; small ensembles
  do as well as large ones; a high-reasoning run beat its standard twin in every pair compared. So: frontier models from
  different labs at high reasoning rather than one model five times.
- **Two research sources.** Winning bots averaged 1.7 sources against 1.0 for the rest: AskNews for news, a
  web-searching model for the resolution source's current state and base rates.
- **Prediction markets as evidence, and as an anchor on an exact match.** Where a liquid real-money market asks the
  same question, its price is a better forecast than a model's; a binary forecast then moves half way to it. A
  separate, strict check decides "the same question" (event, threshold, deadline); play-money markets and thin markets
  never anchor. The Metaculus community prediction is not read.
- **No extremes.** Binary forecasts stay within 2 % and 98 %; every option of a multiple-choice question gets at least
  1 %. Under a log score a confident miss costs about 69 points against 1 point earned by the last point of confidence.
- **Wider numeric answers.** Nine percentiles from the 5th to the 95th instead of six from the 10th to the 90th, and an
  instruction to set wide intervals: bots are more often too narrow than too wide.
- **The Metaculus Cup workflow removed**: no prize for bots there.
- **Profiles.** `lean` (cents a question) and `standard` (frontier models). Which tournament gets which is in
  `bot_config.TOURNAMENT_PROFILE`; it starts on `lean` everywhere until the measured cost a question and the budget are
  known (EXPERIMENTS.md).
