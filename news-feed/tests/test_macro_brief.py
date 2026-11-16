import json
from datetime import datetime, timezone
from types import SimpleNamespace

from news_feed.brief import Briefer, build_inputs, slot_due, unverified_numbers
from news_feed.budget import DailyBudget
from news_feed.collector import Collector
from news_feed.feed import build_feed, topic_of
from news_feed.macro import (calendar_from_ics, fetch_markets, fomc_events, market_line, parse_fomc_page,
                             upcoming)
from news_feed.sources import RawItem, parse_feed

RDF = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="x"><title>BOJ</title></channel>
<item rdf:about="https://www.boj.or.jp/a"><title>Statement on Monetary Policy</title>
<link>https://www.boj.or.jp/a</link><dc:date>2026-10-30T12:00:00+09:00</dc:date></item>
</rdf:RDF>"""

RSS_DESC = """<?xml version="1.0"?><rss><channel><item><title>Fed holds rates</title>
<link>https://fed/1</link><pubDate>Wed, 28 Oct 2026 18:00:00 GMT</pubDate>
<description>&lt;p&gt;The Committee decided to maintain the target range at 3-1/2 to 3-3/4 percent.&lt;/p&gt;</description>
</item></channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
<link rel="alternate" href="https://x/1"/><updated>2026-11-15T00:00:00Z</updated></entry></feed>"""

ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
DTSTART;TZID=US-Eastern:20261210T083000\r
SUMMARY:Consumer Price Index for November\r
 2026\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20261204\r
SUMMARY:The Employment Situation for November 2026\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART:20261205T133000Z\r
SUMMARY:County Employment and Wages\r
END:VEVENT\r
END:VCALENDAR\r
"""

FOMC_HTML = """<h4><a>2026 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col-xs-5"><strong>October</strong></div>
<div class="fomc-meeting__date col-xs-4">27-28</div>
<div class="fomc-meeting__month col-xs-5"><strong>December</strong></div>
<div class="fomc-meeting__date col-xs-4">8-9*</div>
<h4>2027 FOMC Meetings</h4>
<div class="fomc-meeting__month"><strong>Jan/Feb</strong></div><div class="fomc-meeting__date">31-1</div>"""


def test_parse_rdf_rss_desc_atom():
    r = parse_feed(RDF, "BOJ", 1.0, "macro")
    assert r[0].title == "Statement on Monetary Policy" and r[0].published_at == "2026-10-30T03:00:00+00:00"
    assert r[0].group == "macro"
    d = parse_feed(RSS_DESC, "Fed", 1.0, "macro")[0]
    assert d.summary.startswith("The Committee decided") and "<p>" not in d.summary
    a = parse_feed(ATOM, "A", 1.0)[0]
    assert a.url == "https://x/1"


def test_ics_calendar_filters_and_converts_to_kst():
    rows = calendar_from_ics(ICS, "BLS", "u")
    names = [r["name_ko"] for r in rows]
    assert names == ["미국 소비자물가(CPI)", "미국 고용보고서(비농업 고용)"]
    cpi = rows[0]
    assert cpi["date_kst"] == "2026-12-10" and cpi["time_kst"] == "22:30"  # 08:30 EST = 22:30 KST


def test_fomc_page_parse():
    m = parse_fomc_page(FOMC_HTML)
    assert ("2026-10-27", "2026-10-28") in m and ("2026-12-08", "2026-12-09") in m
    assert ("2027-01-31", "2027-02-01") in m
    ev = fomc_events([("2026-12-08", "2026-12-09")], "Fed")[0]
    assert ev["date_kst"] == "2026-12-10" and ev["time_kst"] == "04:00"


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeHttp:
    """Stooq returns N/D for 'gc.f' and fails for 'dx.f'; FRED serves the fallback."""
    def get(self, url, params=None, **kw):
        if "stooq" in url:
            s = params["s"]
            if s == "dx.f":
                raise IOError("blocked")
            if s == "gc.f":
                return FakeResp("Symbol,Date,Time,Open,High,Low,Close\nGC.F,N/D,N/D,N/D,N/D,N/D,N/D\n")
            return FakeResp(f"Symbol,Date,Time,Open,High,Low,Close\n{s},2026-11-16,09:00:00,100,101,99,100.5\n")
        return FakeResp("observation_date,X\n2026-11-12,120.0\n2026-11-13,.\n2026-11-14,121.2\n")


def test_markets_fallbacks_and_line():
    hist = [{"at": "2026-11-15T09:00:00+00:00", "values": {"us10y": 4.40}}]
    m, st = fetch_markets(FakeHttp(), hist, datetime(2026, 11, 16, 9, tzinfo=timezone.utc))
    assert st["dxy"] == "ok:FRED" and m["dxy"]["prev"] == 120.0 and m["dxy"]["change_pct"] == 1.0
    assert st["gold"].startswith("error")  # no FRED fallback for gold
    assert m["us10y"]["source"] == "Stooq"
    line = market_line(m)
    assert "달러지수 121.2(+1.0%)" in line and "미 국채 10년" in line


def test_slot_due():
    kst = lambda h, mi: datetime(2026, 11, 16, h - 9 if h >= 9 else h + 15, mi, tzinfo=timezone.utc)
    now = datetime(2026, 11, 15, 23, 25, tzinfo=timezone.utc)  # 11-16 08:25 KST
    assert slot_due(now, ["08:20", "21:50"], set()) == "2026-11-16 08:20"
    assert slot_due(now, ["08:20", "21:50"], {"2026-11-16 08:20"}) is None
    late = datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)  # 12:00 KST, > 3h grace
    assert slot_due(late, ["08:20", "21:50"], set()) is None


def test_unverified_numbers():
    brief = {"situation": [{"text": "미 10년물 4.41%, 달러지수 104.2", "refs": []}], "risks": [],
             "next_7d": [{"date_kst": "2026-11-18", "time_kst": "22:30", "text": "CPI", "refs": []}],
             "coin_impact": [{"text": "BTC 95,000달러", "refs": []}], "confidence": {"reason": "", "level": "low"}}
    inp = '{"name": "미 국채 10년", "value": 4.41, "x": "2026-11-18", "t": "22:30", "btc": "95000"}'
    assert unverified_numbers(brief, inp) == ["104.2"]


def ev(i, imp, group="macro", hours_ago=1):
    t = datetime(2026, 11, 16, 0, tzinfo=timezone.utc).timestamp() - hours_ago * 3600
    iso = datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"id": f"e{i}", "published_at": iso, "collected_at": iso, "source": "Fed", "group": group,
            "title": f"title {i}", "url": f"https://u/{i}", "classified": True, "importance": imp,
            "summary_ko": "요약", "event_type": "rates", "coins": []}


class FakeSonnet:
    def __init__(self):
        self.kw = None

    def create(self, **kw):
        self.kw = kw
        out = {"situation": [{"text": "연준 금리 동결", "refs": ["N1"]}] * 4,
               "risks": [{"text": "물가", "refs": ["N1", "C1"]}],
               "next_7d": [{"date_kst": "2026-11-18", "time_kst": "22:30", "text": "CPI", "refs": ["C1"]}],
               "coin_impact": [{"coin": "BTC", "direction": "neutral", "text": "중립", "refs": ["M:dxy"]},
                               {"coin": "XLM", "direction": "neutral", "text": "BTC 따라감", "refs": ["N9"]}],
               "confidence": {"level": "medium", "reason": "근거 보통"}}
        return SimpleNamespace(model="claude-sonnet-5-5", stop_reason="end_turn",
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=json.dumps(out, ensure_ascii=False))],
                               usage=SimpleNamespace(input_tokens=6000, output_tokens=1500))


def test_briefer_make_records_cost_refs_and_fallback(tmp_path):
    budget = DailyBudget(tmp_path / "l.json", 2.0, "claude-haiku-5-5")
    b = Briefer("sk-test", budget, tmp_path)
    fake = FakeSonnet()
    b.client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    now = datetime(2026, 11, 16, 0, tzinfo=timezone.utc)
    events = [ev(1, 5), ev(2, 1), ev(3, 4, hours_ago=20)]
    markets = {"dxy": {"label": "달러지수", "value": 104.2, "unit": "", "source": "Stooq", "url": "s"}}
    cal = [{"at_utc": "2026-11-18T13:30+00:00", "date_kst": "2026-11-18", "time_kst": "22:30",
            "name_ko": "미국 소비자물가(CPI)", "importance": 5, "source": "BLS", "url": "b"}]
    rec = b.make(events, markets, cal, "2026-11-16 08:20", now)
    assert len(rec["situation"]) == 3
    assert [s["ref"] for s in rec["sources"]] == ["C1", "M:dxy", "N1"]  # N9 does not exist -> dropped
    assert abs(rec["cost_usd"] - (6000 * 2 + 1500 * 10) / 1e6) < 1e-9
    assert budget.day()["by_model"]["claude-sonnet-5-5:brief"]["calls"] == 1
    assert fake.kw["extra_body"] == {"fallbacks": "default"} and fake.kw["model"] == "claude-sonnet-5-5"
    sent = json.loads(fake.kw["messages"][0]["content"].split("\n", 1)[1])
    assert [n["ref"] for n in sent["news"]][:1] == ["N1"] and len(sent["calendar"]) == 1
    assert "2026-11-16 08:20" in b.done_slots()


def test_briefer_respects_cap(tmp_path):
    budget = DailyBudget(tmp_path / "l.json", 0.01, "claude-haiku-5-5")
    b = Briefer("sk-test", budget, tmp_path)
    fake = FakeSonnet()
    b.client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    try:
        b.make([ev(1, 5)], {}, [], "s", datetime(2026, 11, 16, tzinfo=timezone.utc))
        assert False, "should refuse"
    except RuntimeError as exc:
        assert "cap" in str(exc)
    assert fake.kw is None


def test_feed_topics_and_atomic_write(tmp_path):
    rows = [ev(1, 5), {**ev(2, 3, "crypto"), "coins": ["XLM"], "event_type": "listing"},
            {**ev(3, 4, "exchange"), "source_kind": "upbit_notice"},
            {"id": "old", "collected_at": "2026-11-10T00:00:00+00:00", "source": "CoinDesk", "source_kind": "rss",
             "title": "t", "url": "u", "classified": True, "event_type": "inflation", "importance": 2}]
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert topic_of(rows[3]) == "macro"  # old rows without 'group' still map by event_type
    f = build_feed(tmp_path, {}, [], {"Fed": "ok:1"}, datetime(2026, 11, 16, tzinfo=timezone.utc))
    assert [i["topic"] for i in f["items"]] == ["macro", "crypto", "exchange", "macro"]
    assert f["items"][1]["my_coins"] == ["XLM"] and f["brief"] is None and f["version"] == 1


def test_classifier_reserve_keeps_room_for_briefs(tmp_path):
    from tests.test_news_feed import FakeFetcher, FakeMessages, items, make_classifier
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 0.242, "interval": 300,
           "brief_times": ["08:20", "21:50"]}
    budget = DailyBudget(tmp_path / "l.json", 0.242, "claude-haiku-5-5")
    fake = FakeMessages()
    col = Collector(cfg, data_dir=tmp_path, fetcher=FakeFetcher(items(3)),
                    classifier=make_classifier(budget, fake), briefer=SimpleNamespace(done_slots=lambda: set()),
                    macro=False)
    col.maybe_brief = lambda now: None
    morning = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)  # 05:00 KST: two briefs left -> $0.24 reserved
    assert col._reserve(morning) == 0.24
    col.classifier.reserve = 0.24
    _, _, stop = col.classifier.classify(items(3))
    assert stop and "reserve" in stop and fake.calls == 0
