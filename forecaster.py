"""
The prize bot. It keeps the template's structure (research once, several forecasts, the median) and changes five things:

1. several different frontier models at high reasoning instead of one model five times;
2. two research sources (AskNews and a web-searching model) instead of one;
3. prediction markets as evidence, and as an anchor when one asks exactly the same question;
4. prompts that put the resolution criteria, the base rate and the market first;
5. no forecast at the extremes (bot_config.BINARY_FLOOR / OPTION_FLOOR).

`as_of` runs it as if it were an earlier day, for tests on CLOSED questions: the news search is cut off at that
instant and the web search and the markets, which cannot be cut off, are left out.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import re
import statistics
from datetime import datetime, timedelta, timezone

from asknews_sdk import AsyncAskNewsSDK
from forecasting_tools import (
    AskNewsSearcher,
    BinaryPrediction,
    BinaryQuestion,
    DatePercentile,
    DateQuestion,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericDistribution,
    NumericQuestion,
    Percentile,
    PredictedOption,
    PredictedOptionList,
    ReasonedPrediction,
    clean_indents,
    structure_output,
)

import bot_config as cfg
import markets
import metering
from main import FallTemplateBot2026

logger = logging.getLogger(__name__)

FORECASTER_RULES = """
How you work:
- Read the resolution criteria and the fine print first, word by word. Forecast exactly what they say (the source, the
  threshold, the dates and time zones), not what the headline suggests.
- Start from a base rate: how often has this kind of thing happened over a window this long? Say the number.
- Then the status quo: what happens if nothing changes? The world changes slowly; give the status quo extra weight.
- Then the news: only what actually moves the odds, and by how much. Old news is already in the base rate.
- If a prediction market asks exactly this question, it is the best single forecast you have: stay close to it unless
  you can name what it is missing. A market about something merely related is weak evidence.
- If the research is thin or contradictory, say so and stay nearer the base rate. If it tells you nothing about the
  current state of the thing asked, you are guessing: do not answer below 10% or above 90%.
- Never be more confident than your evidence. Being wrong at 98% costs far more than being right at 98% earns.
"""


def probability_from_text(text: str) -> float | None:
    """The last "Probability: ZZ%" a forecaster wrote, as 0-1; None when it wrote none that can be read."""
    found = re.findall(r"probability\s*[:=]\s*\**\s*(\d{1,3}(?:\.\d+)?)\s*%", text or "", re.I)
    if not found:
        return None
    value = float(found[-1]) / 100
    return value if 0 <= value <= 1 else None


def clamp_binary(p: float) -> float:
    return max(cfg.BINARY_FLOOR, min(cfg.BINARY_CEILING, p))


def floor_options(option_list: PredictedOptionList) -> PredictedOptionList:
    """No option under the floor; the rest scaled so the total stays 1."""
    options = option_list.predicted_options
    n = len(options)
    if not n:
        return option_list
    floor = min(cfg.OPTION_FLOOR, 1 / (2 * n))
    total = sum(max(o.probability, 0) for o in options) or 1
    shares = [max(o.probability, 0) / total for o in options]
    return PredictedOptionList(predicted_options=[
        PredictedOption(option_name=o.option_name, probability=min(1.0, floor + (1 - n * floor) * s)) for o, s in zip(options, shares)
    ])


NO_RESEARCH = "No research could be gathered."


class PrizeBot(FallTemplateBot2026):
    # Set when the news search refuses the key or its wallet is empty: the rest of the run does without it.
    _news_off = False

    def __init__(self, *, profile_name: str, as_of: datetime | None = None, shared_research: dict[str, str] | None = None, **kwargs) -> None:
        self.profile_name = profile_name
        self.profile = cfg.PROFILES[profile_name]
        self.as_of = as_of
        forecasters = self.profile["forecasters"]
        self._forecasters = [metering.llm(f["model"], f["effort"], f["max_tokens"]) for f in forecasters]
        self._parser = metering.llm(cfg.PARSER_MODEL, timeout=60)
        self._scout = metering.llm(cfg.SCOUT_MODEL, effort="low", max_tokens=2_000, timeout=60)
        self._web = metering.llm(self.profile["web_researcher"], timeout=120) if self.profile.get("web_researcher") else None
        self._turn: dict[str, itertools.count] = {}
        self._anchor: dict[str, float] = {}
        # Research already gathered for a question (a test gives two bots the same text).
        self._research: dict[str, str] = shared_research if shared_research is not None else {}
        self.anchored = 0
        # Questions whose research came back short, by source: said in the run's summary, since a log line is not.
        self.gaps = {"news": 0, "web": 0, "markets": 0}
        kwargs.setdefault("parameters_to_exclude_from_config_dict", ["shared_research"])
        kwargs.setdefault("research_reports_per_question", 1)
        kwargs["predictions_per_research_report"] = len(forecasters)
        kwargs.setdefault("enable_summarize_research", False)
        kwargs.setdefault("use_research_summary_to_forecast", False)
        kwargs.setdefault("llms", {"default": self._forecasters[0], "summarizer": self._parser, "researcher": "asknews/news-summaries", "parser": self._parser})
        super().__init__(**kwargs)

    # ------------------------------------------------------------------ helpers

    def _today(self) -> str:
        return (self.as_of or datetime.now(timezone.utc)).strftime("%Y-%m-%d")

    @staticmethod
    def _key(question: MetaculusQuestion) -> str:
        return str(question.id_of_question or question.page_url)

    def _next_forecaster(self, question: MetaculusQuestion) -> metering.MeteredLlm:
        # No await before this point in a forecast, so each of a question's forecasts takes the next model in turn.
        turn = self._turn.setdefault(self._key(question), itertools.count())
        return self._forecasters[next(turn) % len(self._forecasters)]

    # ------------------------------------------------------------------ research

    async def run_research(self, question: MetaculusQuestion) -> str:
        key = self._key(question)
        if key in self._research:
            return self._research[key]
        async with self._concurrency_limiter:
            if key in self._research:
                return self._research[key]
            parts: list[str] = []
            news = await self._news(question)
            if news:
                parts.append("## News search (AskNews)\n" + news)
            if self.as_of is None:
                web, market_text = await asyncio.gather(self._web_research(question), self._market_research(question))
                if web:
                    parts.append("## Web research\n" + web)
                if not parts:
                    # A forecast made blind lost 64 points a question to the market's price in the tests on closed
                    # questions (EXPERIMENTS.md): no forecast is better. The run reports the failure and tries again.
                    raise RuntimeError("no research: the news search and the web research both came back empty")
                parts.append("## Prediction markets\n" + market_text)
            research = "\n\n".join(parts) if parts else NO_RESEARCH
            self._research[key] = research
            return research

    async def _news(self, question: MetaculusQuestion) -> str:
        if PrizeBot._news_off:
            self.gaps["news"] += 1
            return ""
        try:
            if self.as_of is None:
                return await AskNewsSearcher().get_formatted_news_async(question.question_text)
            return await self._news_before(question.question_text, self.as_of)
        except Exception as e:
            self.gaps["news"] += 1
            if re.search(r"\b40[123]\d*\b|wallet|unauthori|forbidden|credential", str(e), re.I):
                PrizeBot._news_off = True  # no key, or nothing left to pay with: do not ask again this run
            logger.warning(f"News search failed for {question.page_url} ({type(e).__name__}: {str(e)[:120]})")
            return ""

    @staticmethod
    async def _news_before(query: str, as_of: datetime) -> str:
        """AskNews' archive up to `as_of` and no later: what a forecaster could have read that day."""
        searcher = AskNewsSearcher()
        async with AsyncAskNewsSDK(client_id=searcher.client_id, client_secret=searcher.client_secret, api_key=searcher.api_key, scopes={"news"}) as ask:
            response = await ask.news.search_news(
                query=query, n_articles=12, return_type="both", strategy="default", historical=True,
                start_timestamp=int((as_of - timedelta(days=90)).timestamp()), end_timestamp=int(as_of.timestamp()),
            )
            await asyncio.sleep(12)  # AskNews allows one call in ten seconds
        articles = [a for a in response.as_dicts if a.pub_date.replace(tzinfo=a.pub_date.tzinfo or timezone.utc) <= as_of]
        if not articles:
            return "No articles were found."
        return "Here are the relevant news articles:\n\n" + searcher._format_articles(articles)

    async def _web_research(self, question: MetaculusQuestion) -> str:
        if self._web is None:
            return ""
        prompt = clean_indents(
            f"""
            You are a research assistant to a professional forecaster. Search the web and report, briefly and with dates:
            1. the current status of the thing asked about, from the most authoritative and most recent sources;
            2. the latest figure or event the resolution source itself shows, if there is one;
            3. base rates or history that say how often this happens;
            4. anything scheduled before the deadline that could decide it;
            5. what experts, bookmakers or prediction markets currently say, with their numbers.
            Do not forecast. Say plainly when you could not find something. Today is {self._today()}.

            Question: {question.question_text}

            Resolution criteria:
            {question.resolution_criteria}

            {question.fine_print}
            """
        )
        try:
            return await self._web.invoke(prompt)
        except Exception as e:
            self.gaps["web"] += 1
            logger.warning(f"Web research failed for {question.page_url} ({type(e).__name__}: {str(e)[:120]})")
            return ""

    async def _market_research(self, question: MetaculusQuestion) -> str:
        try:
            answer = await self._scout.invoke(markets.QUERY_PROMPT.format(title=question.question_text))
            queries = markets.parse_queries(answer, question.question_text)
            found = await asyncio.to_thread(markets.search, queries)
            if found and isinstance(question, BinaryQuestion):
                verdict = await self._scout.invoke(markets.MATCH_PROMPT.format(
                    title=question.question_text, criteria=question.resolution_criteria, fine_print=question.fine_print,
                    resolves=question.scheduled_resolution_time or question.close_time, markets="\n".join(m.line(i + 1) for i, m in enumerate(found)),
                ))
                anchor = markets.anchor_probability(markets.parse_match(verdict, found))
                if anchor is not None:
                    self._anchor[self._key(question)] = anchor
            return markets.format_for_prompt(found)
        except Exception as e:
            self.gaps["markets"] += 1
            logger.warning(f"Market search failed for {question.page_url} ({type(e).__name__}: {str(e)[:120]})")
            return markets.format_for_prompt([])

    # ------------------------------------------------------------------ forecasts

    def _header(self, question: MetaculusQuestion, research: str) -> str:
        return clean_indents(
            f"""
            You are a professional forecaster with a long, well-calibrated track record.

            The question:
            {question.question_text}

            Background:
            {question.background_info}

            The outcome is decided by these resolution criteria, which are not yet satisfied:
            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant's report:
            {research}

            Today is {self._today()}.
            {FORECASTER_RULES}
            {self._get_conditional_disclaimer_if_necessary(question)}
            """
        )

    async def _run_forecast_on_binary(self, question: BinaryQuestion, research: str) -> ReasonedPrediction[float]:
        llm = self._next_forecaster(question)
        prompt = self._header(question, research) + clean_indents(
            """
            Before answering, write briefly:
            (a) The time left until the outcome is known, and what the criteria need exactly.
            (b) The base rate, with its number.
            (c) The status quo outcome if nothing changed.
            (d) The strongest case for No, and the strongest case for Yes.
            (e) What a matching prediction market says, if there is one.

            The last thing you write is your final answer as: "Probability: ZZ%", 0-100
            """
        )
        reasoning = await llm.invoke(prompt)
        value = probability_from_text(reasoning)
        if value is None:
            parsed: BinaryPrediction = await structure_output(
                reasoning, BinaryPrediction, model=self._parser, num_validation_samples=self._structure_output_validation_samples,
                additional_instructions="The text given to you is trying to give a probability forecast for a binary question.\n" + self._create_resolved_question_parsing_message(),
            )
            value = parsed.prediction_in_decimal
        return ReasonedPrediction(prediction_value=clamp_binary(value), reasoning=reasoning)

    async def _run_forecast_on_multiple_choice(self, question: MultipleChoiceQuestion, research: str) -> ReasonedPrediction[PredictedOptionList]:
        llm = self._next_forecaster(question)
        prompt = self._header(question, research) + clean_indents(
            f"""
            The options are: {question.options}

            Before answering, write briefly:
            (a) The time left until the outcome is known.
            (b) The status quo outcome if nothing changed, and the base rate for each plausible option.
            (c) A scenario that results in an unexpected outcome.

            Leave some probability on every option: surprises happen.

            The last thing you write is your final probabilities for the N options in this order {question.options} as:
            Option_A: Probability_A
            Option_B: Probability_B
            ...
            Option_N: Probability_N
            """
        )
        reasoning = await llm.invoke(prompt)
        parsing_instructions = clean_indents(
            f"""
            Make sure that all option names are one of the following:
            {question.options}

            The text you are parsing may prepend these options with some variation of "Option" which you should remove if not part of the option names I just gave you.
            Additionally, you may sometimes need to parse a 0% probability. Please do not skip options with 0% but rather make it an entry in your final list with 0% probability.
            {self._create_resolved_question_parsing_message()}
            """
        )
        options: PredictedOptionList = await structure_output(
            text_to_structure=reasoning, output_type=PredictedOptionList, model=self._parser,
            num_validation_samples=self._structure_output_validation_samples, additional_instructions=parsing_instructions,
        )
        return ReasonedPrediction(prediction_value=floor_options(options), reasoning=reasoning)

    _PERCENTILE_LINES = "\n".join(f"Percentile {p}: XX" for p in (5, 10, 20, 40, 50, 60, 80, 90, 95))

    async def _run_forecast_on_numeric(self, question: NumericQuestion, research: str) -> ReasonedPrediction[NumericDistribution]:
        llm = self._next_forecaster(question)
        upper, lower = self._create_upper_and_lower_bound_messages(question)
        prompt = self._header(question, research) + clean_indents(
            f"""
            Units for answer: {question.unit_of_measure if question.unit_of_measure else "Not stated (please infer this)"}
            {lower}
            {upper}

            Formatting:
            - Give your answer in the units requested (notice whether a number is written as 1,000,000 or 1 million).
            - Never use scientific notation.
            - Percentiles must increase: the value at percentile 5 is the lowest, at 95 the highest.

            Before answering, write briefly:
            (a) The time left until the outcome is known, and the latest value the resolution source shows.
            (b) The outcome if nothing changed, and if the current trend continued.
            (c) How much this quantity has moved over windows this long in the past (its spread).
            (d) What experts and markets expect.
            (e) An unexpected scenario for a low outcome, and one for a high outcome.

            Set wide intervals: forecasters are far more often too narrow than too wide, and the true value falling
            outside your 5-95 range should happen one time in ten, no less.

            The last thing you write is your final answer as:
            "
            {self._PERCENTILE_LINES}
            "
            """
        )
        return await self._numeric_prompt_with(llm, question, prompt)

    async def _numeric_prompt_with(self, llm, question: NumericQuestion, prompt: str) -> ReasonedPrediction[NumericDistribution]:
        reasoning = await llm.invoke(prompt)
        parsing_instructions = clean_indents(
            f"""
            The text given to you is trying to give a forecast distribution for a numeric question.
            - This text is trying to answer the numeric question: "{question.question_text}".
            {self._create_single_distribution_parsing_message(question)}
            {self._create_resolved_question_parsing_message()}
            - When parsing the text, please make sure to give the values (the ones assigned to percentiles) in terms of the correct units.
            - The units for the forecast are: {question.unit_of_measure}
            - As an example, someone else guessed that the answer will be between {question.lower_bound} {question.unit_of_measure} and {question.upper_bound} {question.unit_of_measure}, so the numbers parsed from an answer like this would be verbatim "{question.lower_bound}" and "{question.upper_bound}".
            - If the answer doesn't give the answer in the correct units, you should parse it in the right units. For instance if the answer gives numbers as $500,000,000 and units are "B $" then you should parse the answer as 0.5 (since $500,000,000 is $0.5 billion).
            - If percentiles are not explicitly given (e.g. only a single value is given) please don't return a parsed output, but rather indicate that the answer is not explicitly given in the text.
            - Turn any values that are in scientific notation into regular numbers.
            """
        )
        percentiles: list[Percentile] = await structure_output(
            reasoning, list[Percentile], model=self._parser, additional_instructions=parsing_instructions,
            num_validation_samples=self._structure_output_validation_samples,
        )
        return ReasonedPrediction(prediction_value=NumericDistribution.from_question(percentiles, question), reasoning=reasoning)

    async def _run_forecast_on_date(self, question: DateQuestion, research: str) -> ReasonedPrediction[NumericDistribution]:
        llm = self._next_forecaster(question)
        upper, lower = self._create_upper_and_lower_bound_messages(question)
        date_lines = "\n".join(f"Percentile {p}: YYYY-MM-DD" for p in (5, 10, 20, 40, 50, 60, 80, 90, 95))
        prompt = self._header(question, research) + clean_indents(
            f"""
            {lower}
            {upper}

            Formatting:
            - The answer is a date, written YYYY-MM-DD (append the hour in UTC as YYYY-MM-DDTHH:MM:SSZ only if hours matter).
            - Percentiles must be in chronological order, the earliest date at percentile 5.

            Before answering, write briefly:
            (a) The time left until the outcome is known.
            (b) The outcome if nothing changed, and if the current trend continued.
            (c) What experts and markets expect.
            (d) An unexpected scenario for an early outcome, and one for a late outcome.

            Set wide intervals: forecasters are far more often too narrow than too wide.

            The last thing you write is your final answer as:
            "
            {date_lines}
            "
            """
        )
        reasoning = await llm.invoke(prompt)
        parsing_instructions = clean_indents(
            f"""
            The text given to you is trying to give a forecast distribution for a date question.
            - This text is trying to answer the question: "{question.question_text}".
            {self._create_single_distribution_parsing_message(question)}
            {self._create_resolved_question_parsing_message()}
            - As an example, someone else guessed that the answer will be between {question.lower_bound} and {question.upper_bound}, so the numbers parsed from an answer like this would be verbatim "{question.lower_bound}" and "{question.upper_bound}".
            - The output is given as dates/times please format it into a valid datetime parsable string. Assume midnight UTC if no hour is given.
            - If percentiles are not explicitly given (e.g. only a single value is given) please don't return a parsed output, but rather indicate that the answer is not explicitly given in the text.
            """
        )
        dates: list[DatePercentile] = await structure_output(
            reasoning, list[DatePercentile], model=self._parser, additional_instructions=parsing_instructions,
            num_validation_samples=self._structure_output_validation_samples,
        )
        percentiles = [Percentile(percentile=d.percentile, value=d.value.timestamp()) for d in dates]
        return ReasonedPrediction(prediction_value=NumericDistribution.from_question(percentiles, question), reasoning=reasoning)

    # ------------------------------------------------------------------ the final number

    async def _aggregate_predictions(self, predictions, question):
        if isinstance(question, BinaryQuestion) and predictions and all(isinstance(p, (int, float)) for p in predictions):
            median = float(statistics.median(predictions))
            anchor = self._anchor.get(self._key(question))
            if anchor is not None:
                self.anchored += 1
            return clamp_binary(markets.blend(median, anchor))
        return await super()._aggregate_predictions(predictions, question)
