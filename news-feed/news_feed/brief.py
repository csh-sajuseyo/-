"""World-economy briefing, twice a day (default 08:20 and 21:50 KST), with claude-sonnet-5-5.

Input  : last 12h of collected news (+ AI labels), latest market indicators, the next 7 days of the calendar.
Output : situation (3 lines) / risks / next 7 days / effect on BTC & XLM / confidence — structured JSON,
         every statement cites refs (N* news, M:* market, C* calendar) that map to source links.
Guard  : numbers in the output that do not appear anywhere in the input are listed in `unverified_numbers`.
Cost   : same daily ledger as classification (data/cost_ledger.json); skipped if the worst case would pass the cap.
"""
from __future__ import annotations

import json
import logging
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic

from .budget import KST, PRICES, DailyBudget
from .macro import upcoming

log = logging.getLogger("news_feed.brief")

BRIEF_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 8000
WINDOW_HOURS = 12
MAX_NEWS = 90
COINS = ("BTC", "XLM")
# a briefing costs ~$0.03-0.06; keep room for the remaining slots of the day
RESERVE_PER_BRIEF = 0.12

SYSTEM = """You write a short world-economy briefing in Korean for a retail crypto trader (BTC, XLM holder).
You receive JSON with: news (last 12 hours, ref N*), markets (latest indicator values, ref M:*),
calendar (scheduled releases and meetings in the next 7 days, ref C*).

Rules:
- Use only facts and numbers that appear in the input. Never add a number, date, price or percentage that is not in the input.
  If an important value is missing, say it is not in today's material instead of guessing.
- Every item carries refs: the ids of the inputs it is based on. No statement without refs.
- Plain, short Korean sentences a non-expert understands. No jargon without a short explanation. No hype.
- situation: exactly 3 lines, each <= 70 Korean characters: (1) rates/inflation/dollar, (2) stocks/risk appetite/geopolitics,
  (3) crypto market. If the news for a line is thin, say so briefly.
- risks: 2-4 concrete risk factors visible in the material.
- next_7d: the scheduled items from the calendar input that matter most (max 6), in date order, with what to watch.
  Use the calendar's date_kst and time_kst exactly.
- coin_impact: for BTC and XLM, direction (bullish/bearish/neutral/mixed) and one sentence on the channel of effect.
  XLM: say plainly when there is no XLM-specific news and the view follows BTC/risk appetite.
- confidence: low/medium/high and one sentence why (amount and agreement of evidence)."""

_REF = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "refs": _REF},
            "required": ["text", "refs"], "additionalProperties": False}},
        "risks": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "refs": _REF},
            "required": ["text", "refs"], "additionalProperties": False}},
        "next_7d": {"type": "array", "items": {
            "type": "object",
            "properties": {"date_kst": {"type": "string"}, "time_kst": {"type": "string"},
                           "text": {"type": "string"}, "refs": _REF},
            "required": ["date_kst", "time_kst", "text", "refs"], "additionalProperties": False}},
        "coin_impact": {"type": "array", "items": {
            "type": "object",
            "properties": {"coin": {"type": "string", "enum": list(COINS)},
                           "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral", "mixed"]},
                           "text": {"type": "string"}, "refs": _REF},
            "required": ["coin", "direction", "text", "refs"], "additionalProperties": False}},
        "confidence": {"type": "object",
                       "properties": {"level": {"type": "string", "enum": ["low", "medium", "high"]},
                                      "reason": {"type": "string"}},
                       "required": ["level", "reason"], "additionalProperties": False},
    },
    "required": ["situation", "risks", "next_7d", "coin_impact", "confidence"],
    "additionalProperties": False,
}


def read_tail(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    rows: deque = deque(maxlen=n)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(line)
    out = []
    for line in rows:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _when(row: dict) -> datetime | None:
    v = row.get("published_at") or row.get("collected_at")
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def build_inputs(events: list[dict], markets: dict[str, Any], calendar: list[dict], now: datetime
                 ) -> tuple[dict, dict[str, dict]]:
    """Returns (model input JSON, ref -> {title, url, source})."""
    since = now - timedelta(hours=WINDOW_HOURS)
    recent = [r for r in events if (_when(r) or now) >= since]
    labelled = [r for r in recent if r.get("classified") and (r.get("importance") or 0) >= 2]
    labelled.sort(key=lambda r: (-(r.get("importance") or 0), -(_when(r) or now).timestamp()))
    chosen = labelled[:MAX_NEWS]
    if len(chosen) < 15:  # thin day or classification stopped: add newest unlabelled titles
        rest = [r for r in recent if r not in chosen]
        rest.sort(key=lambda r: -(_when(r) or now).timestamp())
        chosen += rest[: 30 - len(chosen)]
    refs: dict[str, dict] = {}
    news = []
    for i, r in enumerate(chosen, 1):
        ref = f"N{i}"
        refs[ref] = {"title": r.get("title"), "url": r.get("url"), "source": r.get("source")}
        w = _when(r)
        news.append({k: v for k, v in {
            "ref": ref, "source": r.get("source"), "group": r.get("group"),
            "time_kst": w.astimezone(KST).strftime("%m-%d %H:%M") if w else None,
            "title": r.get("title"), "summary": (r.get("summary") or "")[:200] or None,
            "ai_summary": r.get("summary_ko"), "event_type": r.get("event_type"),
            "importance": r.get("importance"), "coins": r.get("coins") or None,
        }.items() if v not in (None, "", [])})
    mk = []
    for key, m in (markets or {}).items():
        ref = f"M:{key}"
        refs[ref] = {"title": f"{m.get('label')} ({m.get('source')})", "url": m.get("url"), "source": m.get("source")}
        mk.append({"ref": ref, "name": m.get("label"), "value": m.get("value"), "unit": m.get("unit"),
                   "change_pct": m.get("change_pct"), "change_bp": m.get("change_bp"),
                   "change_basis": m.get("change_basis"), "as_of": m.get("as_of"), "basis": m.get("basis")})
    cal = []
    for i, c in enumerate(upcoming(calendar or [], now, 7), 1):
        ref = f"C{i}"
        refs[ref] = {"title": c["name_ko"], "url": c["url"], "source": c["source"]}
        cal.append({"ref": ref, "date_kst": c["date_kst"], "time_kst": c["time_kst"], "name": c["name_ko"],
                    "importance": c["importance"], "source": c["source"]})
    return {"now_kst": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"), "window_hours": WINDOW_HOURS,
            "news": news, "markets": mk, "calendar": cal}, refs


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def unverified_numbers(brief: dict, input_text: str) -> list[str]:
    """Numbers in the brief text that never appear in the input (allowing comma / trailing-zero differences)."""
    hay = input_text.replace(",", "")
    allowed = {"1", "2", "3", "7", "12", "24"}  # window sizes / ordinals stated in the prompt
    bad = []
    texts = [x["text"] for k in ("situation", "risks", "next_7d", "coin_impact") for x in brief.get(k, [])]
    texts += [x.get("date_kst", "") + " " + x.get("time_kst", "") for x in brief.get("next_7d", [])]
    texts.append(brief.get("confidence", {}).get("reason", ""))
    for t in texts:
        for m in _NUM.findall(t):
            n = m.replace(",", "").rstrip(".")
            variants = {n, n.rstrip("0").rstrip(".") if "." in n else n, n.lstrip("0") or "0"}
            if n in allowed or any(v and v in hay for v in variants):
                continue
            bad.append(m)
    return sorted(set(bad))


def slot_due(now: datetime, slots: list[str], done: set[str], grace_hours: float = 3.0) -> str | None:
    """Slot key ('2026-11-16 08:20') whose time has passed within `grace_hours` and has no briefing yet."""
    k = now.astimezone(KST)
    for day in (k.date() - timedelta(days=1), k.date()):
        for s in slots:
            hh, mm = (int(x) for x in s.split(":"))
            at = datetime(day.year, day.month, day.day, hh, mm, tzinfo=KST)
            key = at.strftime("%Y-%m-%d %H:%M")
            if at <= k <= at + timedelta(hours=grace_hours) and key not in done:
                return key
    return None


class Briefer:
    def __init__(self, api_key: str, budget: DailyBudget, data_dir: Path, base_url: str | None = None,
                 model: str = BRIEF_MODEL) -> None:
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url or "https://api.anthropic.com",
                                          max_retries=2, timeout=180.0)
        self.budget = budget
        self.model = model
        self.path = data_dir / "briefs.jsonl"

    def done_slots(self) -> set[str]:
        return {b.get("slot") for b in read_tail(self.path, 50) if b.get("slot")}

    def latest(self) -> dict | None:
        rows = read_tail(self.path, 1)
        return rows[-1] if rows else None

    def make(self, events: list[dict], markets: dict, calendar: list[dict], slot: str,
             now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        payload, refs = build_inputs(events, markets, calendar, now)
        text = json.dumps(payload, ensure_ascii=False)
        est_in = 1200 + len(text) // 2  # Korean-heavy JSON: ~2 chars per token is a safe over-estimate
        if not self.budget.allows(est_in, MAX_TOKENS, self.model):
            raise RuntimeError(f"daily cap ${self.budget.cap:.2f} would be exceeded (spent ${self.budget.spent():.4f})")
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},  # on a policy decline, retry on a fallback model in the same call
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": "Input JSON:\n" + text}],
        )
        served = resp.model if resp.model in PRICES else self.model
        cost = self.budget.record(resp.usage, served, "brief")
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"stop_reason={resp.stop_reason}")
        out = json.loads(next(b.text for b in resp.content if b.type == "text"))
        out["situation"] = out["situation"][:3]
        used = sorted({r for k in ("situation", "risks", "next_7d", "coin_impact") for x in out[k]
                       for r in x["refs"] if r in refs}, key=lambda r: (r[0], r))
        rec = {
            "id": f"brief-{slot.replace(' ', 'T')}",
            "slot": slot,
            "created_at": now.isoformat(timespec="seconds"),
            "model": served,
            "window_hours": WINDOW_HOURS,
            **out,
            "sources": [{"ref": r, **refs[r]} for r in used],
            "unverified_numbers": unverified_numbers(out, text),
            "inputs": {"news": len(payload["news"]), "markets": len(payload["markets"]),
                       "calendar": len(payload["calendar"])},
            "cost_usd": round(cost, 6),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
