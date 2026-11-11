"""Claude Haiku classifier: coins, event type, direction, importance 1-5, one-line Korean summary.

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

EVENT_TYPES = ["hack", "withdrawal_halt", "listing", "delisting", "regulation", "macro", "other"]
DIRECTIONS = ["bullish", "bearish", "neutral"]

RISK_WORDS = {  # from coin-auto-trader coinai/news_engine.py
    "CRITICAL": ("hack", "hacked", "exploit", "breach", "delist", "delisting", "bankruptcy",
                 "insolvency", "seized", "trading halt", "withdrawal suspended", "rug pull",
                 "유의종목", "거래지원 종료", "거래지원종료", "해킹", "파산", "입출금 중단", "상장폐지"),
    "WATCH": ("sec", "regulation", "investigation", "lawsuit", "outage", "halt", "unlock",
              "token unlock", "inflation", "cpi", "ppi", "pce", "fomc", "rate hike",
              "treasury yield", "dollar index", "jobs report", "payroll", "liquidation",
              "금리", "규제", "조사", "소송", "중단", "언락", "물가", "고용", "청산"),
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


SYSTEM_PROMPT = """You classify crypto-market headlines and exchange notices for a trading research log.
For each item return:
- coins: uppercase tickers the item is directly about (e.g. ["BTC"], ["SOL","JUP"]). Empty list if none or market-wide.
- event_type: one of hack (hack/exploit/theft), withdrawal_halt (deposit/withdrawal suspension, network maintenance, exchange outage), listing (new spot/futures listing or trading support), delisting (delisting, trading-support end, caution/warning designation), regulation (laws, regulators, enforcement, lawsuits, ETF approvals), macro (rates, inflation, jobs, dollar, equities, geopolitics affecting risk assets), other.
- direction: likely short-term price effect on the named coins (or the overall crypto market if none): bullish, bearish or neutral.
- importance: integer 1-5. 5 = market-moving now (major exchange hack, BTC ETF decision, FOMC surprise, top-10 coin delisting); 4 = strong effect on a named coin (major-exchange listing/delisting, large hack); 3 = notable; 2 = minor; 1 = noise (opinion, price recap, promotion, airdrop campaign, routine API/futures product updates).
- summary_ko: one short Korean sentence (<= 60 characters) stating the fact, no speculation.
Judge only from the title and source given. Return every input id exactly once."""

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
                },
                "required": ["id", "coins", "event_type", "direction", "importance", "summary_ko"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}

BATCH_SIZE = 15
MAX_TOKENS = 2048
# rough input estimate for the budget pre-check: system prompt + per-item text
_EST_FIXED_TOKENS = 700
_EST_TOKENS_PER_ITEM = 60


class BudgetExceeded(Exception):
    pass


class Classifier:
    def __init__(self, api_key: str, model: str, budget: DailyBudget, base_url: str | None = None) -> None:
        # base_url is passed explicitly so a stray ANTHROPIC_BASE_URL in the shell is never used.
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url or "https://api.anthropic.com",
                                          max_retries=2, timeout=60.0)
        self.model = model
        self.budget = budget

    def _request(self, batch: list[RawItem]) -> tuple[dict[str, dict[str, Any]], float]:
        lines = [json.dumps({"id": it.id, "source": it.source, "kind": it.kind,
                             "category": it.category, "title": it.title}, ensure_ascii=False)
                 for it in batch]
        est_in = _EST_FIXED_TOKENS + _EST_TOKENS_PER_ITEM * len(batch)
        if not self.budget.allows(est_in, MAX_TOKENS):
            raise BudgetExceeded(f"daily cap ${self.budget.cap:.2f} reached (spent ${self.budget.spent():.4f})")
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Items (one JSON object per line):\n" + "\n".join(lines)}],
        )
        cost = self.budget.record(resp.usage)
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"unexpected stop_reason={resp.stop_reason}")
        text = next(b.text for b in resp.content if b.type == "text")
        out = {}
        for ev in json.loads(text)["events"]:
            ev["importance"] = max(1, min(5, int(ev["importance"])))
            ev["coins"] = sorted({c.strip().upper() for c in ev["coins"] if c.strip()})
            out[ev.pop("id")] = ev
        return out, cost

    def classify(self, items: list[RawItem]) -> tuple[dict[str, dict[str, Any]], float, str | None]:
        """Returns (results by item id, cost this call, stop reason or None)."""
        results: dict[str, dict[str, Any]] = {}
        total = 0.0
        for i in range(0, len(items), BATCH_SIZE):
            try:
                got, cost = self._request(items[i:i + BATCH_SIZE])
            except BudgetExceeded as exc:
                return results, total, str(exc)
            except (anthropic.APIError, RuntimeError, ValueError, KeyError) as exc:
                return results, total, f"{type(exc).__name__}: {str(exc)[:200]}"
            results.update(got)
            total += cost
        return results, total, None
