"""Claude Haiku classifier: coins, event type, direction, importance 1-5, Korean summary / why / crypto impact.

Keyword rules (`rule_tag`) are reused from coin-auto-trader
`NewsEngine.RISK_WORDS` and `UpbitAnnouncementStream._classify`; they are kept as a
free, always-on hint and as the fallback when the daily budget stops AI calls.
"""
from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from .budget import DailyBudget
from .sources import RawItem

# crypto-specific types, then macro sub-types (rates..equities), then catch-alls
EVENT_TYPES = ["hack", "withdrawal_halt", "listing", "delisting", "regulation",
               "rates", "inflation", "jobs", "fx", "geopolitics", "equities", "macro", "other"]
MACRO_TYPES = {"rates", "inflation", "jobs", "fx", "geopolitics", "equities", "macro"}
DIRECTIONS = ["bullish", "bearish", "neutral"]

RISK_WORDS = {  # from coin-auto-trader coinai/news_engine.py
    "CRITICAL": ("hack", "hacked", "exploit", "breach", "delist", "delisting", "bankruptcy",
                 "insolvency", "seized", "trading halt", "withdrawal suspended", "rug pull",
                 "유의종목", "거래지원 종료", "거래지원종료", "해킹", "파산", "입출금 중단", "상장폐지"),
    "WATCH": ("sec", "regulation", "investigation", "lawsuit", "outage", "halt", "unlock",
              "token unlock", "inflation", "cpi", "ppi", "pce", "fomc", "rate hike",
              "treasury yield", "dollar index", "jobs report", "payroll", "liquidation",
              "tariff", "tariffs", "sanctions", "war", "rate cut", "rate decision", "recession",
              "금리", "규제", "조사", "소송", "중단", "언락", "물가", "고용", "청산", "관세", "제재", "환율"),
    "POSITIVE": ("approval", "approved", "partnership", "launch", "upgrade", "record inflow",
                 "etf inflow", "integration", "adoption", "will list", "new listing",
                 "승인", "파트너십", "업그레이드", "출시", "채택", "유입", "신규 거래지원"),
}


def rule_tag(title: str) -> str:
    low = title.lower()
    for tag in ("CRITICAL", "WATCH", "POSITIVE"):
        for w in RISK_WORDS[tag]:
            # ascii words need word boundaries ("sec" must not match "second")
            hit = re.search(rf"\b{re.escape(w)}\b", low) if w.isascii() else (w in low)
            if hit:
                return tag
    return "NONE"


SYSTEM_PROMPT = """You classify headlines for a crypto trader who also follows the world economy.
Items are crypto news, exchange notices (Upbit, Binance), central-bank press releases (Fed, ECB, BOJ, BOK),
US statistics releases (BLS, BEA) and general economic news. For each item return:
- coins: uppercase tickers the item is directly about (e.g. ["BTC"], ["SOL","JUP"]). Empty list if none or market-wide.
- event_type, one of:
  hack (hack/exploit/theft); withdrawal_halt (deposit/withdrawal suspension, network maintenance, exchange outage);
  listing (new spot/futures listing or trading support); delisting (delisting, trading-support end, caution designation);
  regulation (laws, regulators, enforcement, lawsuits, ETF approvals, crypto policy);
  rates (central-bank policy rate decisions, rate guidance, bond yields, balance sheet);
  inflation (CPI, PPI, PCE, inflation expectations); jobs (payrolls, unemployment, wages, jobless claims);
  fx (dollar, yen, won, other currencies, FX intervention); geopolitics (war, sanctions, tariffs, elections, trade disputes);
  equities (stock indexes, big-tech earnings, equity market moves); macro (other economy: GDP, growth, oil, fiscal, banking stress);
  other (anything else).
- direction: likely short-term price effect on the named coins (or the overall crypto market if none): bullish, bearish or neutral.
- importance: integer 1-5. 5 = market-moving now (major exchange hack, FOMC/CPI/jobs surprise, BTC ETF decision, war escalation,
  top-10 coin delisting); 4 = strong effect (major-exchange listing/delisting, large hack, central-bank decision in line with
  expectations, key data release); 3 = notable; 2 = minor (routine speeches, small data); 1 = noise (opinion, price recap,
  promotion, airdrop campaign, routine API/futures product updates, unrelated corporate news).
- summary_ko: one short Korean sentence (<= 60 characters) stating the fact, no speculation.
- why: one short Korean sentence (<= 60 characters): why this matters for markets. Empty string if importance <= 1.
- crypto_impact: one short Korean sentence (<= 60 characters): the likely channel of effect on crypto (BTC first),
  e.g. "달러 강세로 BTC 등 위험자산에 부담". Say "직접 영향 적음" if there is none.
Use only the title, summary and source given; do not invent numbers that are not in them.
Return every input id exactly once."""

SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "coins": {"type": "array", "items": {"type": "string"}},
                    "event_type": {"type": "string", "enum": EVENT_TYPES},
                    "direction": {"type": "string", "enum": DIRECTIONS},
                    "importance": {"type": "integer"},
                    "summary_ko": {"type": "string"},
                    "why": {"type": "string"},
                    "crypto_impact": {"type": "string"},
                },
                "required": ["id", "coins", "event_type", "direction", "importance", "summary_ko",
                             "why", "crypto_impact"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}

BATCH_SIZE = 8  # 3 Korean sentences per item: 15 items could hit max_tokens and lose the whole batch
MAX_TOKENS = 4096  # 8 items x ~250 output tokens + JSON overhead, with room to spare
# rough input estimate for the budget pre-check: system prompt + per-item text (title + short summary)
_EST_FIXED_TOKENS = 1100
_EST_TOKENS_PER_ITEM = 130


class BudgetExceeded(Exception):
    pass


class Classifier:
    def __init__(self, api_key: str, model: str, budget: DailyBudget, base_url: str | None = None,
                 reserve: float = 0.0) -> None:
        # base_url is passed explicitly so a stray ANTHROPIC_BASE_URL in the shell is never used.
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url or "https://api.anthropic.com",
                                          max_retries=2, timeout=60.0)
        self.model = model
        self.budget = budget
        self.reserve = reserve  # USD kept free for today's briefings

    def _request(self, batch: list[RawItem]) -> tuple[dict[str, dict[str, Any]], float]:
        lines = [json.dumps({"id": it.id, "source": it.source, "kind": it.kind, "category": it.category,
                             "title": it.title, "summary": it.summary[:240]}, ensure_ascii=False)
                 for it in batch]
        est_in = _EST_FIXED_TOKENS + _EST_TOKENS_PER_ITEM * len(batch)
        if not self.budget.allows(est_in, MAX_TOKENS, self.model, reserve=self.reserve):
            raise BudgetExceeded(f"daily cap ${self.budget.cap:.2f} reached (spent ${self.budget.spent():.4f}, "
                                 f"briefing reserve ${self.reserve:.2f})")
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Items (one JSON object per line):\n" + "\n".join(lines)}],
        )
        cost = self.budget.record(resp.usage, self.model, "classify")
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"unexpected stop_reason={resp.stop_reason}")
        text = next(b.text for b in resp.content if b.type == "text")
        out = {}
        for ev in json.loads(text)["events"]:
            ev["importance"] = max(1, min(5, int(ev["importance"])))
            ev["coins"] = sorted({c.strip().upper() for c in ev["coins"] if c.strip()})
            ev["why"] = (ev.get("why") or "").strip()
            ev["crypto_impact"] = (ev.get("crypto_impact") or "").strip()
            out[ev.pop("id")] = ev
        return out, cost

    def classify(self, items: list[RawItem]) -> tuple[dict[str, dict[str, Any]], float, str | None]:
        """Returns (results by item id, cost this call, stop reason or None).

        A failed batch (API error, truncated output) is skipped and the next batch still runs;
        only the budget stops everything. Unclassified items are retried later by the collector.
        """
        results: dict[str, dict[str, Any]] = {}
        total = 0.0
        errors: list[str] = []
        for i in range(0, len(items), BATCH_SIZE):
            try:
                got, cost = self._request(items[i:i + BATCH_SIZE])
            except BudgetExceeded as exc:
                errors.append(str(exc))
                break
            except (anthropic.APIError, RuntimeError, ValueError, KeyError, StopIteration) as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
                continue
            results.update(got)
            total += cost
        return results, total, ("; ".join(errors)[:400] or None)
