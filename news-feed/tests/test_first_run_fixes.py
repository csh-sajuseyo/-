"""Regression tests for the problems seen on the first real run (2026-11-16 PC run)."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from news_feed.brief import build_inputs
from news_feed.budget import DailyBudget
from news_feed.collector import Collector
from news_feed.feed import build_feed, feed_item
from news_feed.macro import (calendar_from_ics, calendar_summary, fomc_events, later, parse_fomc_page,
                             upcoming)
from news_feed.sources import Fetcher
from news_feed.store import load_events

NOW = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)  # 2026-11-16 09:00 KST

# BEA-style feed: all-day entries, quoted TZID, calendar default zone, UTC, folded and escaped summaries
BEA_ICS = (
    "﻿BEGIN:VCALENDAR\r\nX-WR-TIMEZONE:America/New_York\r\n"
    "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261120\r\nSUMMARY:Personal Income and Outlays\\, October 2026\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART;TZID=\"America/New_York\":20261125T083000\r\nSUMMARY:GDP (Second Estimate)\\, 3rd Quar\r\n"
    " ter 2026\r\nEND:VEVENT\r\n"
    "begin:vevent\r\ndtstart:20261119T133000Z\r\nsummary:GDP by State\\, 2nd Quarter 2026\r\nend:vevent\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20261118T0830\r\nSUMMARY:Gross Domestic Product by Industry\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20251120T083000\r\nSUMMARY:GDP (Advance)\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_bea_calendar_formats_all_land_in_window():
    rows = calendar_from_ics(BEA_ICS, "BEA", "u")
    assert len(rows) == 5
    by = {r["title"][:12]: r for r in rows}
    pce = by["Personal Inc"]  # all-day -> 08:30 ET -> 22:30 KST
    assert (pce["date_kst"], pce["time_kst"]) == ("2026-11-20", "22:30")
    assert by["GDP (Second "]["time_kst"] == "22:30"  # quoted TZID + folded line
    assert by["GDP by State"]["time_kst"] == "22:30"  # lower-case keys, UTC
    assert by["Gross Domest"]["date_kst"] == "2026-11-18"  # no seconds, X-WR-TIMEZONE default
    week = upcoming(rows, NOW, 7)
    assert [r["date_kst"] for r in week] == ["2026-11-18", "2026-11-19", "2026-11-20"]
    assert [r["date_kst"] for r in later(rows, NOW)] == ["2026-11-25"]  # 2025 entry is in the past


def test_upcoming_accepts_naive_and_bad_rows():
    cal = [{"at_utc": "2026-11-17T13:30", "date_kst": "2026-11-17"},  # naive -> treated as UTC
           {"at_utc": "garbage"}, {"date_kst": "x"},
           {"at_utc": "2026-11-17T13:30+00:00", "date_kst": "2026-11-17b"}]
    assert [r["date_kst"] for r in upcoming(cal, NOW, 7)] == ["2026-11-17", "2026-11-17b"]
    assert upcoming(cal, NOW.replace(tzinfo=None), 7)  # naive "now" works too


def test_fomc_quiet_week_goes_to_later_and_summary_explains():
    meetings = parse_fomc_page('<h4>2026 FOMC Meetings</h4><div class="fomc-meeting__month"><strong>December'
                               '</strong></div><div class="fomc-meeting__date">8-9</div>')
    cal = fomc_events(meetings, "Fed")
    assert upcoming(cal, NOW, 7) == [] and later(cal, NOW)[0]["date_kst"] == "2026-12-10"
    s = calendar_summary(cal, NOW)["Fed"]
    assert "7일 안 0" in s and "2026-12-10 04:00" in s


def test_brief_gets_calendar_later_and_crypto_prices():
    cal = calendar_from_ics(BEA_ICS, "BEA", "u")
    markets = {"btc": {"label": "BTC(바이낸스)", "name_ko": "BTC 현재가(바이낸스, USDT)", "value": 95120.5,
                       "unit": "$", "change_pct": -1.23, "change_basis": "24시간 변화", "source": "Binance",
                       "url": "b", "as_of": "2026-11-16T00:00:00+00:00"}}
    payload, refs = build_inputs([], markets, cal, NOW)
    assert [c["ref"] for c in payload["calendar"]] == ["C1", "C2", "C3"]
    assert payload["calendar_later"][0]["ref"] == "L1" and "L1" in refs
    btc = payload["markets"][0]
    assert btc["name"] == "BTC(바이낸스)" and btc["change_pct"] == -1.23 and "change_pp" not in btc


def test_bls_gets_contact_headers_only_when_configured():
    seen = []

    class S:
        headers = {}

        def get(self, url, timeout=None, headers=None, **kw):
            seen.append((url, headers))
            return SimpleNamespace(raise_for_status=lambda: None, content=b"ok")

    Fetcher(session=S(), contact="me@example.org").text("https://www.bls.gov/schedule/news_release/bls.ics",
                                                        accept="text/calendar, */*")
    url, h = seen[-1]
    assert "contact: me@example.org" in h["User-Agent"] and h["From"] == "me@example.org"
    assert h["Accept"].startswith("text/calendar")
    Fetcher(session=S(), contact="me@example.org").get("https://www.coindesk.com/x")
    assert "From" not in seen[-1][1]  # other sites never get the contact
    Fetcher(session=S()).get("https://www.bls.gov/x")
    assert "From" not in seen[-1][1]


def test_unclassified_items_are_marked_and_never_important():
    row = {"id": "a", "source": "BBC", "group": "macro", "title": "t", "url": "u", "classified": False,
           "importance": None}
    it = feed_item(row)
    assert it["importance_label"] == "분류 전" and it["status"] == "unclassified" and it["important"] is False
    hot = feed_item({**row, "classified": True, "importance": 4})
    assert hot["important"] is True and hot["importance_label"] == "중요"
    low = feed_item({**row, "classified": True, "importance": 3})
    assert low["important"] is False and low["importance_label"] == "보통"


class FakeMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        lines = kw["messages"][0]["content"].split("\n")[1:]
        events = [{"id": json.loads(l)["id"], "coins": [], "event_type": "rates", "direction": "neutral",
                   "importance": 4, "summary_ko": "요약", "why": "이유", "crypto_impact": "영향"} for l in lines]
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"events": events}))],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=500))


def test_relabel_fills_unclassified_without_rewriting_events(tmp_path):
    from news_feed.classifier import Classifier
    rows = [{"id": f"x{i}", "collected_at": "2026-11-15T20:00:00+00:00", "published_at": "2026-11-15T20:00:00+00:00",
             "source": "BBC", "source_kind": "rss", "group": "macro", "title": f"t{i}", "url": f"https://b/{i}",
             "classified": False, "importance": None} for i in range(3)]
    rows.append({**rows[0], "id": "old", "published_at": "2026-11-01T00:00:00+00:00", "url": "https://b/old"})
    original = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    (tmp_path / "news_events.jsonl").write_text(original, encoding="utf-8")
    budget = DailyBudget(tmp_path / "l.json", 2.0, "claude-haiku-5-5")
    clf = Classifier("sk-test", "claude-haiku-5-5", budget)
    fake = FakeMessages()
    clf.client = SimpleNamespace(messages=fake)
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 2.0, "interval": 300}
    col = Collector(cfg, data_dir=tmp_path, classifier=clf, macro=False)
    # ids in the file are not sha1(source|url); relabel must still map results back to them
    out = col.relabel(NOW)
    assert out.startswith("0/3") or out.startswith("3/3")
    assert (tmp_path / "news_events.jsonl").read_text(encoding="utf-8") == original  # never rewritten
    # with real ids (as the collector writes them) the labels land on the rows
    from news_feed.sources import RawItem
    real = [{**r, "id": RawItem(r["source"], "rss", r["title"], r["url"], None).id} for r in rows[:3]]
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in real) + "\n", encoding="utf-8")
    col._relabel_tried.clear()
    assert col.relabel(NOW).startswith("3/3")
    ev = load_events(tmp_path)
    assert all(r["classified"] and r["importance"] == 4 and r["why"] == "이유" for r in ev)
    assert col.relabel(NOW) is None  # nothing left
    f = build_feed(tmp_path, {}, [], {}, NOW)
    assert f["counts"] == {"items": 3, "classified": 3, "unclassified": 0, "important": 3,
                           "body_summaries": 0}


def test_failed_batch_does_not_stop_later_batches(tmp_path):
    from news_feed.classifier import Classifier
    from tests.test_news_feed import items

    class Flaky(FakeMessages):
        def create(self, **kw):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(stop_reason="max_tokens", content=[],
                                       usage=SimpleNamespace(input_tokens=10, output_tokens=10))
            return super().create(**kw)

    clf = Classifier("sk-test", "claude-haiku-5-5", DailyBudget(tmp_path / "l.json", 2.0, "claude-haiku-5-5"))
    clf.client = SimpleNamespace(messages=Flaky())
    got, _, stop = clf.classify(items(20))
    assert len(got) == 12 and "max_tokens" in stop  # batch 1 (8 items) lost, batches 2-3 kept


def test_all_file_io_names_utf8():
    """Windows defaults to cp949: every text read/write in code and tests must name UTF-8."""
    root = Path(__file__).resolve().parent.parent
    bad = []
    calls = re.compile(r"\.read_text\(|\.write_text\(|(?<![\w.])open\(|\.open\(")
    for p in list((root / "news_feed").glob("*.py")) + list((root / "tests").glob("*.py")) + [root / "run.py"]:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#")[0]
            code = code.replace("os.open(", "os_open(")  # byte-level file descriptors, no text encoding
            if calls.search(code) and "encoding" not in code and '"rb"' not in code and "calls.search" not in code:
                bad.append(f"{p.name}:{n}: {line.strip()}")
    assert not bad, "\n".join(bad)
