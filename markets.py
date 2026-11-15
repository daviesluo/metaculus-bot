"""
Prediction markets that may ask the same thing as a question: Polymarket (real money) and Manifold (play money).

Keyless, read-only, public data. A market is evidence for the forecasters; a Polymarket market that a careful check
says is the SAME question (same event, same threshold, same deadline) with real volume also anchors the final forecast
(bot_config.MARKET_WEIGHT). Any failure here returns nothing: the bot forecasts without markets.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

import bot_config as cfg

logger = logging.getLogger(__name__)
_UA = {"User-Agent": "forecasting-bot/1.0", "Accept": "application/json"}


@dataclass
class Market:
    venue: str
    title: str
    probability: float  # of YES, 0-1
    volume_usd: float | None  # Polymarket: dollars traded; Manifold: None (play money)
    traders: int | None
    closes: str | None
    url: str

    def line(self, n: int) -> str:
        size = f"${self.volume_usd:,.0f} traded" if self.volume_usd is not None else f"play money, {self.traders or 0} traders"
        return f"[M{n}] {self.venue}: \"{self.title}\" — YES at {self.probability * 100:.1f}% ({size}; closes {self.closes or 'unknown'}) {self.url}"


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_polymarket(payload: dict, now: datetime | None = None) -> list[Market]:
    """Open YES/No markets out of Gamma's public-search answer."""
    now = now or datetime.now(timezone.utc)
    out: list[Market] = []
    for event in payload.get("events") or []:
        for m in event.get("markets") or []:
            if m.get("closed") or not m.get("active"):
                continue
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            except (ValueError, TypeError):
                continue
            if [str(o).lower() for o in outcomes] != ["yes", "no"] or len(prices) != 2:
                continue
            end = m.get("endDate") or event.get("endDate")
            try:
                if end and datetime.fromisoformat(end.replace("Z", "+00:00")) < now:
                    continue
            except ValueError:
                pass
            bid, ask = _num(m.get("bestBid")), _num(m.get("bestAsk"))
            mid = (bid + ask) / 2 if bid is not None and ask is not None and 0 < ask - bid <= 0.1 else prices[0]
            if not 0 <= mid <= 1:
                continue
            out.append(Market("Polymarket", str(m.get("question") or event.get("title") or "").strip(), mid,
                              _num(m.get("volume")) or 0.0, None, (end or "")[:10] or None,
                              f"https://polymarket.com/event/{event.get('slug')}"))
    return out


def parse_manifold(payload: list) -> list[Market]:
    out: list[Market] = []
    for m in payload or []:
        if m.get("outcomeType") != "BINARY" or m.get("isResolved") or _num(m.get("probability")) is None:
            continue
        closes = None
        if m.get("closeTime"):
            closes = datetime.fromtimestamp(m["closeTime"] / 1000, timezone.utc).date().isoformat()
        out.append(Market("Manifold", str(m.get("question") or "").strip(), float(m["probability"]), None,
                          int(m.get("uniqueBettorCount") or 0), closes, str(m.get("url") or "")))
    return out


def search(queries: list[str], per_query: int = 4) -> list[Market]:
    """Markets for a few short queries, the most traded first, without repeats. Never raises."""
    found: dict[str, Market] = {}
    for q in queries[:3]:
        q = q.strip()
        if not q:
            continue
        try:
            r = requests.get("https://gamma-api.polymarket.com/public-search",
                             params={"q": q, "limit_per_type": per_query, "events_status": "active"}, headers=_UA, timeout=15)
            if r.ok:
                for m in parse_polymarket(r.json()):
                    found.setdefault(m.venue + m.title, m)
        except Exception as e:
            logger.warning(f"Polymarket search failed ({type(e).__name__})")
        try:
            r = requests.get("https://api.manifold.markets/v0/search-markets",
                             params={"term": q, "filter": "open", "sort": "liquidity", "limit": per_query}, headers=_UA, timeout=15)
            if r.ok:
                for m in parse_manifold(r.json()):
                    if (m.traders or 0) >= 10:
                        found.setdefault(m.venue + m.title, m)
        except Exception as e:
            logger.warning(f"Manifold search failed ({type(e).__name__})")
    markets = sorted(found.values(), key=lambda m: (m.volume_usd is None, -(m.volume_usd or 0), -(m.traders or 0)))
    return markets[:8]


def format_for_prompt(markets: list[Market]) -> str:
    if not markets:
        return "No related prediction market was found."
    lines = [m.line(i + 1) for i, m in enumerate(markets)]
    return (
        "Prediction markets found by keyword search. They may or may not ask the same thing: check the event, the "
        "threshold and the deadline against the resolution criteria before leaning on one. A real-money market that "
        "asks exactly this question is strong evidence; a merely related one is weak evidence.\n" + "\n".join(lines)
    )


QUERY_PROMPT = """Write search queries to find prediction markets about the question below.
Give 1 to 3 queries, each 2 to 5 plain keywords (names, the event, the year), one per line, nothing else.

Question: {title}"""


def parse_queries(text: str, fallback: str) -> list[str]:
    lines = [re.sub(r"^[\s\-\*\d\.\)]+", "", ln).strip().strip('"') for ln in (text or "").splitlines()]
    queries = [ln for ln in lines if 3 <= len(ln) <= 80][:3]
    return queries or [fallback[:80]]


MATCH_PROMPT = """You check whether a prediction market asks EXACTLY the same question as a forecasting question.

Forecasting question: {title}
Resolution criteria: {criteria}
Fine print: {fine_print}
The forecasting question closes for resolution around: {resolves}

Candidate markets:
{markets}

A market is an exact match only if ALL of these hold:
- it is about the same event and the same entity;
- any number, threshold or option is the same;
- its deadline is the same as the forecasting question's (a market that runs longer or shorter is NOT a match);
- YES on the market means YES on the forecasting question (or exactly NO: then say inverted).
When in doubt, it is not a match. Most candidates are not.

Answer with one line of JSON and nothing else:
{{"match": <the market's number, or null>, "inverted": <true or false>, "why": "<one short sentence>"}}"""


def parse_match(text: str, markets: list[Market]) -> tuple[Market, bool] | None:
    """The market the checker named, and whether its YES is the question's NO. None when it named none."""
    found = re.search(r"\{.*\}", text or "", re.S)
    if not found:
        return None
    try:
        verdict = json.loads(found.group(0))
    except ValueError:
        return None
    n = verdict.get("match")
    if isinstance(n, str) and n.strip().lstrip("Mm").isdigit():
        n = int(n.strip().lstrip("Mm"))
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(markets):
        return None
    return markets[n - 1], bool(verdict.get("inverted"))


def anchor_probability(match: tuple[Market, bool] | None) -> float | None:
    """The probability to anchor on: only a Polymarket match with enough traded, and never a price at the rails."""
    if match is None:
        return None
    market, inverted = match
    if market.venue != "Polymarket" or (market.volume_usd or 0) < cfg.MARKET_MIN_VOLUME_USD:
        return None
    p = 1 - market.probability if inverted else market.probability
    return p if 0.005 <= p <= 0.995 else None


def blend(model_probability: float, anchor: float | None) -> float:
    if anchor is None:
        return model_probability
    return cfg.MARKET_WEIGHT * anchor + (1 - cfg.MARKET_WEIGHT) * model_probability
