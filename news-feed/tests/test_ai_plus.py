"""AI+ shared ledger, monthly limit, NEWS_AI_PLUS switch, weekly digest (fake API responses only)."""
import json
import threading
from datetime import date, datetime, timezone
from types import SimpleNamespace

from news_feed.ailedger import SharedLedger
from news_feed.budget import DailyBudget
from news_feed.collector import MONTHLY_STOPPED, Collector, load_config
from news_feed.feed import build_feed
from news_feed.weekly import Weekly, build_inputs, due_sunday, week_key, weekly_problems
from tests.test_body_summary import FakeHaiku, FakeReader, row
from tests.test_news_feed import FakeFetcher, FakeMessages, items, make_classifier

NOW = datetime(2026, 11, 17, 0, tzinfo=timezone.utc)


def usage(i=1000, o=500, cw=0, cr=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


def test_shared_ledger_line_format_and_cache_tokens(tmp_path):
    led = SharedLedger(tmp_path / "_ai")
    b = DailyBudget(tmp_path / "cost_ledger.json", 2.0, "claude-haiku-5-5")
    b.shared = led
    cost = b.record(usage(1000, 500, cw=2000, cr=4000), "claude-haiku-5-5", "summarize")
    b.record(usage(10_000, 2_000), "claude-sonnet-5-5", "weekly", batch=True)
    lines = [json.loads(l) for l in (tmp_path / "_ai" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    a, w = lines
    assert set(a) >= {"ts", "project", "feature", "model", "in_tok", "out_tok", "usd", "batch"}
    assert a["project"] == "news-feed" and a["feature"] == "summarize" and a["batch"] is False
    assert a["in_tok"] == 1000 + 2000 + 4000 and a["out_tok"] == 500
    assert abs(a["usd"] - cost) < 1e-12 and abs(cost - (1000 * .1 + 500 * .5 + 2000 * .125 + 4000 * .01) / 1e6) < 1e-12
    assert w["batch"] is True and abs(w["usd"] - (10_000 * 2 + 2_000 * 10) / 1e6 / 2) < 1e-12  # half price
    assert json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))  # old ledger still written


def test_shared_ledger_concurrent_appends_and_other_projects(tmp_path):
    led = SharedLedger(tmp_path / "_ai")
    other = SharedLedger(tmp_path / "_ai", project="coin-ai")
    ts = [threading.Thread(target=lambda l=l: [l.append("x", "m", usage(), 0.01) for _ in range(25)])
          for l in (led, other, led, other)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rows = [json.loads(l) for l in (tmp_path / "_ai" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 100  # every line whole
    assert abs(led.month_spent() - 0.5) < 1e-9 and abs(other.month_spent() - 0.5) < 1e-9  # only own project counted


def test_limits_file_created_once_and_never_overwritten(tmp_path):
    led = SharedLedger(tmp_path / "_ai")
    assert led.monthly_limit() == 15.0
    assert json.loads((tmp_path / "_ai" / "limits.json").read_text(encoding="utf-8")) == {"news-feed": {"monthly_usd": 15.0}}
    limits = json.dumps({"coin-ai": 30, "news-feed": {"monthly_usd": 4}})
    (tmp_path / "_ai" / "limits.json").write_text(limits, encoding="utf-8")
    assert led.monthly_limit() == 4.0
    assert json.loads((tmp_path / "_ai" / "limits.json").read_text(encoding="utf-8"))["coin-ai"] == 30
    (tmp_path / "_ai" / "limits.json").write_text('{"news-feed": 2.5}', encoding="utf-8")
    assert led.monthly_limit() == 2.5


def cfg(**kw):
    return {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 2.0, "interval": 300,
            "brief_times": [], **kw}


def test_monthly_limit_stops_summaries_and_briefs_but_not_classification(tmp_path):
    led = SharedLedger(tmp_path / "_ai")
    (tmp_path / "_ai").mkdir()
    (tmp_path / "_ai" / "limits.json").write_text('{"news-feed": {"monthly_usd": 0.001}}', encoding="utf-8")
    led.append("summarize", "claude-haiku-5-5", usage(), 0.001)  # month already at the limit
    (tmp_path / "news_events.jsonl").write_text(json.dumps(row(1, 5)) + "\n", encoding="utf-8")
    budget = DailyBudget(tmp_path / "l.json", 2.0, "claude-haiku-5-5")
    clf_fake = FakeMessages()
    from news_feed.summarize import Summarizer
    s = Summarizer("sk-test", "claude-haiku-5-5", DailyBudget(tmp_path / "cost_ledger.json", 2.0, "claude-haiku-5-5"),
                   0.30, shared=led)
    hk = FakeHaiku()
    s.client = SimpleNamespace(messages=hk)
    briefer = SimpleNamespace(done_slots=lambda: set(), make=lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    col = Collector(cfg(), data_dir=tmp_path, fetcher=FakeFetcher(items(3, "m")),
                    classifier=make_classifier(budget, clf_fake), summarizer=s, reader=FakeReader(set()),
                    briefer=briefer, macro=False, shared=led)
    out = col.run_once()
    assert out["classified"] == 3 and clf_fake.calls == 1  # classification keeps going
    assert out["summarize"]["status"] == MONTHLY_STOPPED and hk.calls == 0
    assert col.maybe_brief(NOW, force_slot="x") == "skipped: AI+ monthly limit"
    f = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    assert f["summary_status"] == MONTHLY_STOPPED
    assert f["ai_plus"] == {"on": True, "month_spent_usd": 0.001, "monthly_limit_usd": 0.001}


def test_ai_plus_off_means_classification_only(tmp_path):
    env = tmp_path / ".env"
    text = "ANTHROPIC_API_KEY=sk-test-not-real\nNEWS_AI_PLUS=off\nNEWS_AI_SHARED_DIR=" + str(tmp_path / "_ai") + "\n"
    env.write_text(text, encoding="utf-8")
    c = load_config(env)
    assert c["ai_plus"] is False and c["summary_cap"] == 0.30
    col = Collector({**c, "brief_times": ["08:20"]}, data_dir=tmp_path, fetcher=FakeFetcher([]), macro=False)
    assert col.classifier is not None
    assert col.summarizer is None and col.briefer is None and col.weekly is None and col.reader is None
    out = col.run_once()
    assert out["summarize"] is None and out["brief"] is None and out["weekly"] is None
    f = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    assert f["summary_status"] == "off" and f["ai_plus"]["on"] is False
    on = load_config(tmp_path / "missing.env")
    assert on["ai_plus"] is True and on["shared_dir"].endswith("_ai")


# ---------------------------------------------------------------------------- weekly

def test_due_sunday_window():
    sun = date(2026, 11, 22)
    assert due_sunday(datetime(2026, 11, 21, 23, 30, tzinfo=timezone.utc)) == sun  # Sun 08:30 KST
    assert due_sunday(datetime(2026, 11, 21, 23, 29, tzinfo=timezone.utc)) is None
    assert due_sunday(datetime(2026, 11, 22, 23, 0, tzinfo=timezone.utc)) == sun  # Mon 08:00 KST (grace)
    assert due_sunday(datetime(2026, 11, 22, 23, 31, tzinfo=timezone.utc)) is None
    assert week_key(sun) == "2026-W47"


def week_events():
    ev = []
    for i in range(12):
        r = row(i, 3 + i % 3, hours=24 * (i % 6) + 1)
        r["published_at"] = r["collected_at"] = datetime(2026, 11, 21, 12 - i, tzinfo=timezone.utc).isoformat()
        r["body_summary"] = {"lead": [f"요약 {i} 첫 줄", "둘째 줄", "셋째 줄"], "numbers": ["BTC 64,800달러"]}
        ev.append(r)
    ev.append({**row(99, 2), "published_at": "2026-11-21T00:00:00+00:00"})  # importance 2: excluded
    ev.append({**row(98, 5), "published_at": "2026-11-01T00:00:00+00:00"})  # older than 7 days: excluded
    ev.append({**row(97, 5), "published_at": "2026-11-21T00:00:00+00:00", "same_as": "r1"})  # duplicate: excluded
    return ev


CAL = [{"at_utc": "2026-11-25T13:30+00:00", "date_kst": "2026-11-25", "time_kst": "22:30", "name_ko": "미국 GDP",
        "importance": 4, "source": "BEA", "url": "b"},
       {"at_utc": "2026-11-20T13:30+00:00", "date_kst": "2026-11-20", "time_kst": "22:30", "name_ko": "지난 일정",
        "importance": 4, "source": "BEA", "url": "b"}]


def test_weekly_inputs():
    payload, refs, original = build_inputs(week_events(), CAL, date(2026, 11, 22))
    assert payload["week"] == "2026-W47" and len(payload["items"]) == 12
    assert payload["items"][0]["importance"] == 5 and "summary" in payload["items"][0]
    assert [c["date_kst"] for c in payload["calendar"]] == ["2026-11-25"]
    assert "W1" in refs and "C1" in refs and "Fed holds rates" in original or original


GOOD = {"lines": [{"text": f"이번 주 흐름 {i}번째 정리 문장", "refs": ["W1", "W99"]} for i in range(5)],
        "next_week": [{"date_kst": "2026-11-25", "time_kst": "22:30", "text": "미국 GDP 발표, 성장 둔화 여부", "refs": ["C1"]}]}


class FakeBatches:
    def __init__(self, out=GOOD, status_seq=("in_progress", "ended")):
        self.created = []
        self.out = out
        self.seq = list(status_seq)

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id="msgbatch_1")

    def retrieve(self, bid):
        return SimpleNamespace(processing_status=self.seq.pop(0) if len(self.seq) > 1 else self.seq[0])

    def results(self, bid):
        msg = SimpleNamespace(model="claude-sonnet-5-5", stop_reason="end_turn", usage=usage(20_000, 1_500),
                              content=[SimpleNamespace(type="text", text=json.dumps(self.out, ensure_ascii=False))])
        return [SimpleNamespace(custom_id="2026-W47", result=SimpleNamespace(type="succeeded", message=msg))]


def make_weekly(tmp_path, fb):
    led = SharedLedger(tmp_path / "_ai")
    b = DailyBudget(tmp_path / "cost_ledger.json", 2.0, "claude-haiku-5-5")
    b.shared = led
    w = Weekly("sk-test", b, tmp_path, model="claude-sonnet-5-5", shared=led)
    w.client = SimpleNamespace(messages=SimpleNamespace(batches=fb))
    return w, b, led


def test_weekly_batch_flow_files_feed_and_ledger(tmp_path):
    ev = week_events()
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in ev) + "\n", encoding="utf-8")
    fb = FakeBatches()
    w, b, led = make_weekly(tmp_path, fb)
    from news_feed.store import load_events
    sun = datetime(2026, 11, 21, 23, 35, tzinfo=timezone.utc)
    assert w.tick(sun, load_events, CAL) == "submitted:2026-W47"
    req = fb.created[0][0]
    assert req["custom_id"] == "2026-W47" and req["params"]["model"] == "claude-sonnet-5-5"
    assert "thinking" not in req["params"] and req["params"]["output_config"]["format"]["type"] == "json_schema"
    assert w.tick(sun, load_events, CAL) == "waiting:2026-W47"
    out = w.tick(sun, load_events, CAL)
    assert out.startswith("ok:2026-W47")
    rec = json.loads((tmp_path / "weekly" / "2026-W47.json").read_text(encoding="utf-8"))
    assert weekly_problems(rec, "") == [] and len(rec["lines"]) == 5 and len(rec["next_week"]) == 1
    assert rec["lines"][0]["refs"] == ["W1"]  # unknown ref dropped
    assert [s["ref"] for s in rec["sources"]] == ["C1", "W1"] and rec["batch"] is True
    md = (tmp_path / "weekly" / "2026-W47.md").read_text(encoding="utf-8")
    assert md.startswith("# 주간 정리 2026-W47") and "## 다음 주 볼 일정" in md and "2026-11-25 22:30" in md
    assert abs(rec["cost_usd"] - (20_000 * 2 + 1_500 * 10) / 1e6 / 2) < 1e-12
    led_rows = [json.loads(l) for l in (tmp_path / "_ai" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert led_rows[-1]["feature"] == "weekly" and led_rows[-1]["batch"] is True
    assert w.tick(sun, load_events, CAL) is None  # done once per week
    f = build_feed(tmp_path, {}, [], {}, sun)
    assert f["weekly"]["week"] == "2026-W47" and len(f["weekly"]["lines"]) == 5


def test_weekly_format_rules():
    bad = {"lines": [{"text": "“Fed holds rates steady as inflation cools” 라고 함", "refs": []}] * 2, "next_week": []}
    from news_feed.weekly import clean_weekly
    w = clean_weekly(bad, {}, "Fed holds rates steady as inflation cools")
    assert w["lines"] == []  # copied run removed
    assert weekly_problems({"lines": [{"text": "a", "refs": []}] * 4, "next_week": []}, "") == ["lines 4 != 5"]
    q = clean_weekly({"lines": [{"text": "그는 “좋다”고 말함", "refs": []}], "next_week": []}, {}, "")
    assert "“" not in q["lines"][0]["text"]
