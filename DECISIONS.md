# Decisions

Every change to how the bot forecasts, newest first, with its reason. The tournament's rule: a change is made from
general reasoning or from the results of CLOSED questions, never after looking at the bot's forecasts on open questions.

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
- **MiniBench's money is kept from the season.** A MiniBench round is a fresh contest; the season was joined half way,
  where a prize (the square of the summed score) is out of reach. The season is forecast only while the key holds more
  than one MiniBench round's cost (`SEASON_RESERVE_USD`, $1.50 at `lean`).
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
