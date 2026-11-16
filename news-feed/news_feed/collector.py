"""Poll sources every N seconds, classify new items, append to data/news_events.jsonl.

Also on its own slower clocks: market indicators (15 min), economic calendar (6 h),
the world-economy briefing at the configured KST times, and data/feed.json after every poll.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values

from .brief import RESERVE_PER_BRIEF, Briefer, read_tail, slot_due
from .budget import KST, DailyBudget
from .classifier import Classifier, rule_tag
from .feed import write_feed
from .macro import atomic_write_json, fetch_calendar, fetch_markets
from .sources import Fetcher, RawItem

log = logging.getLogger("news_feed")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

MARKETS_EVERY = 900
CALENDAR_EVERY = 6 * 3600
BRIEF_RETRY_EVERY = 900


def load_config(env_path: Path = ROOT / ".env") -> dict:
    """Settings come only from .env — process environment variables are ignored on purpose."""
    env = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    return {
        "api_key": env.get("ANTHROPIC_API_KEY", "").strip(),
        "base_url": env.get("ANTHROPIC_BASE_URL") or None,
        "model": env.get("NEWS_FEED_MODEL", "claude-haiku-5-5"),
        "brief_model": env.get("NEWS_FEED_BRIEF_MODEL", "claude-sonnet-5-5"),
        "cap": float(env.get("NEWS_FEED_DAILY_USD_CAP", "2.00")),
        "interval": int(env.get("NEWS_FEED_INTERVAL_SEC", "300")),
        "brief_times": [t.strip() for t in env.get("NEWS_FEED_BRIEF_TIMES", "08:20,21:50").split(",") if t.strip()],
        "contact": env.get("NEWS_FEED_CONTACT", "").strip(),  # optional, for BLS' user-agent policy
    }


class SeenStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.ids: set[str] = set(json.loads(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            self.ids = set()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self.ids)), encoding="utf-8")
        tmp.replace(self.path)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class Collector:
    def __init__(self, cfg: dict, data_dir: Path = DATA, backfill_hours: float = 24.0,
                 fetcher: Fetcher | None = None, classifier: Classifier | None = None,
                 briefer: Briefer | None = None, macro: bool = True) -> None:
        self.cfg = cfg
        self.data_dir = data_dir
        self.events_path = data_dir / "news_events.jsonl"
        self.seen = SeenStore(data_dir / "seen_ids.json")
        self.backfill = timedelta(hours=backfill_hours)
        self.fetcher = fetcher or Fetcher(contact=cfg.get("contact", ""))
        self.budget = DailyBudget(data_dir / "cost_ledger.json", cfg["cap"], cfg["model"])
        self.brief_times = cfg.get("brief_times", [])
        if classifier is None and cfg["api_key"]:
            classifier = Classifier(cfg["api_key"], cfg["model"], self.budget, cfg["base_url"])
        if briefer is None and cfg["api_key"] and self.brief_times:
            briefer = Briefer(cfg["api_key"], self.budget, data_dir, cfg["base_url"],
                              cfg.get("brief_model", "claude-sonnet-5-5"))
        self.classifier = classifier
        self.briefer = briefer
        self.macro = macro  # markets + calendar (off in unit tests)
        self.markets = _load_json(data_dir / "markets.json", {}).get("items", {})
        self.calendar = _load_json(data_dir / "calendar.json", {}).get("events", [])
        self.status: dict[str, str] = {}
        self._next_markets = 0.0
        self._next_calendar = 0.0
        self._brief_tries: dict[str, list[float]] = {}
        if self.classifier is None:
            log.warning("ANTHROPIC_API_KEY missing in .env — collecting without AI classification or briefings")

    # ------------------------------------------------------------------ helpers
    def _fresh(self, it: RawItem, now: datetime) -> bool:
        if not it.published_at:
            return True
        return now - datetime.fromisoformat(it.published_at) <= self.backfill

    def _reserve(self, now: datetime) -> float:
        """USD to keep free for today's briefings that have not happened yet."""
        if self.briefer is None:
            return 0.0
        k = now.astimezone(KST)
        left = sum(1 for s in self.brief_times if k.strftime("%H:%M") < s)
        return left * RESERVE_PER_BRIEF

    def refresh_markets(self, force: bool = False) -> dict[str, str]:
        if not (force or time.monotonic() >= self._next_markets):
            return {}
        self._next_markets = time.monotonic() + MARKETS_EVERY
        hist = read_tail(self.data_dir / "markets_history.jsonl", 200)
        now = datetime.now(timezone.utc)
        items, status = fetch_markets(self.fetcher, hist, now)
        if items:
            self.markets = {**self.markets, **items}  # keep last good value of an indicator that failed now
            atomic_write_json(self.data_dir / "markets.json",
                              {"updated_at": now.isoformat(timespec="seconds"), "items": self.markets})
            with (self.data_dir / "markets_history.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": now.isoformat(timespec="seconds"),
                                     "values": {k: v["value"] for k, v in items.items()}}) + "\n")
        return {f"mkt:{k}": v for k, v in status.items()}

    def refresh_calendar(self, force: bool = False) -> dict[str, str]:
        if not (force or time.monotonic() >= self._next_calendar):
            return {}
        self._next_calendar = time.monotonic() + CALENDAR_EVERY
        events, status = fetch_calendar(self.fetcher)
        if events:
            self.calendar = events
            atomic_write_json(self.data_dir / "calendar.json",
                              {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "events": events})
        return {f"cal:{k}": v for k, v in status.items()}

    def maybe_brief(self, now: datetime, force_slot: str | None = None) -> str | None:
        if self.briefer is None:
            return None
        slot = force_slot or slot_due(now, self.brief_times, self.briefer.done_slots())
        if not slot:
            return None
        tries = self._brief_tries.setdefault(slot, [])
        if not force_slot and (len(tries) >= 3 or (tries and time.monotonic() - tries[-1] < BRIEF_RETRY_EVERY)):
            return None
        tries.append(time.monotonic())
        try:
            events = read_tail(self.events_path, 3000)
            rec = self.briefer.make(events, self.markets, self.calendar, slot, now)
            return f"ok:{slot} ${rec['cost_usd']:.4f}"
        except Exception as exc:
            log.warning("brief %s failed: %s", slot, exc)
            return f"error:{type(exc).__name__}: {str(exc)[:160]}"

    # ------------------------------------------------------------------ main poll
    def run_once(self) -> dict:
        now = datetime.now(timezone.utc)
        slow: dict[str, str] = {}
        if self.macro:
            for job in (self.refresh_markets, self.refresh_calendar):
                try:
                    slow.update(job())
                except Exception as exc:
                    slow[job.__name__] = f"error:{type(exc).__name__}"
        items, status = self.fetcher.collect()
        new: list[RawItem] = []
        skipped_old = 0
        for it in items:
            if it.id in self.seen.ids:
                continue
            self.seen.ids.add(it.id)
            if self._fresh(it, now):
                new.append(it)
            else:
                skipped_old += 1
        # same id can appear twice in one poll (e.g. a Binance notice in two catalogs)
        uniq = list({it.id: it for it in new}.values())

        if self.classifier is None:
            ai, cost, stop = {}, 0.0, "no api key"
        else:
            self.classifier.reserve = self._reserve(now)
            ai, cost, stop = self.classifier.classify(uniq)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as fh:
            for it in uniq:
                row = {
                    "id": it.id,
                    "collected_at": now.isoformat(timespec="seconds"),
                    "published_at": it.published_at,
                    "source": it.source,
                    "source_kind": it.kind,
                    "source_category": it.category,
                    "group": it.group,
                    "title": it.title,
                    "summary": it.summary or None,
                    "url": it.url,
                    "rule_tag": rule_tag(it.title),
                    "classified": it.id in ai,
                    "model": self.cfg["model"] if it.id in ai else None,
                    **(ai.get(it.id) or {"coins": None, "event_type": None, "direction": None,
                                         "importance": None, "summary_ko": None,
                                         "why": None, "crypto_impact": None}),
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.seen.save()

        brief = self.maybe_brief(now)
        self.status = {**self.status, **status, **slow}
        try:
            write_feed(self.data_dir, self.markets, self.calendar, self.status, now)
            feed = "ok"
        except Exception as exc:
            feed = f"error:{type(exc).__name__}"
        summary = {
            "at": now.isoformat(timespec="seconds"), "sources": status, "slow": slow or None,
            "new": len(uniq), "skipped_old": skipped_old, "classified": len(ai), "cost_usd": round(cost, 6),
            "spent_today_usd": round(self.budget.spent(), 6), "classify_stopped": stop,
            "brief": brief, "feed": feed,
        }
        with (self.data_dir / "run_log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
        return summary

    def run_forever(self, duration_sec: float | None = None) -> None:
        end = time.monotonic() + duration_sec if duration_sec else None
        interval = self.cfg["interval"]
        while True:
            started = time.monotonic()
            try:
                log.info("%s", json.dumps(self.run_once(), ensure_ascii=False))
            except Exception:
                log.exception("poll failed")
            if end and time.monotonic() + interval > end:
                return
            time.sleep(max(1.0, interval - (time.monotonic() - started)))
