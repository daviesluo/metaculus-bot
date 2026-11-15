# Decisions

Every change to how the bot forecasts, newest first, with its reason. The tournament's rule: a change is made from
general reasoning or from the results of CLOSED questions, never after looking at the bot's forecasts on open questions.

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
