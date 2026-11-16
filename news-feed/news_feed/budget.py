"""Daily USD cost ledger shared by every model (Haiku classification + Sonnet briefings).

Before each call the caller asks `allows(...)` with the worst case (max_tokens fully used);
the call is skipped if it could push the day past the cap, so actual spend never exceeds it.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

# USD per 1M tokens (Anthropic first-party API list prices, prompts <= 100K tokens).
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.10, "cache_write": 2.50},
}


def _get(usage, k: str) -> int:
    v = usage.get(k) if isinstance(usage, dict) else getattr(usage, k, 0)
    return int(v or 0)


def usage_cost(model: str, usage) -> float:
    p = PRICES[model]
    return (
        _get(usage, "input_tokens") * p["input"]
        + _get(usage, "output_tokens") * p["output"]
        + _get(usage, "cache_read_input_tokens") * p["cache_read"]
        + _get(usage, "cache_creation_input_tokens") * p["cache_write"]
    ) / 1_000_000


def _empty() -> dict:
    return {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "by_model": {}}


class DailyBudget:
    def __init__(self, path: Path, cap_usd: float, model: str) -> None:
        if model not in PRICES:
            raise ValueError(f"no price table for model {model!r}; add it to PRICES")
        self.path = path
        self.cap = cap_usd
        self.model = model  # default model for calls that don't name one
        self._lock = threading.Lock()

    @staticmethod
    def today() -> str:
        return datetime.now(KST).date().isoformat()  # day boundary = 00:00 KST

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def day(self, date: str | None = None) -> dict:
        return {**_empty(), **self._load().get(date or self.today(), {})}

    def all_days(self) -> dict:
        return self._load()

    def spent(self) -> float:
        return float(self.day()["usd"])

    def worst_case(self, est_input_tokens: int, max_output_tokens: int, model: str | None = None) -> float:
        p = PRICES[model or self.model]
        return (est_input_tokens * p["input"] + max_output_tokens * p["output"]) / 1_000_000

    def allows(self, est_input_tokens: int, max_output_tokens: int, model: str | None = None,
               reserve: float = 0.0) -> bool:
        """True only if even a worst-case call stays within today's cap (minus `reserve`)."""
        return self.spent() + self.worst_case(est_input_tokens, max_output_tokens, model) <= self.cap - reserve

    def record(self, usage, model: str | None = None, purpose: str = "classify") -> float:
        model = model or self.model
        cost = usage_cost(model, usage)
        with self._lock:
            data = self._load()
            d = {**_empty(), **data.get(self.today(), {})}
            d["usd"] = round(d["usd"] + cost, 8)
            d["calls"] += 1
            d["input_tokens"] += _get(usage, "input_tokens")
            d["output_tokens"] += _get(usage, "output_tokens")
            m = d["by_model"].setdefault(f"{model}:{purpose}", {"usd": 0.0, "calls": 0})
            m["usd"] = round(m["usd"] + cost, 8)
            m["calls"] += 1
            data[self.today()] = d
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        return cost
