"""Body summaries (Haiku 5.5, thinking off): read-instead-of-clicking Korean summaries.

body_summary (v2; the v1 fields `points` and `numbers` are still filled so older screens keep working)
  lead    : 3 lines 「무슨 일 · 왜 · 그래서」 (each <= 60 chars)
  body    : 6-10 Korean sentences in 2-3 paragraphs (paragraphs separated by a blank line)
  numbers : 0-5 key figures that appear in the source (unit changes like 4,500만 달러 <-> $45 million allowed)
  impact  : 1 line 「코인에 주는 뜻」 that always ends with "(추정)"
  points  : = lead (v1 compatibility)
  basis   : "원문" (article / RSS full text) or "제목만" (title + short description only: shorter lead/body)
  read_at : UTC

Rules enforced in code (not only asked of the model): no sentence may share more than 15 consecutive characters
with the source, quote marks are removed, numbers must exist in the source. The article text is never stored.

Cost: its own daily cap (NEWS_FEED_SUMMARY_USD_CAP, default $0.30) inside the global daily cap and the AI+
monthly limit. Close to the cap, lower-importance items stop first (see `min_importance`).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

from .budget import DailyBudget

MAX_TOKENS = 4800
BATCH = 3
LINE_MAX = 60
COPY_RUN = 15  # a line may not share more than this many consecutive characters with the source
_EST_FIXED = 1100
MY_COINS = ("BTC", "XLM")
STOPPED = "요약 멈춤(오늘 상한)"

SYSTEM = """You turn one news article into Korean reading material, so that a retail investor never needs to open
the original. Each input item has basis "원문" (article text given) or "제목만" (only title / short description).
Return for each item:
- lead: exactly 3 short Korean lines (each at most 55 characters): 1) 무슨 일이 있었나 2) 왜 그런가 / 배경
  3) 그래서 어떻게 되나 / 다음 단계. For "제목만" write only what the title and description state (1-2 lines are fine).
- body: for "원문", 6 to 10 Korean sentences in 2 or 3 paragraphs, separated by a blank line ("\\n\\n"),
  covering the facts, the background and what comes next, so the reader learns the article's content.
  For "제목만", 1 or 2 sentences only, with nothing beyond the title and description.
- numbers: 0 to 5 key figures that appear in the input, each with a short Korean label,
  e.g. "BTC 64,800달러 (-3.5%)", "피해액 4,500만 달러". Only numbers present in the input.
- impact: one Korean line on what it may mean for crypto (BTC first), ending with "(추정)".
  If there is no link, write "코인 시장과 직접 관련은 적음 (추정)".
Rules: rewrite everything in your own Korean words — never copy a sentence or a long phrase, no quotations and
no quotation marks. Never reuse more than 15 characters of the original in a row: write long names short or in
Korean (e.g. "HIVE", "연준", "미 노동부") instead of the full original name. Leave out reporter names, photo captions, ads, promotions and newsletter text. Facts from the
input only: no guesses (except the clearly marked impact line), no advice, no hype words.
Return every input id exactly once."""

SCHEMA = {
    "type": "object",
    "properties": {"summaries": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"},
                       "lead": {"type": "array", "items": {"type": "string"}},
                       "body": {"type": "string"},
                       "numbers": {"type": "array", "items": {"type": "string"}},
                       "impact": {"type": "string"}},
        "required": ["id", "lead", "body", "numbers", "impact"], "additionalProperties": False}}},
    "required": ["summaries"], "additionalProperties": False,
}

QUOTES = re.compile(r"[\"“”‘’'「」『』《》]")


class SummaryCapReached(Exception):
    pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def copied_run(line: str, source: str, n: int = COPY_RUN) -> bool:
    """True if `line` shares more than n consecutive characters with `source` (whitespace-normalised)."""
    p, src = _norm(line), _norm(source)
    for i in range(0, max(0, len(p) - n)):
        if p[i:i + n + 1] in src:
            return True
    return False


def _number_in(d: str, hay: str) -> bool:
    """Digits found in the source, allowing unit changes: 4,500만 달러 <-> $45 million, 1.2조 <-> 1,200 billion."""
    sig = d.replace(".", "").strip("0") or "0"
    return d in hay or (len(sig) >= 2 and sig in hay.replace(".", ""))


def sentences(text: str) -> list[str]:
    """Korean/English sentence split (good enough to count and filter sentences)."""
    out = []
    for para in re.split(r"\n\s*\n", text or ""):
        for s in re.split(r"(?<=[.!?…])\s+", _norm(para)):
            if s:
                out.append(s)
    return out


def _clean_line(s: str) -> str:
    return _norm(QUOTES.sub("", s)).lstrip("-•·▶ ")


def clean_summary(raw: dict, source_text: str, basis: str) -> dict:
    """Enforce the format and the no-copy / no-invented-number rules. Returns the stored fields (minus basis/read_at)."""
    src = source_text or ""
    lead = []
    for p in (raw.get("lead") or raw.get("points") or []):
        p = _clean_line(p)
        if not p or copied_run(p, src):
            continue
        if len(p) > LINE_MAX:
            p = p[: LINE_MAX - 1].rstrip() + "…"
        if p not in lead:
            lead.append(p)
    lead = lead[:3] if basis == "원문" else lead[:2]

    paras = []
    for para in re.split(r"\n\s*\n", raw.get("body") or ""):
        kept = [s for s in (_clean_line(x) for x in re.split(r"(?<=[.!?…])\s+", _norm(QUOTES.sub("", para))))
                if s and not copied_run(s, src)]
        if kept:
            paras.append(" ".join(kept))
    max_sent = 10 if basis == "원문" else 2
    out_paras, n = [], 0
    for para in paras[:3]:
        ss = re.split(r"(?<=[.!?…])\s+", para)
        take = ss[: max(0, max_sent - n)]
        if take:
            out_paras.append(" ".join(take))
            n += len(take)
    body = "\n\n".join(out_paras)

    hay = _norm(src).replace(",", "")
    nums = []
    for x in raw.get("numbers") or []:
        x = _clean_line(x)[:40]
        digits = [d.replace(",", "") for d in re.findall(r"\d[\d,]*(?:\.\d+)?", x)]
        if x and digits and all(_number_in(d, hay) for d in digits) and x not in nums:
            nums.append(x)
    nums = nums[:5]

    impact = _clean_line(raw.get("impact") or "")
    if impact and copied_run(impact, src):
        impact = ""
    impact = re.sub(r"\s*\(?추정\)?\s*$", "", impact).strip()
    if len(impact) > LINE_MAX:
        impact = impact[: LINE_MAX - 1].rstrip() + "…"
    impact = f"{impact} (추정)" if impact else "코인 시장과 직접 관련은 적음 (추정)"
    return {"lead": lead, "body": body, "numbers": nums, "impact": impact,
            "points": list(lead), "format": 2}


def format_problems(s: dict, source_text: str) -> list[str]:
    """Checks used by tests and the report; empty list = OK."""
    out = []
    full = s.get("basis") == "원문"
    lo, hi = (3, 3) if full else (1, 2)
    if not lo <= len(s.get("lead", [])) <= hi:
        out.append(f"lead {len(s.get('lead', []))} not in {lo}-{hi}")
    n = len(sentences(s.get("body", "")))
    paras = [p for p in re.split(r"\n\s*\n", s.get("body", "")) if p.strip()]
    if full and not (6 <= n <= 10 and 2 <= len(paras) <= 3):
        out.append(f"body {n} sentences / {len(paras)} paragraphs (need 6-10 / 2-3)")
    if not full and n > 2:
        out.append(f"title-only body has {n} sentences")
    if not s.get("impact", "").endswith("(추정)"):
        out.append("impact without (추정)")
    if len(s.get("numbers", [])) > 5:
        out.append("more than 5 numbers")
    lines = list(s.get("lead", [])) + sentences(s.get("body", "")) + [s.get("impact", "")]
    out += [f"too long: {p}" for p in s.get("lead", []) if len(p) > LINE_MAX]
    out += [f"copied: {p}" for p in lines if p and copied_run(p, source_text)]
    out += [f"quote: {p}" for p in lines if QUOTES.search(p or "")]
    if s.get("points") != s.get("lead"):
        out.append("points (v1) not equal to lead")
    return out


def min_importance(spent: float, cap: float) -> int:
    """Priority close to the cap: low-importance items stop first (BTC/XLM items always pass while money is left)."""
    used = spent / cap if cap > 0 else 1.0
    if used < 0.6:
        return 1
    if used < 0.85:
        return 3
    return 4


class Summarizer:
    def __init__(self, api_key: str, model: str, budget: DailyBudget, cap: float = 0.30,
                 base_url: str | None = None, shared=None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url or "https://api.anthropic.com",
                                          max_retries=2, timeout=120.0)
        self.model = model
        self.budget = budget
        self.cap = cap
        self.shared = shared  # AI+ monthly limit

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
        if self.shared is not None and not self.shared.allows(worst):
            raise SummaryCapReached("AI+ monthly limit")
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
        """items: {id, source, title, text, basis, importance, my_coin} in priority order.
        Returns ({id: body_summary}, stop reason or None)."""
        now = now or datetime.now(timezone.utc)
        done: dict[str, dict] = {}
        errors: list[str] = []
        queue = list(items)
        while queue:
            floor = min_importance(self.spent(), self.cap)
            queue = [b for b in queue if (b.get("importance") or 0) >= floor or b.get("my_coin")]
            if not queue:
                return done, f"요약 일부 멈춤(상한 근접: 중요도 {floor} 이상·내 코인만)"
            batch, queue = queue[:BATCH], queue[BATCH:]
            try:
                got = self._call(batch)
            except SummaryCapReached as exc:
                return done, f"{STOPPED}: {exc}"
            except (anthropic.APIError, RuntimeError, ValueError, KeyError, StopIteration) as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
                continue
            for b in batch:
                raw = got.get(b["id"])
                if not raw:
                    continue
                s = clean_summary(raw, b["text"] + " " + b["title"], b["basis"])
                if not s["lead"]:
                    continue
                done[b["id"]] = {**s, "basis": b["basis"], "read_at": now.isoformat(timespec="seconds")}
        return done, ("; ".join(errors)[:300] or None)


# ----------------------------------------------------------------------------- who gets a summary

NOISE = re.compile(
    r"sponsored|press release|\bpromoted\b|partner content|advertorial|\bgiveaway\b|airdrop campaign|"
    r"price prediction|price analysis|price forecast|price today|price alert|technical analysis:|"
    r"\bwill (?:\w+ )?(?:hit|reach) \$|top \d+ (?:coins|altcoins) to buy|best crypto to buy|"
    r"\[광고\]|\[홍보\]|\[보도자료\]|이벤트 안내|에어드랍 이벤트|시세 알림|가격 알림|오늘의 시세|\[시황\]|\[코인시황\]",
    re.I)


def is_noise(row: dict) -> bool:
    """Ads, promotions and price-alert / price-prediction pieces: not worth a body summary."""
    return bool(NOISE.search(row.get("title") or ""))


def needs_summary(row: dict, my_coins: tuple[str, ...] = MY_COINS) -> bool:
    """Every classified crypto / world-economy item (and exchange notices about my coins), except noise."""
    if not row.get("classified") or row.get("body_summary") or row.get("same_as") or is_noise(row):
        return False
    mine = bool(set(row.get("coins") or []) & set(my_coins))
    exchange = row.get("group") == "exchange" or row.get("source_kind") in ("upbit_notice", "binance_notice")
    return mine or not exchange


def source_text_for(row: dict) -> str:
    """What the title-only fallback is allowed to use: RSS description, else the title."""
    return row.get("summary") or row.get("title") or ""


def as_item(row: dict, text: str) -> dict[str, Any]:
    return {"id": row["id"], "source": row.get("source"), "title": row.get("title") or "",
            "text": text or source_text_for(row), "basis": "원문" if text else "제목만",
            "importance": row.get("importance") or 0,
            "my_coin": bool(set(row.get("coins") or []) & set(MY_COINS))}


# ----------------------------------------------------------------------------- same story

_STOP = set("the a an of to in on for and or with as at by from is are was were be its it this that after over "
            "says said amid new report reports update".split())


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$%.]+|[가-힣]{2,}", (title or "").lower()) if w not in _STOP}


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jac(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def same_story(a: dict, b: dict) -> bool:
    """Two items about the same event: similar titles, or similar Korean one-line AI summaries with the same coins."""
    if _jac(_words(a.get("title")), _words(b.get("title"))) >= 0.5:
        return True
    ka, kb = a.get("summary_ko") or "", b.get("summary_ko") or ""
    if ka and kb and _jac(_bigrams(ka), _bigrams(kb)) >= 0.55:
        return set(a.get("coins") or []) == set(b.get("coins") or [])
    return False
