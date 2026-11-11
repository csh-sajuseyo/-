"""Poll sources every N seconds, classify new items, append to data/news_events.jsonl."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values

from .budget import DailyBudget
from .classifier import Classifier, rule_tag
from .sources import Fetcher, RawItem

log = logging.getLogger("news_feed")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_config(env_path: Path = ROOT / ".env") -> dict:
    """Settings come only from .env — process environment variables are ignored on purpose."""
    env = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    return {
        "api_key": env.get("ANTHROPIC_API_KEY", "").strip(),
        "base_url": env.get("ANTHROPIC_BASE_URL") or None,
        "model": env.get("NEWS_FEED_MODEL", "claude-haiku-5-5"),
        "cap": float(env.get("NEWS_FEED_DAILY_USD_CAP", "1.00")),
        "interval": int(env.get("NEWS_FEED_INTERVAL_SEC", "300")),
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


class Collector:
    def __init__(self, cfg: dict, data_dir: Path = DATA, backfill_hours: float = 24.0,
                 fetcher: Fetcher | None = None, classifier: Classifier | None = None) -> None:
        self.cfg = cfg
        self.data_dir = data_dir
        self.events_path = data_dir / "news_events.jsonl"
        self.seen = SeenStore(data_dir / "seen_ids.json")
        self.backfill = timedelta(hours=backfill_hours)
        self.fetcher = fetcher or Fetcher()
        self.budget = DailyBudget(data_dir / "cost_ledger.json", cfg["cap"], cfg["model"])
        if classifier is None and cfg["api_key"]:
            classifier = Classifier(cfg["api_key"], cfg["model"], self.budget, cfg["base_url"])
        self.classifier = classifier
        if self.classifier is None:
            log.warning("ANTHROPIC_API_KEY missing in .env — collecting without AI classification")

    def _fresh(self, it: RawItem, now: datetime) -> bool:
        if not it.published_at:
            return True
        return now - datetime.fromisoformat(it.published_at) <= self.backfill

    def run_once(self) -> dict:
        now = datetime.now(timezone.utc)
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

        ai, cost, stop = ({}, 0.0, "no api key") if self.classifier is None else self.classifier.classify(uniq)
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
                    "title": it.title,
                    "url": it.url,
                    "rule_tag": rule_tag(it.title),
                    "classified": it.id in ai,
                    "model": self.cfg["model"] if it.id in ai else None,
                    **(ai.get(it.id) or {"coins": None, "event_type": None, "direction": None,
                                         "importance": None, "summary_ko": None}),
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.seen.save()
        summary = {
            "at": now.isoformat(timespec="seconds"), "sources": status, "new": len(uniq),
            "skipped_old": skipped_old, "classified": len(ai), "cost_usd": round(cost, 6),
            "spent_today_usd": round(self.budget.spent(), 6), "classify_stopped": stop,
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
