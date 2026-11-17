"""news-feed entry point.

    python run.py                 # poll forever, every NEWS_FEED_INTERVAL_SEC (default 300s)
    python run.py --once          # one poll
    python run.py --duration 3600 # poll for one hour then exit
    python run.py --report        # status: classified counts, cost per day, importance, top items, last brief
    python run.py --check-sources # try every source once and print ok / error (writes nothing)
    python run.py --brief-now     # make a briefing right now (costs ~$0.03-0.06)
    python run.py --summarize-backfill  # body summaries for the last 24h of important articles (within the cap)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from news_feed.store import load_events, read_tail
from news_feed.budget import KST, DailyBudget
from news_feed.collector import DATA, Collector, load_config
from news_feed.macro import calendar_summary, fetch_calendar, fetch_markets, market_line
from news_feed.sources import Fetcher


def _kst(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(KST).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return "?"


def report(since: str | None = None) -> None:
    cfg = load_config()
    rows = load_events(DATA, 10**7)
    done = [r for r in rows if r.get("classified")]
    if since:
        done = [r for r in done if (r.get("collected_at") or "") >= since]
    first = min((r["collected_at"] for r in done), default=None)
    print(f"전체 수집 {len(rows)}건 / AI 분류 {len(done)}건" + (f" (첫 분류 {_kst(first)} KST)" if first else ""))
    print("소스별:", dict(Counter(r["source"] for r in rows).most_common()))
    print("분류 유형:", dict(Counter(r["event_type"] for r in done).most_common()))
    print("중요도 분포:", {k: v for k, v in sorted(Counter(r["importance"] for r in done).items())})
    days = DailyBudget(DATA / "cost_ledger.json", cfg["cap"], cfg["model"]).all_days()
    print(f"하루 비용 (한도 ${cfg['cap']:.2f}):")
    for d in sorted(days)[-7:]:
        v = days[d]
        print(f"  {d}: ${v['usd']:.4f}  호출 {v['calls']}회  in {v['input_tokens']:,} / out {v['output_tokens']:,}"
              + ("  " + ", ".join(f"{k} ${m['usd']:.4f}" for k, m in v.get("by_model", {}).items())
                 if v.get("by_model") else ""))
    top = sorted(done, key=lambda r: (r.get("importance") or 0, r.get("published_at") or ""), reverse=True)[:5]
    print("중요도 높은 예시 5개:")
    for r in top:
        print(f"  [{r['importance']}] {_kst(r.get('published_at'))} {r['source']}: {r.get('summary_ko') or r['title']}")
    b = read_tail(DATA / "briefs.jsonl", 1)
    if b:
        print(f"최근 브리핑: {b[-1]['slot']} (모델 {b[-1]['model']}, ${b[-1]['cost_usd']:.4f})")
    sums = [r for r in rows if r.get("body_summary")]
    logs_all = read_tail(DATA / "run_log.jsonl", 5000)
    reads: Counter = Counter()
    for r in logs_all:
        reads.update((r.get("summarize") or {}).get("read") or {})
    tried = sum(reads.values())
    print(f"본문 요약: {len(sums)}건 (원문 {sum(1 for r in sums if r['body_summary']['basis'] == '원문')}, "
          f"제목만 {sum(1 for r in sums if r['body_summary']['basis'] != '원문')})"
          + (f", 본문 못 읽은 비율 {(tried - reads['ok']) / tried:.0%} ({dict(reads.most_common())})" if tried else ""))
    for r in sorted(sums, key=lambda r: (r.get("importance") or 0, r.get("published_at") or ""), reverse=True)[:2]:
        b = r["body_summary"]
        print(f"  [{r.get('importance')}] {r['source']}: {r.get('summary_ko') or r['title']}  ({b['basis']})")
        for p in b["points"]:
            print(f"     - {p}")
        if b["numbers"]:
            print(f"     숫자: {' / '.join(b['numbers'])}")
    stops = Counter((r.get("classify_stopped") or "")[:60] for r in read_tail(DATA / "run_log.jsonl", 2000)
                    if r.get("classify_stopped"))
    if stops:
        print("분류가 멈춘 이유(최근 수집 기록):", dict(stops.most_common(5)))
    print(f"아직 분류 전: {sum(1 for r in rows if not r.get('classified'))}건 (최근 36시간 것은 수집기가 다시 분류함)")
    logs = read_tail(DATA / "run_log.jsonl", 1)
    if logs:
        bad = {k: v for k, v in logs[-1]["sources"].items() if not str(v).startswith("ok")}
        print(f"마지막 수집 {_kst(logs[-1]['at'])} KST, 실패 소스: {bad or '없음'}")


def check_sources() -> int:
    cfg = load_config()
    f = Fetcher(contact=cfg["contact"])
    _, status = f.collect()
    markets, mstat = fetch_markets(f)
    cal, cstat = fetch_calendar(f)
    bad = 0
    for title, st in (("뉴스·공지", status), ("시장 지표", mstat), ("경제 일정", cstat)):
        print(f"[{title}]")
        for k, v in st.items():
            ok = str(v).startswith(("ok", "fallback"))
            bad += not ok
            print(f"  {'OK ' if ok else '실패'} {k:16s} {v}")
    print("[경제 일정 — 소스별 앞으로 일정]")
    for k, v in calendar_summary(cal, datetime.now(timezone.utc)).items():
        print(f"     {k:16s} {v}")
    if str(cstat.get("BLS-schedule", "")).startswith("error") and not cfg["contact"]:
        print("  ※ BLS가 막혔고 .env에 NEWS_FEED_CONTACT(연락처 이메일)가 없습니다. 넣으면 BLS 일정이 열립니다.")
    print("지표 한 줄:", market_line(markets) or "(없음)")
    print(f"실패 {bad}개" if bad else "모두 OK")
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--duration", type=float, help="seconds to run, then exit")
    ap.add_argument("--backfill-hours", type=float, default=24.0,
                    help="ignore items published longer ago than this (first run)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--since", help="--report: only classified rows collected at/after this ISO time")
    ap.add_argument("--check-sources", action="store_true")
    ap.add_argument("--brief-now", action="store_true")
    ap.add_argument("--summarize-backfill", action="store_true")
    ap.add_argument("--log-file", help="write logs here (needed under pythonw, which has no console)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Korean output in a Windows console
    fmt = "%(asctime)s %(levelname)s %(message)s"
    if args.log_file:
        logging.basicConfig(level=logging.INFO, format=fmt, filename=args.log_file, encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)
    if args.report:
        return report(args.since)
    if args.check_sources:
        raise SystemExit(check_sources())
    c = Collector(load_config(), backfill_hours=args.backfill_hours)
    if args.summarize_backfill:
        if c.summarizer is None:
            raise SystemExit("ANTHROPIC_API_KEY가 .env에 없어 요약을 만들 수 없습니다.")
        total: Counter = Counter()
        cost = 0.0
        made = 0
        while True:
            out = c.summarize_pending(datetime.now(timezone.utc), limit=8)
            if not out or not out.get("candidates"):
                print("요약 상태:", (out or {}).get("status", "ok"))
                break
            total.update(out["read"])
            cost += out["cost_usd"]
            made += out["summarized"]
            print(json.dumps(out, ensure_ascii=False))
        n = sum(total.values())
        print(f"요약 {made}건, 비용 ${cost:.4f}, 본문 읽기 {dict(total)}"
              + (f", 못 읽은 비율 {(n - total['ok']) / n:.0%}" if n else ""))
        c.run_once()  # refresh data/feed.json
        return
    if args.brief_now:
        if c.briefer is None:
            raise SystemExit("ANTHROPIC_API_KEY가 .env에 없어 브리핑을 만들 수 없습니다.")
        c.refresh_markets(force=True)
        c.refresh_calendar(force=True)
        now = datetime.now(timezone.utc)
        print(c.maybe_brief(now, force_slot=now.astimezone(KST).strftime("%Y-%m-%d %H:%M") + " manual"))
        c.run_once()  # also refreshes data/feed.json
        print(json.dumps(c.briefer.latest(), ensure_ascii=False, indent=2))
        return
    if args.once:
        print(json.dumps(c.run_once(), ensure_ascii=False, indent=2))
    else:
        pid_file = DATA / "collector.pid"
        DATA.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        try:
            c.run_forever(args.duration)
        finally:
            pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
