import json
from types import SimpleNamespace

from news_feed.budget import DailyBudget, usage_cost
from news_feed.classifier import Classifier, rule_tag
from news_feed.collector import Collector, load_config
from news_feed.sources import RawItem, parse_feed

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Exchange X hacked for $40M</title><link>https://a/1</link><pubDate>Wed, 11 Nov 2026 01:00:00 GMT</pubDate></item>
<item><title>Old story</title><link>https://a/2</link><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_feed():
    rows = parse_feed(RSS, "T", 1.0)
    assert [r.title for r in rows] == ["Exchange X hacked for $40M", "Old story"]
    assert rows[0].published_at == "2026-11-11T01:00:00+00:00"


def test_rule_tag_word_boundary():
    assert rule_tag("Exchange hacked") == "CRITICAL"
    assert rule_tag("SEC sues firm") == "WATCH"
    assert rule_tag("Bitcoin's second rally") == "NONE"
    assert rule_tag("업비트 거래지원 종료 안내") == "CRITICAL"


def test_usage_cost_haiku():
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    assert abs(usage_cost("claude-haiku-5-5", u) - 0.60) < 1e-9


class FakeMessages:
    def __init__(self, tokens_out=500):
        self.calls = 0
        self.tokens_out = tokens_out

    def create(self, **kw):
        self.calls += 1
        lines = kw["messages"][0]["content"].split("\n")[1:]
        events = [{"id": json.loads(l)["id"], "coins": ["btc"], "event_type": "hack",
                   "direction": "bearish", "importance": 9, "summary_ko": "해킹"} for l in lines]
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"events": events}))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=self.tokens_out,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))


def make_classifier(budget, fake):
    c = Classifier("sk-test", "claude-haiku-5-5", budget)
    c.client = SimpleNamespace(messages=fake)
    return c


class FakeFetcher:
    def __init__(self, items):
        self.items = items

    def collect(self):
        return list(self.items), {"fake": f"ok:{len(self.items)}"}


def items(n, prefix="x"):
    return [RawItem("S", "rss", f"{prefix} title {i}", f"https://s/{prefix}{i}", None) for i in range(n)]


def test_collector_classifies_dedups_and_clamps(tmp_path):
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 1.0, "interval": 300}
    budget = DailyBudget(tmp_path / "cost_ledger.json", 1.0, "claude-haiku-5-5")
    fake = FakeMessages()
    its = items(20)
    col = Collector(cfg, data_dir=tmp_path, fetcher=FakeFetcher(its + its[:3]),
                    classifier=make_classifier(budget, fake))
    s = col.run_once()
    assert s["new"] == 20 and s["classified"] == 20 and fake.calls == 2  # batches of 15
    rows = [json.loads(l) for l in (tmp_path / "news_events.jsonl").read_text().splitlines()]
    assert len(rows) == 20 and rows[0]["importance"] == 5 and rows[0]["coins"] == ["BTC"]
    assert col.run_once()["new"] == 0  # seen ids persist


def test_budget_cap_stops_classification(tmp_path):
    cap = 0.0005  # each fake call costs 1000*0.1/1e6 + 500*0.5/1e6 = $0.00035
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": cap, "interval": 300}
    budget = DailyBudget(tmp_path / "cost_ledger.json", cap, "claude-haiku-5-5")
    fake = FakeMessages()
    col = Collector(cfg, data_dir=tmp_path, fetcher=FakeFetcher(items(5)),
                    classifier=make_classifier(budget, fake))
    s = col.run_once()
    # worst-case pre-check (2048 max output tokens ≈ $0.001) already exceeds the cap -> no call
    assert fake.calls == 0 and s["classified"] == 0 and "daily cap" in s["classify_stopped"]
    rows = [json.loads(l) for l in (tmp_path / "news_events.jsonl").read_text().splitlines()]
    assert len(rows) == 5 and not any(r["classified"] for r in rows)  # still collected


def test_budget_never_exceeded_over_many_calls(tmp_path):
    budget = DailyBudget(tmp_path / "l.json", 0.01, "claude-haiku-5-5")
    fake = FakeMessages(tokens_out=2048)
    c = make_classifier(budget, fake)
    for _ in range(40):
        c.classify(items(15))
    assert budget.spent() <= 0.01 and fake.calls > 0


def test_key_only_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    (tmp_path / ".env").write_text("NEWS_FEED_DAILY_USD_CAP=0.5\n")
    cfg = load_config(tmp_path / ".env")
    assert cfg["api_key"] == "" and cfg["cap"] == 0.5
