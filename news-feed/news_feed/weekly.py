"""Weekly digest: every Sunday 08:30 KST, Sonnet 5.5 through the Message Batches API (half price).

Input : the last 7 days of classified items with importance >= 3 — their body-summary lead lines (our own
        Korean wording) or the one-line AI summary; duplicates of the same story are skipped — plus the
        scheduled events of the coming week.
Output: 「이번 주 코인·세계경제 5줄」 + 「다음 주 볼 일정 3개」, each line with refs to source links.
Files : data/weekly/YYYY-Www.json and data/weekly/YYYY-Www.md (the ISO week of that Sunday); feed.json `weekly`.
A Sunday missed while the PC was off is made until Monday 08:30 KST. The batch is submitted on one poll and
picked up on a later poll (data/weekly/state.json).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import anthropic

from .brief import unverified_numbers
from .budget import KST, PRICES, DailyBudget
from .macro import atomic_write_json, upcoming
from .summarize import QUOTES, copied_run

MAX_TOKENS = 6000
MAX_ITEMS = 150
SUBMIT_AT = (8, 30)
GRACE_H = 24
BATCH_EXPIRE_H = 24

SYSTEM = """You write a weekly wrap-up in Korean for a retail crypto investor (BTC, XLM holder).
Input JSON: items (the past week's important news as short Korean summaries, ref W*) and calendar
(scheduled releases and meetings next week, ref C*).
Return:
- lines: exactly 5 lines, each at most 80 Korean characters, covering crypto and the world economy this week:
  the biggest moves and stories first. Each line has refs (the W* ids it is based on).
- next_week: up to 3 calendar items worth watching next week, in date order, using date_kst and time_kst exactly
  as given, each with one short reason. Empty list if the calendar is empty.
Rules: only facts and numbers from the input; write in your own words (do not copy titles); no quotation marks;
plain words a non-expert understands; no advice."""

_REF = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {"type": "array", "items": {"type": "object",
                                             "properties": {"text": {"type": "string"}, "refs": _REF},
                                             "required": ["text", "refs"], "additionalProperties": False}},
        "next_week": {"type": "array", "items": {"type": "object",
                                                 "properties": {"date_kst": {"type": "string"},
                                                                "time_kst": {"type": "string"},
                                                                "text": {"type": "string"}, "refs": _REF},
                                                 "required": ["date_kst", "time_kst", "text", "refs"],
                                                 "additionalProperties": False}},
    },
    "required": ["lines", "next_week"], "additionalProperties": False,
}


def week_key(sunday: date) -> str:
    y, w, _ = sunday.isocalendar()
    return f"{y}-W{w:02d}"


def due_sunday(now: datetime) -> date | None:
    """The Sunday whose digest should exist now (Sunday 08:30 KST .. Monday 08:30 KST), else None."""
    k = now.astimezone(KST)
    for back in (0, 1):
        d = k.date() - timedelta(days=back)
        if d.weekday() != 6:
            continue
        start = datetime(d.year, d.month, d.day, *SUBMIT_AT, tzinfo=KST)
        if start <= k <= start + timedelta(hours=GRACE_H):
            return d
    return None


def _when(r: dict) -> str:
    return r.get("published_at") or r.get("collected_at") or ""


def build_inputs(events: list[dict], calendar: list[dict], sunday: date) -> tuple[dict, dict[str, dict], str]:
    """Returns (model input, ref -> source, original text the output must not copy)."""
    end = datetime(sunday.year, sunday.month, sunday.day, *SUBMIT_AT, tzinfo=KST).astimezone(timezone.utc)
    start = end - timedelta(days=7)
    rows = [r for r in events if r.get("classified") and (r.get("importance") or 0) >= 3 and not r.get("same_as")
            and start.isoformat() <= _when(r) <= end.isoformat()]
    rows.sort(key=_when, reverse=True)
    rows.sort(key=lambda r: -(r.get("importance") or 0))
    rows = rows[:MAX_ITEMS]
    refs: dict[str, dict] = {}
    items, originals = [], []
    for i, r in enumerate(rows, 1):
        ref = f"W{i}"
        refs[ref] = {"title": r.get("title"), "url": r.get("url"), "source": r.get("source")}
        b = r.get("body_summary") or {}
        items.append({k: v for k, v in {
            "ref": ref, "day_kst": datetime.fromisoformat(_when(r)).astimezone(KST).strftime("%m-%d") if _when(r) else None,
            "source": r.get("source"), "importance": r.get("importance"), "type": r.get("event_type"),
            "coins": r.get("coins") or None,
            "summary": " / ".join(b.get("lead") or b.get("points") or []) or r.get("summary_ko"),
            "numbers": b.get("numbers") or None,
            "same_story_count": (r.get("same_story") or {}).get("count"),
        }.items() if v not in (None, "", [])})
        originals += [r.get("title") or "", r.get("summary") or ""]
    nxt_monday = sunday + timedelta(days=1)
    from_ = datetime(nxt_monday.year, nxt_monday.month, nxt_monday.day, tzinfo=KST).astimezone(timezone.utc)
    cal = []
    for i, c in enumerate(upcoming(calendar or [], from_, 7), 1):
        ref = f"C{i}"
        refs[ref] = {"title": c["name_ko"], "url": c["url"], "source": c["source"]}
        cal.append({"ref": ref, "date_kst": c["date_kst"], "time_kst": c["time_kst"], "name": c["name_ko"],
                    "importance": c["importance"]})
    payload = {"week": week_key(sunday), "items": items, "calendar": cal}
    return payload, refs, "\n".join(originals)


def clean_weekly(raw: dict, refs: dict[str, dict], original: str) -> dict:
    lines = []
    for x in raw.get("lines") or []:
        t = re.sub(r"\s+", " ", QUOTES.sub("", x.get("text") or "")).strip()
        if not t or copied_run(t, original):
            continue
        if len(t) > 80:
            t = t[:79].rstrip() + "…"
        lines.append({"text": t, "refs": [r for r in x.get("refs") or [] if r in refs]})
    nxt = []
    for x in (raw.get("next_week") or [])[:3]:
        t = re.sub(r"\s+", " ", QUOTES.sub("", x.get("text") or "")).strip()
        if t:
            nxt.append({"date_kst": x.get("date_kst", ""), "time_kst": x.get("time_kst", ""), "text": t,
                        "refs": [r for r in x.get("refs") or [] if r in refs]})
    return {"lines": lines[:5], "next_week": nxt}


def weekly_problems(w: dict, original: str) -> list[str]:
    out = []
    if len(w.get("lines", [])) != 5:
        out.append(f"lines {len(w.get('lines', []))} != 5")
    if len(w.get("next_week", [])) > 3:
        out.append("more than 3 next_week items")
    for x in w.get("lines", []) + w.get("next_week", []):
        if copied_run(x["text"], original):
            out.append(f"copied: {x['text']}")
        if QUOTES.search(x["text"]):
            out.append(f"quote: {x['text']}")
    return out


def to_markdown(w: dict) -> str:
    md = [f"# 주간 정리 {w['week']} (코인·세계경제)", "", f"_만든 때: {w['created_at']} · 모델 {w['model']}_", "",
          "## 이번 주 5줄", ""]
    md += [f"{i}. {x['text']}" for i, x in enumerate(w["lines"], 1)]
    md += ["", "## 다음 주 볼 일정", ""]
    md += [f"- {x['date_kst']} {x['time_kst']} — {x['text']}" for x in w["next_week"]] or ["- 큰 일정 없음"]
    md += ["", "## 출처", ""]
    md += [f"- {s['ref']}: [{s.get('title') or s.get('source')}]({s.get('url')})" for s in w["sources"]]
    if w.get("unverified_numbers"):
        md += ["", f"> 입력에서 찾지 못한 숫자: {', '.join(w['unverified_numbers'])}"]
    return "\n".join(md) + "\n"


class Weekly:
    def __init__(self, api_key: str, budget: DailyBudget, data_dir: Path, base_url: str | None = None,
                 model: str = "claude-sonnet-5-5", shared=None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url or "https://api.anthropic.com",
                                          max_retries=2, timeout=120.0)
        self.budget = budget
        self.model = model
        self.shared = shared
        self.dir = data_dir / "weekly"
        self.state_path = self.dir / "state.json"

    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def latest(self) -> dict | None:
        files = sorted(self.dir.glob("*-W*.json")) if self.dir.exists() else []
        if not files:
            return None
        try:
            return json.loads(files[-1].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def tick(self, now: datetime, load_events: Callable[..., list[dict]], calendar: list[dict]) -> str | None:
        st = self._state()
        if st.get("batch_id"):
            return self._collect(st, now)
        sunday = due_sunday(now)
        if sunday is None:
            return None
        key = week_key(sunday)
        if (self.dir / f"{key}.json").exists() or st.get("attempts", {}).get(key, 0) >= 3:
            return None
        return self._submit(sunday, key, load_events(self.dir.parent, 6000), calendar, now, st)

    def _submit(self, sunday: date, key: str, events: list[dict], calendar: list[dict], now: datetime,
                st: dict) -> str:
        payload, refs, original = build_inputs(events, calendar, sunday)
        text = json.dumps(payload, ensure_ascii=False)
        est_in = 1000 + len(text) // 2
        worst = self.budget.worst_case(est_in, MAX_TOKENS, self.model) * 0.5  # batch price
        if self.budget.spent() + worst > self.budget.cap:
            return "skipped: daily cap"
        if self.shared is not None and not self.shared.allows(worst):
            return "skipped: AI+ monthly limit"
        batch = self.client.messages.batches.create(requests=[{
            "custom_id": key,
            "params": {"model": self.model, "max_tokens": MAX_TOKENS, "system": SYSTEM,
                       "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                       "messages": [{"role": "user", "content": "Input JSON:\n" + text}]},
        }])
        attempts = {**st.get("attempts", {}), key: st.get("attempts", {}).get(key, 0) + 1}
        atomic_write_json(self.state_path, {"batch_id": batch.id, "week": key, "sunday": sunday.isoformat(),
                                            "submitted_at": now.isoformat(timespec="seconds"), "refs": refs,
                                            "original": original, "input": text, "attempts": attempts})
        return f"submitted:{key}"

    def _collect(self, st: dict, now: datetime) -> str:
        b = self.client.messages.batches.retrieve(st["batch_id"])
        if b.processing_status != "ended":
            if now - datetime.fromisoformat(st["submitted_at"]) > timedelta(hours=BATCH_EXPIRE_H):
                atomic_write_json(self.state_path, {"attempts": st.get("attempts", {})})
                return f"expired:{st['week']}"
            return f"waiting:{st['week']}"
        result = None
        for entry in self.client.messages.batches.results(st["batch_id"]):
            if entry.custom_id == st["week"]:
                result = entry.result
        atomic_write_json(self.state_path, {"attempts": st.get("attempts", {})})  # clear the pending batch
        if result is None or result.type != "succeeded":
            return f"failed:{st['week']}:{getattr(result, 'type', 'missing')}"
        msg = result.message
        served = msg.model if msg.model in PRICES else self.model
        cost = self.budget.record(msg.usage, served, "weekly", batch=True)
        if msg.stop_reason != "end_turn":
            return f"failed:{st['week']}:stop_reason={msg.stop_reason}"
        raw = json.loads(next(x.text for x in msg.content if x.type == "text"))
        w = clean_weekly(raw, st["refs"], st.get("original", ""))
        used = sorted({r for x in w["lines"] + w["next_week"] for r in x["refs"]}, key=lambda r: (r[0], int(r[1:])))
        rec: dict[str, Any] = {
            "week": st["week"], "sunday": st.get("sunday"), "created_at": now.isoformat(timespec="seconds"),
            "model": served, **w,
            "sources": [{"ref": r, **st["refs"][r]} for r in used],
            "unverified_numbers": unverified_numbers({"situation": w["lines"], "risks": [], "next_7d": w["next_week"],
                                                      "coin_impact": [], "confidence": {}}, st.get("input", "")),
            "cost_usd": round(cost, 6), "batch": True,
        }
        atomic_write_json(self.dir / f"{st['week']}.json", rec)
        tmp = self.dir / f"{st['week']}.md.tmp"
        tmp.write_text(to_markdown(rec), encoding="utf-8")
        tmp.replace(self.dir / f"{st['week']}.md")
        return f"ok:{st['week']} ${cost:.4f}"
