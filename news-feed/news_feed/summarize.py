"""Body summaries (Haiku): 3-5 short Korean points + up to 3 key numbers per important article.

Only for items with importance >= 3 or about the user's coins. The article text is read by
ArticleReader, passed to the model and then dropped — only the summary is stored (data/summaries.jsonl).
Own daily cap (default $0.10, `.env` NEWS_FEED_SUMMARY_USD_CAP) inside the shared ledger and the global cap.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

from .budget import DailyBudget

MAX_TOKENS = 2500
BATCH = 4
POINT_MAX = 60
COPY_RUN = 15  # a point may not share more than this many consecutive characters with the source
_EST_FIXED = 900

SYSTEM = """You write short Korean summaries of news articles for a retail investor who will not open the article.
For each input item return:
- points: Korean bullet sentences, each at most 55 characters, plain and factual.
  basis "원문" (article text given): 3 to 5 points covering what happened, why, and what comes next if the text says so.
  basis "제목만" (only title / short description): 1 or 2 points, only what the title/description states.
- numbers: 0 to 3 key figures exactly as they appear in the input with a short Korean label,
  e.g. "BTC 64,800달러 (-3.5%)", "피해액 450만 달러". Only numbers present in the input. Empty list if none matter.
Rules: rewrite in your own Korean words — never copy a sentence or a long phrase, no quotations, no quotation marks.
Leave out reporter names, photo captions, ads and promotions. Facts only: no guesses, no advice, no adjectives
like 충격/폭등 unless the text states the number. Return every input id exactly once."""

SCHEMA = {
    "type": "object",
    "properties": {"summaries": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"},
                       "points": {"type": "array", "items": {"type": "string"}},
                       "numbers": {"type": "array", "items": {"type": "string"}}},
        "required": ["id", "points", "numbers"], "additionalProperties": False}}},
    "required": ["summaries"], "additionalProperties": False,
}


class SummaryCapReached(Exception):
    pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def copied_run(point: str, source: str, n: int = COPY_RUN) -> bool:
    """True if `point` shares more than n consecutive characters with `source` (whitespace-normalised)."""
    p, src = _norm(point), _norm(source)
    for i in range(0, max(0, len(p) - n)):
        if p[i:i + n + 1] in src:
            return True
    return False


def _number_in(d: str, hay: str) -> bool:
    """Digits found in the source, allowing unit changes: 4,500만 달러 <-> $45 million, 1.2조 <-> 1,200 billion."""
    sig = d.replace(".", "").strip("0") or "0"
    return d in hay or (len(sig) >= 2 and sig in hay.replace(".", ""))


def clean_summary(raw: dict, source_text: str, basis: str) -> dict:
    """Enforce the format: 3-5 (원문) / 1-2 (제목만) points, <= 60 chars, no copied runs, numbers from the source."""
    pts = []
    for p in raw.get("points") or []:
        p = _norm(re.sub(r"[\"“”‘’'「」『』]", "", p)).lstrip("-•· ")
        if not p or copied_run(p, source_text):
            continue
        if len(p) > POINT_MAX:
            p = p[: POINT_MAX - 1].rstrip() + "…"
        if p not in pts:
            pts.append(p)
    pts = pts[:5] if basis == "원문" else pts[:2]
    hay = _norm(source_text).replace(",", "")
    nums = []
    for x in raw.get("numbers") or []:
        x = _norm(x)[:40]
        digits = [d.replace(",", "") for d in re.findall(r"\d[\d,]*(?:\.\d+)?", x)]
        if x and digits and all(_number_in(d, hay) for d in digits):
            nums.append(x)
    return {"points": pts, "numbers": nums[:3]}


def format_problems(s: dict, source_text: str) -> list[str]:
    """Checks used by tests and the report; empty list = OK."""
    out = []
    lo, hi = (3, 5) if s.get("basis") == "원문" else (1, 2)
    if not lo <= len(s.get("points", [])) <= hi:
        out.append(f"points {len(s.get('points', []))} not in {lo}-{hi}")
    out += [f"too long: {p}" for p in s.get("points", []) if len(p) > POINT_MAX]
    out += [f"copied: {p}" for p in s.get("points", []) if copied_run(p, source_text)]
    if len(s.get("numbers", [])) > 3:
        out.append("more than 3 numbers")
    return out


class Summarizer:
    def __init__(self, api_key: str, model: str, budget: DailyBudget, cap: float = 0.10,
                 base_url: str | None = None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url or "https://api.anthropic.com",
                                          max_retries=2, timeout=90.0)
        self.model = model
        self.budget = budget
        self.cap = cap

    def spent(self) -> float:
        return self.budget.spent_for("summarize")

    def _call(self, batch: list[dict]) -> dict[str, dict]:
        text = "\n".join(json.dumps({"id": b["id"], "basis": b["basis"], "source": b["source"], "title": b["title"],
                                     "text": b["text"]}, ensure_ascii=False) for b in batch)
        est_in = _EST_FIXED + len(text) // 2  # Korean-heavy text: ~2 chars per token is a safe over-estimate
        worst = self.budget.worst_case(est_in, MAX_TOKENS, self.model)
        if self.spent() + worst > self.cap:
            raise SummaryCapReached(f"summary cap ${self.cap:.2f} (spent ${self.spent():.4f})")
        if not self.budget.allows(est_in, MAX_TOKENS, self.model):
            raise SummaryCapReached(f"daily cap ${self.budget.cap:.2f}")
        resp = self.client.messages.create(
            model=self.model, max_tokens=MAX_TOKENS, thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM, messages=[{"role": "user", "content": "Items (one JSON object per line):\n" + text}])
        self.budget.record(resp.usage, self.model, "summarize")
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"stop_reason={resp.stop_reason}")
        out = json.loads(next(b.text for b in resp.content if b.type == "text"))
        return {s["id"]: s for s in out["summaries"]}

    def summarize(self, items: list[dict], now: datetime | None = None) -> tuple[dict[str, dict], str | None]:
        """items: {id, source, title, text, basis}. Returns ({id: body_summary}, stop reason or None)."""
        now = now or datetime.now(timezone.utc)
        done: dict[str, dict] = {}
        errors: list[str] = []
        for i in range(0, len(items), BATCH):
            batch = items[i:i + BATCH]
            try:
                got = self._call(batch)
            except SummaryCapReached as exc:
                return done, f"요약 멈춤(오늘 상한): {exc}"
            except (anthropic.APIError, RuntimeError, ValueError, KeyError, StopIteration) as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
                continue
            for b in batch:
                raw = got.get(b["id"])
                if not raw:
                    continue
                s = clean_summary(raw, b["text"] + " " + b["title"], b["basis"])
                if not s["points"]:
                    continue
                done[b["id"]] = {**s, "basis": b["basis"], "read_at": now.isoformat(timespec="seconds")}
        return done, ("; ".join(errors)[:300] or None)


def source_text_for(row: dict) -> str:
    """What the title-only fallback is allowed to use: RSS description, else the title."""
    return row.get("summary") or row.get("title") or ""


def needs_summary(row: dict, my_coins: tuple[str, ...] = ("BTC", "XLM")) -> bool:
    if not row.get("classified") or row.get("body_summary"):
        return False
    return (row.get("importance") or 0) >= 3 or bool(set(row.get("coins") or []) & set(my_coins))


def as_item(row: dict, text: str) -> dict[str, Any]:
    return {"id": row["id"], "source": row.get("source"), "title": row.get("title") or "",
            "text": text or source_text_for(row), "basis": "원문" if text else "제목만"}
