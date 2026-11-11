"""news-feed entry point.

    python run.py                 # poll forever, every NEWS_FEED_INTERVAL_SEC (default 300s)
    python run.py --once          # one poll
    python run.py --duration 3600 # poll for one hour then exit
    python run.py --report        # counts and today's cost
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter

from news_feed.budget import DailyBudget
from news_feed.collector import DATA, Collector, load_config


def report() -> None:
    cfg = load_config()
    rows = []
    path = DATA / "news_events.jsonl"
    if path.exists():
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = [r for r in rows if r["classified"]]
    print(f"events={len(rows)} classified={len(done)}")
    print("by source:", dict(Counter(r["source"] for r in rows)))
    print("by event_type:", dict(Counter(r["event_type"] for r in done)))
    print("by importance:", dict(sorted(Counter(r["importance"] for r in done).items())))
    day = DailyBudget(DATA / "cost_ledger.json", cfg["cap"], cfg["model"]).day()
    print(f"today: ${day['usd']:.6f} / cap ${cfg['cap']:.2f}, calls={day['calls']}, "
          f"in={day['input_tokens']} out={day['output_tokens']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--duration", type=float, help="seconds to run, then exit")
    ap.add_argument("--backfill-hours", type=float, default=24.0,
                    help="ignore items published longer ago than this (first run)")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.report:
        return report()
    c = Collector(load_config(), backfill_hours=args.backfill_hours)
    if args.once:
        print(json.dumps(c.run_once(), ensure_ascii=False, indent=2))
    else:
        c.run_forever(args.duration)


if __name__ == "__main__":
    main()
