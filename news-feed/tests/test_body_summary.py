"""Article reading + body summaries. All pages here are short fake HTML (no real article text in the repo)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from news_feed.article import ArticleReader, Paywalled, extract_text, notice_text
from news_feed.budget import DailyBudget
from news_feed.collector import SUMMARY_STOPPED, Collector
from news_feed.feed import build_feed
from news_feed.store import load_events
from news_feed.summarize import Summarizer, clean_summary, copied_run, format_problems, needs_summary

S1 = "The central bank kept its policy rate unchanged at the meeting held this week in the capital city."
S2 = "Officials said inflation is still above target and that further moves will depend on the coming data."
S3 = "Markets had widely expected the decision, and bond yields moved little after the statement was released."

PAGES = {
    "Yonhap": f"""<html><body><nav><p>뉴스 홈 경제 정치 사회 국제 스포츠 연예 오피니언 전체메뉴 바로가기 로그인 회원가입</p></nav>
<div class="story-news article"><p>(서울=연합뉴스) 홍길동 기자 = 한국은행이 기준금리를 연 2.50%로 동결했다고 17일 밝혔다. 금융통화위원회는 이날 회의에서 만장일치로 결정했다.</p>
<p>한은은 물가 상승률이 목표 수준에 가까워졌지만 가계부채 증가세가 여전히 빠르다고 설명했다. 시장에서는 내년 초 인하 가능성을 점치고 있다.</p>
<figure><figcaption>이창용 한국은행 총재가 기자간담회에서 발언하고 있다. 2026.11.17 [연합뉴스 자료사진]</figcaption></figure>
<p>hong@yna.co.kr</p><p>&lt;저작권자(c) 연합뉴스, 무단 전재-재배포, AI 학습 및 활용 금지&gt;</p></div></body></html>""",
    "CoinDesk": """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle",
"isAccessibleForFree":true,"articleBody":"%s\\n%s\\n%s\\nRead more: another story about something else entirely here."}
</script></head><body><p>Short teaser.</p></body></html>""" % (S1, S2, S3),
    "Cointelegraph": f"""<html><body><div class="ticker"><p>BTC$64,495.00-0.36%ETH$1,824+1.2%XRP$0.51+0.4%SOL$140+2%</p></div>
<article><p>{S1}</p><p>{S2}</p><div class="related-articles"><p>Related: Ten other things you might want to read about markets today.</p></div>
<p>{S3}</p></article><footer><p>© Cointelegraph 2026. All rights reserved and some other footer words here.</p></footer></body></html>""",
    "Decrypt": f"""<html><body><p>NewsPredictPerpsLearnVideosNewsletterPartnerwithusBTC$64,495.00-0.36%ETH$1,824</p>
<main><p>{S1}</p><p>{S2}</p><aside><p>Sign up for our newsletter to get the daily debrief in your inbox every day.</p></aside>
<p>{S3}</p><p class="author-bio">Edited by Someone Name, who writes about technology and markets.</p></main></body></html>""",
    "CNBC": f"""<html><body><header><p>Markets Business Investing Tech Politics Video Watchlist Investing Club PRO</p></header>
<div class="ArticleBody-articleBody"><p>{S1}</p><div class="InlineVideo"><p>WATCH NOW VIDEO 03:12 Interview with an analyst about the decision</p></div>
<p>{S2}</p><p>{S3}</p></div></body></html>""",
    "BBC": f"""<html><body><div data-component="text-block"><p>{S1}</p></div><div data-component="image-block"><figure>
<figcaption>Image caption, The governor spoke to reporters after the meeting in the capital city.</figcaption></figure></div>
<div data-component="text-block"><p>{S2}</p><p>{S3}</p></div></body></html>""",
    "Fed": f"""<html><body><div id="article"><div class="col-xs-12 col-sm-8 col-md-8"><p>{S1}</p><p>{S2}</p><p>{S3}</p>
<p>For media inquiries, e-mail media@frb.gov or call 202-452-2955.</p></div></div></body></html>""",
    "BLS": f"""<html><body><pre>{S1}</pre><p>{S1}</p><p>{S2}</p><p>{S3}</p></body></html>""",
}


@pytest.mark.parametrize("source", sorted(PAGES))
def test_extract_body_per_source(source):
    text = extract_text(PAGES[source])
    assert len(text) <= 3000
    for bad in ("기자 =", "자료사진", "저작권자", "@", "Related:", "Sign up", "Edited by", "WATCH NOW", "BTC$",
                "Image caption", "Read more", "NewsPredict", "Markets Business", "뉴스 홈"):
        assert bad not in text, (source, bad)
    if source == "Yonhap":
        assert text.startswith("한국은행이 기준금리를") and "가계부채" in text
    else:
        assert S1 in text and S2 in text and S3 in text


def test_extract_limit_and_paywall():
    long = "<html><body>" + "".join(f"<p>{S1} {i}</p>" for i in range(100)) + "</body></html>"
    assert len(extract_text(long)) == 3000 or len(extract_text(long)) <= 3000
    paid = ('<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script>'
            f"<p>{S1}</p>")
    with pytest.raises(Paywalled):
        extract_text(paid)


def test_upbit_notice_text():
    t = notice_text("<p>안녕하세요, 업비트입니다.</p><ul><li>대상: 스텔라루멘(XLM)</li><li>일시: 2026-11-18 10:00</li></ul>")
    assert "스텔라루멘(XLM)" in t and "2026-11-18 10:00" in t


class FakeHTTP:
    def __init__(self, pages):
        self.pages = pages  # url -> list of (status, body, headers) answered in order
        self.calls = []
        self.headers = {}

    def get(self, url, timeout=None, allow_redirects=True):
        self.calls.append(url)
        seq = self.pages.get(url) or [(404, "", {})]
        status, body, headers = seq.pop(0) if len(seq) > 1 else seq[0]
        return SimpleNamespace(status_code=status, content=body.encode("utf-8"), url=url, encoding="utf-8",
                               headers={"Content-Type": "text/html; charset=utf-8", **headers})


def reader(pages):
    sleeps, clock = [], [0.0]
    r = ArticleReader(session=FakeHTTP(pages), gap=4.0, sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)),
                      clock=lambda: clock[0])
    return r, sleeps


def test_reader_robots_gap_retry_and_blocks():
    page = PAGES["Cointelegraph"]
    pages = {
        "https://a.com/robots.txt": [(200, "User-agent: *\nDisallow: /private/", {})],
        "https://a.com/news/1": [(503, "", {}), (200, page, {})],     # one retry
        "https://a.com/news/2": [(500, "", {}), (500, "", {}), (200, page, {})],  # gives up after 1 retry
        "https://a.com/news/3": [(403, "", {})],                     # blocked: no retry
        "https://b.com/robots.txt": [(503, "", {})],                 # robots unreachable -> do not fetch
        "https://c.com/robots.txt": [(404, "", {})],                 # no robots -> allowed
        "https://c.com/x": [(200, page, {})],
    }
    r, sleeps = reader(pages)
    text, st = r.read("https://a.com/news/1")
    assert st == "ok" and S1 in text
    assert r.read("https://a.com/private/9") == ("", "robots")
    assert r.read("https://a.com/news/2") == ("", "http:500")
    assert r.read("https://a.com/news/3") == ("", "blocked:403")
    assert r.session.calls.count("https://a.com/news/3") == 1
    assert r.read("https://b.com/y") == ("", "robots")
    assert r.read("https://c.com/x")[1] == "ok"
    assert r.session.calls.count("https://a.com/robots.txt") == 1  # cached
    assert sleeps and all(s <= 4.0 for s in sleeps)  # same-site requests were spaced


def test_reader_login_redirect_and_upbit_api():
    pages = {"https://p.com/robots.txt": [(404, "", {})]}
    r, _ = reader(pages)

    def get(url, timeout=None, allow_redirects=True):
        if url.endswith("robots.txt"):
            return SimpleNamespace(status_code=404, content=b"", url=url, encoding="utf-8", headers={})
        if "upbit" in url:
            body = json.dumps({"success": True, "data": {"body": "<p>안녕하세요, 업비트입니다. 스텔라루멘(XLM) 네트워크 업그레이드로 "
                                                                   "입출금이 일시 중단됩니다.</p><p>일시: 2026-11-18 10:00 ~ 완료 시까지</p>"}})
            return SimpleNamespace(status_code=200, content=body.encode("utf-8"), url=url, encoding="utf-8",
                                   headers={"Content-Type": "application/json"})
        return SimpleNamespace(status_code=200, content=b"<p>x</p>", url="https://p.com/account/login?next=/a",
                               encoding="utf-8", headers={"Content-Type": "text/html"})
    r.session.get = get
    assert r.read("https://p.com/a") == ("", "paywall")
    text, st = r.read("https://upbit.com/service_center/notice?id=5123")
    assert st == "ok" and "스텔라루멘(XLM)" in text


# ---------------------------------------------------------------------------- summarizer

SRC = f"{S1} {S2} {S3} The rate is 4.25% and the next meeting is on 17 December."


def test_clean_summary_format_rules():
    raw = {"points": ["중앙은행이 이번 주 회의에서 정책금리를 그대로 유지함",
                      "“물가가 아직 목표보다 높다”고 당국자들이 밝힘",
                      "The central bank kept its policy rate unchanged",  # copied run -> dropped
                      "시장은 이 결정을 대체로 예상했고 채권 금리 변화도 작았다고 보도되었으며 다음 회의는 12월로 예정되어 있어 시장의 관심이 모인다",
                      "다음 회의는 12월 17일", "a", "b"],
           "numbers": ["정책금리 4.25%", "다음 회의 12월 17일", "지어낸 숫자 9.99%", "넷째 1", "다섯째 2"]}
    s = {**clean_summary(raw, SRC, "원문"), "basis": "원문"}
    assert not any(copied_run(p, SRC) for p in s["points"])
    assert all(len(p) <= 60 for p in s["points"]) and 3 <= len(s["points"]) <= 5
    assert all("“" not in p for p in s["points"])  # quotation marks removed
    assert "지어낸 숫자 9.99%" not in s["numbers"] and len(s["numbers"]) <= 3
    assert format_problems(s, SRC) == []
    t = {**clean_summary({"points": ["한 줄", "두 줄", "세 줄"], "numbers": []}, "title", "제목만"), "basis": "제목만"}
    assert len(t["points"]) == 2 and format_problems(t, "title") == []
    assert format_problems({"basis": "원문", "points": ["하나"], "numbers": []}, SRC) == ["points 1 not in 3-5"]


def test_copied_run_threshold():
    assert copied_run("abcdefghijklmnop", "xx abcdefghijklmnop yy")  # 16 chars shared
    assert not copied_run("abcdefghijklmno", "xx abcdefghijklmno yy")  # exactly 15 is allowed
    assert not copied_run("짧은 요약", SRC)


class FakeHaiku:
    def __init__(self):
        self.calls = 0
        self.inputs = []

    def create(self, **kw):
        self.calls += 1
        lines = kw["messages"][0]["content"].split("\n")[1:]
        out = []
        for l in lines:
            it = json.loads(l)
            self.inputs.append(it)
            pts = (["중앙은행이 정책금리를 4.25%로 묶어 둠", "물가가 목표보다 높아 추가 조정은 지표를 보고 정함",
                    "시장 예상과 같아 채권 금리 반응은 작았음", "다음 회의는 12월 17일"]
                   if it["basis"] == "원문" else ["제목 기준: 금리 동결 소식"])
            out.append({"id": it["id"], "points": pts, "numbers": ["정책금리 4.25%"] if it["basis"] == "원문" else []})
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"summaries": out}, ensure_ascii=False))],
                               usage=SimpleNamespace(input_tokens=3000, output_tokens=600))


def make_summarizer(tmp_path, cap=0.10, global_cap=2.0):
    b = DailyBudget(tmp_path / "cost_ledger.json", global_cap, "claude-haiku-5-5")
    s = Summarizer("sk-test", "claude-haiku-5-5", b, cap)
    fake = FakeHaiku()
    s.client = SimpleNamespace(messages=fake)
    return s, fake, b


def test_summarizer_counts_cost_separately_and_stops_at_cap(tmp_path):
    s, fake, b = make_summarizer(tmp_path, cap=0.0021)  # each call: 3000*0.1/1e6 + 600*0.5/1e6 = $0.0006
    items = [{"id": f"i{k}", "source": "BBC", "title": "Rates held", "text": SRC, "basis": "원문"} for k in range(40)]
    done, stop = s.summarize(items)
    assert stop and stop.startswith(SUMMARY_STOPPED)
    assert b.spent_for("summarize") <= 0.0021 and b.spent_for("classify") == 0
    assert 0 < len(done) < 40 and fake.calls == len(done) // 4
    one = next(iter(done.values()))
    assert one["basis"] == "원문" and one["read_at"] and one["numbers"] == ["정책금리 4.25%"]
    assert "claude-haiku-5-5:summarize" in b.day()["by_model"]


def row(i, imp=4, coins=(), url=None, hours=1):
    t = datetime(2026, 11, 17, 0, tzinfo=timezone.utc).timestamp() - hours * 3600
    iso = datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"id": f"r{i}", "collected_at": iso, "published_at": iso, "source": "BBC", "source_kind": "rss",
            "group": "macro", "title": f"Rates held {i}", "summary": "The bank held rates.", "url": url or f"https://x/{i}",
            "classified": True, "importance": imp, "coins": list(coins), "summary_ko": "금리 동결"}


class FakeReader:
    def __init__(self, ok_urls):
        self.ok = ok_urls
        self.urls = []

    def read(self, url):
        self.urls.append(url)
        return (SRC, "ok") if url in self.ok else ("", "blocked:403")


def test_collector_summarizes_only_important_and_my_coins(tmp_path):
    rows = [row(1, 5), row(2, 2), row(3, 2, coins=["XLM"]), row(4, 3), row(5, 1), row(6, 4, hours=30),
            {**row(7, None), "classified": False}]
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s, fake, b = make_summarizer(tmp_path)
    fr = FakeReader({"https://x/1", "https://x/4"})
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 2.0, "interval": 300}
    col = Collector(cfg, data_dir=tmp_path, macro=False, summarizer=s, reader=fr)
    now = datetime(2026, 11, 17, 0, tzinfo=timezone.utc)
    out = col.summarize_pending(now)
    assert sorted(fr.urls) == ["https://x/1", "https://x/3", "https://x/4"]  # imp>=3 or my coin, last 24h
    assert out["summarized"] == 3 and out["read"] == {"ok": 2, "blocked": 1}
    ev = {r["id"]: r for r in load_events(tmp_path)}
    assert ev["r1"]["body_summary"]["basis"] == "원문" and ev["r3"]["body_summary"]["basis"] == "제목만"
    assert "body_summary" not in ev["r2"]
    assert col.summarize_pending(now) is None  # nothing left
    f = build_feed(tmp_path, {}, [], {}, now, summary_status=col.summary_status)
    items = {i["id"]: i for i in f["items"]}
    assert items["r1"]["body_summary"]["points"] and items["r2"]["body_summary"] is None
    assert f["summary_status"] == "ok" and f["counts"]["body_summaries"] == 3
    # backward compatible: every v2 field still there
    for k in ("id", "published_at", "source", "topic", "title", "url", "summary_ko", "why", "crypto_impact",
              "event_type", "direction", "importance", "importance_label", "important", "status", "coins",
              "my_coins", "classified"):
        assert k in items["r1"]
    assert not (tmp_path / "articles").exists() and SRC not in (tmp_path / "summaries.jsonl").read_text(encoding="utf-8")


def test_collector_cap_sets_status_for_the_day(tmp_path):
    rows = [row(i, 4) for i in range(10)]
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s, fake, b = make_summarizer(tmp_path, cap=0.0016)  # worst case ~$0.00136 per call: room for exactly one
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 2.0, "interval": 300}
    col = Collector(cfg, data_dir=tmp_path, macro=False, summarizer=s, reader=FakeReader(set()))
    now = datetime(2026, 11, 17, 0, tzinfo=timezone.utc)
    out = col.summarize_pending(now, limit=8)
    assert out["status"] == SUMMARY_STOPPED and fake.calls == 1
    calls = fake.calls
    assert col.summarize_pending(now)["status"] == SUMMARY_STOPPED and fake.calls == calls  # stays stopped today
    assert build_feed(tmp_path, {}, [], {}, now, summary_status=col.summary_status)["summary_status"] == SUMMARY_STOPPED


def test_needs_summary():
    assert needs_summary({"classified": True, "importance": 3})
    assert needs_summary({"classified": True, "importance": 1, "coins": ["BTC"]})
    assert not needs_summary({"classified": True, "importance": 2, "coins": ["ETH"]})
    assert not needs_summary({"classified": False, "importance": None, "coins": ["XLM"]})
    assert not needs_summary({"classified": True, "importance": 5, "body_summary": {"points": ["x"]}})


def test_numbers_allow_unit_conversion_but_not_invented():
    s = clean_summary({"points": ["가"], "numbers": ["예상 연 매출 4,500만 달러", "지어낸 9.99%", "금리 4.25%"]},
                      "revenue of $45 million, rate 4.25%", "원문")
    assert s["numbers"] == ["예상 연 매출 4,500만 달러", "금리 4.25%"]
