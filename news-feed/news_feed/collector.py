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

from .brief import RESERVE_PER_BRIEF, Briefer, slot_due
from .budget import KST, DailyBudget
from .classifier import Classifier, rule_tag
from .feed import write_feed
from .macro import atomic_write_json, fetch_calendar, fetch_markets
from .sources import Fetcher, RawItem
from .store import append_labels, append_summaries, load_events
from .article import ArticleReader
from .ailedger import SharedLedger
from .article import extract_text
from .sources import clean_text
from .summarize import STOPPED as SUMMARY_STOPPED, Summarizer, as_item, needs_summary, same_story
from .weekly import Weekly

log = logging.getLogger("news_feed")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

MARKETS_EVERY = 900
CALENDAR_EVERY = 6 * 3600
BRIEF_RETRY_EVERY = 900
RELABEL_PER_POLL = 24      # unclassified items retried per poll
RELABEL_WINDOW_H = 36      # only items newer than this
SUMMARY_PER_POLL = 12      # body summaries per poll (pages are read politely, one site at a time)
SUMMARY_WINDOW_H = 24
SAME_STORY_WINDOW_H = 36
MONTHLY_STOPPED = "요약 멈춤(AI+ 월 한도)"
RSS_TEXT_KEEP = 2000       # feed full texts kept in memory (never on disk) until summarised


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
        "summary_cap": float(env.get("NEWS_FEED_SUMMARY_USD_CAP", "0.30")),
        # AI+ : body summaries, briefings and the weekly digest. off = classification only.
        "ai_plus": env.get("NEWS_AI_PLUS", "on").strip().lower() not in ("off", "0", "false", "no"),
        "shared_dir": env.get("NEWS_AI_SHARED_DIR", "").strip() or str(ROOT.parent / "_ai"),
        "weekly_model": env.get("NEWS_FEED_WEEKLY_MODEL", "claude-sonnet-5-5"),
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
                 briefer: Briefer | None = None, macro: bool = True,
                 summarizer: Summarizer | None = None, reader: ArticleReader | None = None,
                 weekly: Weekly | None = None, shared: SharedLedger | None = None) -> None:
        self.cfg = cfg
        self.data_dir = data_dir
        self.events_path = data_dir / "news_events.jsonl"
        self.seen = SeenStore(data_dir / "seen_ids.json")
        self.backfill = timedelta(hours=backfill_hours)
        self.fetcher = fetcher or Fetcher(contact=cfg.get("contact", ""))
        self.budget = DailyBudget(data_dir / "cost_ledger.json", cfg["cap"], cfg["model"])
        self.shared = shared if shared is not None else (
            SharedLedger(Path(cfg["shared_dir"])) if cfg.get("shared_dir") else None)
        self.budget.shared = self.shared  # every Claude call is also written to the AI+ shared ledger
        self.brief_times = cfg.get("brief_times", [])
        self.ai_plus = cfg.get("ai_plus", True)
        key = cfg["api_key"]
        if classifier is None and key:
            classifier = Classifier(key, cfg["model"], self.budget, cfg["base_url"])
        if not self.ai_plus:  # NEWS_AI_PLUS=off: classification only
            briefer = summarizer = weekly = None
        else:
            if briefer is None and key and self.brief_times:
                briefer = Briefer(key, self.budget, data_dir, cfg["base_url"], cfg.get("brief_model", "claude-sonnet-5-5"))
            if summarizer is None and key:
                summarizer = Summarizer(key, cfg["model"], self.budget, cfg.get("summary_cap", 0.30), cfg["base_url"],
                                        shared=self.shared)
            if weekly is None and key:
                weekly = Weekly(key, self.budget, data_dir, cfg["base_url"], cfg.get("weekly_model", "claude-sonnet-5-5"),
                                shared=self.shared)
        self.classifier = classifier
        self.briefer = briefer
        self.summarizer = summarizer
        self.weekly = weekly
        self.rss_text: dict[str, str] = {}
        self.reader = reader or (ArticleReader() if summarizer is not None else None)
        self.summary_status = "ok" if summarizer is not None else "off"
        self._sum_stop_day: str | None = None
        self._sum_tried: set[str] = set()
        self.macro = macro  # markets + calendar (off in unit tests)
        self.markets = _load_json(data_dir / "markets.json", {}).get("items", {})
        self.calendar = _load_json(data_dir / "calendar.json", {}).get("events", [])
        self.status: dict[str, str] = {}
        self._next_markets = 0.0
        self._next_calendar = 0.0
        self._brief_tries: dict[str, list[float]] = {}
        self._relabel_tried: set[str] = set()
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
        now = datetime.now(timezone.utc)
        items, status = fetch_markets(self.fetcher)
        if items:
            # keep the last good value of an indicator that failed now; drop keys we no longer publish
            self.markets = {k: v for k, v in {**self.markets, **items}.items() if k in status}
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

    def relabel(self, now: datetime) -> str | None:
        """Retry AI labels for recent items that were stored unclassified (written to labels.jsonl)."""
        if self.classifier is None:
            return None
        since = (now - timedelta(hours=RELABEL_WINDOW_H)).isoformat()
        todo = [r for r in load_events(self.data_dir, 3000)
                if not r.get("classified") and r.get("id") not in self._relabel_tried
                and (r.get("published_at") or r.get("collected_at") or "") >= since]
        todo = todo[-RELABEL_PER_POLL:]
        if not todo:
            return None
        items = [RawItem(source=r.get("source") or "", kind=r.get("source_kind") or "rss", title=r.get("title") or "",
                         url=r.get("url") or "", published_at=r.get("published_at"),
                         category=r.get("source_category"), group=r.get("group") or "crypto",
                         summary=r.get("summary") or "") for r in todo]
        by_new_id = {it.id: r["id"] for it, r in zip(items, todo)}
        self.classifier.reserve = self._reserve(now)
        ai, cost, stop = self.classifier.classify(items)
        if stop and "daily cap" in stop:
            return f"stopped: {stop[:120]}"  # budget: try these again later
        self._relabel_tried.update(r["id"] for r in todo)
        labels = {by_new_id.get(k, k): v for k, v in ai.items()}
        append_labels(self.data_dir, labels, self.cfg["model"], now.isoformat(timespec="seconds"))
        return f"{len(labels)}/{len(todo)} ${cost:.4f}" + (f" ({stop[:80]})" if stop else "")

    def _monthly_ok(self) -> bool:
        return self.shared is None or self.shared.month_spent() < self.shared.monthly_limit()

    def summarize_pending(self, now: datetime, limit: int = SUMMARY_PER_POLL,
                          window_h: float = SUMMARY_WINDOW_H) -> dict | None:
        """Read + summarise recent classified articles that have no body_summary yet, most important first.
        Articles about a story that already has (or gets, in this run) a summary are linked to it instead."""
        if self.summarizer is None or self.reader is None:
            return None
        today = DailyBudget.today()
        if self._sum_stop_day == today:
            self.summary_status = SUMMARY_STOPPED
            return {"status": SUMMARY_STOPPED}
        if not self._monthly_ok():
            self.summary_status = MONTHLY_STOPPED
            return {"status": MONTHLY_STOPPED}
        events = load_events(self.data_dir, 3000)
        since = (now - timedelta(hours=window_h)).isoformat()
        since_story = (now - timedelta(hours=SAME_STORY_WINDOW_H)).isoformat()
        when = lambda r: r.get("published_at") or r.get("collected_at") or ""  # noqa: E731
        reps = [r for r in events if r.get("body_summary") and not r.get("same_as") and when(r) >= since_story]
        rows = [r for r in events if needs_summary(r) and r["id"] not in self._sum_tried and when(r) >= since]
        if not rows:
            self.summary_status = "ok"
            return None
        rows.sort(key=when, reverse=True)  # newest first, then by importance / my coins (stable)
        rows.sort(key=lambda r: (-(r.get("importance") or 0), -len(set(r.get("coins") or []) & {"BTC", "XLM"})))
        chosen: list[dict] = []
        links: dict[str, str] = {}
        for r in rows:
            rep = next((x for x in reps + chosen if x["id"] != r["id"] and same_story(r, x)), None)
            if rep is not None:
                links[r["id"]] = rep["id"]
            elif len(chosen) < limit:
                chosen.append(r)
        reads: dict[str, dict[str, int]] = {}
        items = []
        for r in chosen:
            text, st = self.rss_text.get(r["id"], ""), "rss"
            if not text:
                text, st = self.reader.read(r.get("url") or "")
            key = st.split(":")[0]
            src = reads.setdefault(r.get("source") or "?", {})
            src[key] = src.get(key, 0) + 1
            items.append(as_item(r, text))
            text = ""  # the article text is not kept
        spent0 = self.summarizer.spent()
        done, stop = self.summarizer.summarize(items, now)
        # a link only counts once its representative has (or will get) a summary
        keep_links = {d: rep for d, rep in links.items() if rep in done or any(x["id"] == rep for x in reps)}
        append_summaries(self.data_dir, done, keep_links)
        for i in done:
            self.rss_text.pop(i, None)
        if stop and stop.startswith(SUMMARY_STOPPED):
            self._sum_stop_day = today
            self.summary_status = SUMMARY_STOPPED if "monthly" not in stop else MONTHLY_STOPPED
            self._sum_tried.update(done)
        elif stop and stop.startswith("요약 일부 멈춤"):
            self.summary_status = stop
            self._sum_tried.update(done)
        else:
            self.summary_status = "ok"
            self._sum_tried.update(r["id"] for r in chosen)
        total = {}
        for per in reads.values():
            for k, v in per.items():
                total[k] = total.get(k, 0) + v
        return {"status": self.summary_status, "candidates": len(rows), "chosen": len(chosen),
                "summarized": len(done), "same_story_linked": len(keep_links), "read": total,
                "read_by_source": reads, "cost_usd": round(self.summarizer.spent() - spent0, 6),
                "error": stop if stop and not stop.startswith(("요약 멈춤", "요약 일부 멈춤")) else None}

    def maybe_weekly(self, now: datetime) -> str | None:
        if self.weekly is None:
            return None
        try:
            return self.weekly.tick(now, load_events, self.calendar)
        except Exception as exc:
            log.warning("weekly failed: %s", exc)
            return f"error:{type(exc).__name__}: {str(exc)[:160]}"

    def maybe_brief(self, now: datetime, force_slot: str | None = None) -> str | None:
        if self.briefer is None:
            return None
        if not self._monthly_ok():
            return "skipped: AI+ monthly limit"
        slot = force_slot or slot_due(now, self.brief_times, self.briefer.done_slots())
        if not slot:
            return None
        tries = self._brief_tries.setdefault(slot, [])
        if not force_slot and (len(tries) >= 3 or (tries and time.monotonic() - tries[-1] < BRIEF_RETRY_EVERY)):
            return None
        tries.append(time.monotonic())
        try:
            events = load_events(self.data_dir, 3000)
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
        for it in uniq:  # feed full text: memory only, used by the summariser instead of reading the page
            raw = it.extra.get("rss_html")
            if raw:
                txt = extract_text(raw) or clean_text(raw, 3000)
                if len(txt) >= 600:
                    self.rss_text[it.id] = txt[:3000]
        while len(self.rss_text) > RSS_TEXT_KEEP:
            self.rss_text.pop(next(iter(self.rss_text)))

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

        try:
            relabel = self.relabel(now)
        except Exception as exc:
            relabel = f"error:{type(exc).__name__}"
        try:
            summ = self.summarize_pending(now)
        except Exception as exc:
            summ = {"status": f"error:{type(exc).__name__}"}
        brief = self.maybe_brief(now)
        weekly = self.maybe_weekly(now)
        self.status = {**self.status, **status, **slow}
        try:
            write_feed(self.data_dir, self.markets, self.calendar, self.status, now,
                       summary_status=self.summary_status if self.summarizer else "off",
                       ai_plus={"on": self.ai_plus, **(self.shared.status() if self.shared else {})})
            feed = "ok"
        except Exception as exc:
            feed = f"error:{type(exc).__name__}"
        summary = {
            "at": now.isoformat(timespec="seconds"), "sources": status, "slow": slow or None,
            "new": len(uniq), "skipped_old": skipped_old, "classified": len(ai), "cost_usd": round(cost, 6),
            "spent_today_usd": round(self.budget.spent(), 6), "classify_stopped": stop,
            "relabel": relabel, "summarize": summ, "brief": brief, "weekly": weekly, "feed": feed,
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
