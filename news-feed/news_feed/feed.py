"""Read-only summary for the chart helper's News tab: data/feed.json (written atomically).

Format: docs/FEED-FORMAT.md. Bump FEED_VERSION on breaking changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .brief import read_tail
from .classifier import MACRO_TYPES
from .macro import atomic_write_json, market_line, upcoming

FEED_VERSION = 1
MAX_ITEMS = 200
MY_COINS = ("BTC", "XLM")


def topic_of(row: dict) -> str:
    """'exchange' | 'macro' | 'crypto' — the News tab's main filter."""
    if row.get("group") == "exchange" or row.get("source_kind") in ("upbit_notice", "binance_notice"):
        return "exchange"
    if row.get("group") == "macro" or row.get("event_type") in MACRO_TYPES:
        return "macro"
    return "crypto"


def feed_item(row: dict) -> dict:
    coins = row.get("coins") or []
    return {
        "id": row["id"],
        "published_at": row.get("published_at") or row.get("collected_at"),
        "source": row.get("source"),
        "topic": topic_of(row),
        "title": row.get("title"),
        "url": row.get("url"),
        "summary_ko": row.get("summary_ko"),
        "why": row.get("why"),
        "crypto_impact": row.get("crypto_impact"),
        "event_type": row.get("event_type"),
        "direction": row.get("direction"),
        "importance": row.get("importance"),
        "coins": coins,
        "my_coins": [c for c in coins if c in MY_COINS],
        "classified": bool(row.get("classified")),
    }


def build_feed(data_dir: Path, markets: dict[str, Any], calendar: list[dict], sources: dict[str, str],
               now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = read_tail(data_dir / "news_events.jsonl", 3000)
    rows.sort(key=lambda r: r.get("published_at") or r.get("collected_at") or "", reverse=True)
    seen, items = set(), []
    for r in rows:
        if r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        items.append(feed_item(r))
        if len(items) >= MAX_ITEMS:
            break
    briefs = read_tail(data_dir / "briefs.jsonl", 1)
    return {
        "version": FEED_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "brief": briefs[-1] if briefs else None,
        "markets": {"line_ko": market_line(markets), "items": markets},
        "calendar_7d": upcoming(calendar, now, 7),
        "items": items,
        "sources": sources,
    }


def write_feed(data_dir: Path, *args, **kw) -> Path:
    path = data_dir / "feed.json"
    atomic_write_json(path, build_feed(data_dir, *args, **kw))
    return path
