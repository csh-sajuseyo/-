"""Daily USD cost ledger. Classification stops once the day's spend reaches the cap."""
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
}


def usage_cost(model: str, usage) -> float:
    p = PRICES[model]
    get = (lambda k: getattr(usage, k, 0) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k) or 0)
    return (
        get("input_tokens") * p["input"]
        + get("output_tokens") * p["output"]
        + get("cache_read_input_tokens") * p["cache_read"]
        + get("cache_creation_input_tokens") * p["cache_write"]
    ) / 1_000_000


class DailyBudget:
    def __init__(self, path: Path, cap_usd: float, model: str) -> None:
        if model not in PRICES:
            raise ValueError(f"no price table for model {model!r}; add it to PRICES")
        self.path = path
        self.cap = cap_usd
        self.model = model
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
        return self._load().get(date or self.today(),
                                {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0})

    def spent(self) -> float:
        return float(self.day()["usd"])

    def worst_case(self, est_input_tokens: int, max_output_tokens: int) -> float:
        p = PRICES[self.model]
        return (est_input_tokens * p["input"] + max_output_tokens * p["output"]) / 1_000_000

    def allows(self, est_input_tokens: int, max_output_tokens: int) -> bool:
        """True only if even a worst-case call stays within today's cap."""
        return self.spent() + self.worst_case(est_input_tokens, max_output_tokens) <= self.cap

    def record(self, usage) -> float:
        cost = usage_cost(self.model, usage)
        with self._lock:
            data = self._load()
            d = data.setdefault(self.today(), {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0})
            d["usd"] = round(d["usd"] + cost, 8)
            d["calls"] += 1
            d["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
            d["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        return cost
