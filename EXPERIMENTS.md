# Experiments

What was tested, on what, and what it showed. No secret appears here. Every test used closed questions (resolved
Polymarket markets) or Metaculus' bot-testing area, which is not a prize tournament. All of it is 2026-11-15.

## In short

| what | result |
|---|---|
| Does every kind of question go through, and get posted? | Yes: binary, numeric, discrete, date and multiple choice, on the bot-testing area, with both profiles |
| Cost a question, `lean` | **$0.024** (three small models and the better web researcher, which is $0.017 of it); $0.014 with the cheaper researcher it first had |
| Cost a question, `standard` | **$0.19** (five frontier models, after the fixes below; $0.31 before them) |
| Cost a question, the template's default bot | $0.03 to $0.07 |
| With a news search, against the market's own price | `lean` was level with it on 5 forecasts (3 questions twice over) |
| Without any research | far worse than the market: −77 points a question (`lean`), −43 (the template) on 10 questions. **The bot now refuses to forecast blind** |
| Against the template's default bot | not answered: 2 questions with news (level), 10 without (the template less bad, −34 points to us) |

**What the tests could not do.** Metaculus shows this account no resolution on any resolved question (the season, the
Cup, the summer's bot tournament), so no Metaculus question could be scored. The news search stopped after five
searches (its prepaid wallet is empty), so only five forecasts were made the way the bot was designed to make them. The
web research and the market anchor need the present and cannot be tested on the past. So the evidence on accuracy is
thin; the evidence on plumbing and cost is good.

## 1. Offline checks

`python run_bot.py --mode selftest`: 32 checks of the bot's arithmetic, each a case worked by hand, all passing (also on
GitHub's runner): reading a written probability, the clamps, the option floor, the three scores, the market parsers,
the same-question check, the anchor's conditions, the blend, list-price cost, a profile's worst case, the run's plan
under a budget, the public log's mask, and what counts as an answer.

The whole flow was also run with stand-in models and no network: a binary median of 30 % with a matching market at 10 %
gives 20 %, as designed.

## 2. Closed questions: resolved Polymarket markets

**How.** "Test Bot", mode `backtest`. Well-traded Yes/No markets that ended in the last 30 days, one an event, the most
traded first, sports and price markets left out; each forecast **as of seven days before its end**, and only if the
market's price then stood between 8 % and 92 %. The news search is cut off at that instant. The web search and the
market search cannot be cut off, so both are left out, and the bot is never shown the market's price. The template's
default bot (GPT-4o five times, its own prompts) gets the same news text. Scores: the log of the probability given to
what happened, and differences in Metaculus' peer-score unit (100 × the difference of logs). The market's price on the
test's day is the benchmark.

**Run 1** (`lean`, 3 questions, news found for all 3):

| question | happened | market | `lean` |
|---|---|---|---|
| a candidate wins Brazil's presidency | no | 78.7 % | 76 % |
| one party controls the US House | yes | 91.5 % | 92 % |
| the Fed leaves rates unchanged in October | yes | 87.5 % | 84 % |

`lean` +2.7 points a question against the market (n = 3: nothing).

**Run 2** (`lean` and the template, 12 questions). The news search answered for the first two and then refused every
call ("wallet balance is depleted"), so ten questions were forecast with no research at all.

| | questions | `lean` | template | market |
|---|---|---|---|---|
| with news: mean log score | 2 | −0.83 | −0.65 | −0.82 |
| **blind: mean log score** | 10 | **−1.39** | **−1.05** | **−0.62** |
| all: mean log score | 12 | −1.30 | −0.99 | −0.65 |

- Blind, both bots are far worse than the market (−77 and −43 points a question), and `lean` is worse than the template
  (−34 points; over all 12, −31, t −2.0). The small models went to the extremes with nothing to go on: 3 % on an
  outcome that happened, 88 % on one that did not.
- Five of the twelve questions are one election night: fewer independent questions than twelve.
- **What it changed** (DECISIONS.md): no forecast without research; the prompt holds a guess inside 10 to 90 %; the
  better web researcher in both profiles.

## 3. The live path, on the bot-testing area

"Test Bot", mode `test_questions`: the full live path (web research, market search, forecasts, the median, posting).
The news search was refused throughout (its wallet), so this is the bot on web research alone.

| run | posted | cost a question, at list prices | what it showed |
|---|---|---|---|
| `lean` with the cheaper web researcher, 4 questions | 4 of 4: discrete, numeric, date, binary | $0.0142 | the path works |
| `standard` as first built, 5 questions | 5 of 5, multiple choice included | $0.305 | Gemini 4 Carbon spent its whole 12,000-token cap on reasoning and gave no answer in 3 calls of 5 ($0.13 a call); the library passed its reasoning on as an answer and a parser read 0 % from it; Grok 4.7 used 15,000 tokens a call ($0.10) |
| `standard` rebuilt, 2 questions | 2 of 2 | $0.191 | no empty answer; Gemini within its reasoning cap |
| `lean` as launched (the better web researcher), 4 questions | 4 of 4 | $0.0242 | the web researcher is $0.017 of it |

The cost is counted from each call's tokens at OpenRouter's list prices. The key's own meter agrees but runs late: read
straight after a run it showed 85 to 95 % of the list-price figure, and the rest arrived before the next run (the last
run: $0.0968 counted, $0.0987 on the meter). The bot's budget guard reads the meter before a run and plans on each
question's worst case, so a late meter cannot let a run overspend by more than the run before it left uncounted.

Tokens a question, `standard` rebuilt: about 18,000 in and 12,000 out over five forecasters, 500 in and 700 out for
the web researcher, 17,000 in and 1,600 out for the parser's 10 small calls.

## 4. What the tests cost

About $2.49 of OpenRouter spend in all (the key's own meter), $1.5 of it the one run of the first `standard`. The
news search's wallet was emptied by five date-bounded archive searches; its balance could not be read beforehand.

## 5. Budget arithmetic

| | questions | `lean` at $0.024 | `standard` at $0.19 |
|---|---|---|---|
| one MiniBench round | about 60 | $1.45 | $11.40 |
| the season from mid-November to 6 January (3 to 5 new questions a day) | 160 to 260 | $4 to $6 | $30 to $49 |
| a whole season | 300 to 500 | $7 to $12 | $57 to $95 |

The question counts are estimates (a MiniBench round's from Metaculus' description of it, the season's from its pace so
far); the first live runs will show the real ones. A news search, when it is funded, adds prompt tokens: about a cent a
question on `standard`, a tenth of a cent on `lean`.
