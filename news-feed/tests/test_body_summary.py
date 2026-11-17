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
from news_feed.summarize import (Summarizer, clean_summary, copied_run, format_problems, min_importance,
                                 needs_summary, same_story, sentences)

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


BODY_OK = ("중앙은행이 이번 주 회의에서 정책금리를 4.25%로 묶어 두었다. 시장 대부분이 예상한 결과였다. "
           "당국자들은 물가가 아직 목표보다 높다고 설명했다.\n\n"
           "앞으로의 조정은 새로 나오는 지표를 보고 정하겠다고 밝혔다. 발표 뒤 채권 금리는 크게 움직이지 않았다. "
           "다음 회의는 12월 17일로 잡혀 있다.")


def v2_raw(**kw):
    raw = {"lead": ["중앙은행이 정책금리를 그대로 유지함", "물가가 아직 목표보다 높기 때문", "다음 조정은 지표를 보고 결정 예정"],
           "body": BODY_OK, "numbers": ["정책금리 4.25%", "다음 회의 17일"],
           "impact": "금리 인하 기대가 늦춰져 BTC에는 부담"}
    return {**raw, **kw}


def test_v2_format_ok_and_v1_fields_kept():
    s = {**clean_summary(v2_raw(), SRC, "원문"), "basis": "원문"}
    assert format_problems(s, SRC) == []
    assert len(s["lead"]) == 3 and s["points"] == s["lead"]  # v1 field kept for old screens
    assert s["impact"].endswith("(추정)") and s["numbers"] == ["정책금리 4.25%", "다음 회의 17일"]
    assert 6 <= len(sentences(s["body"])) <= 10 and s["body"].count("\n\n") == 1


def test_v2_rules_enforced():
    long_body = BODY_OK + "\n\n" + " ".join(f"추가 설명 문장 {i}번입니다." for i in range(9))
    raw = v2_raw(lead=["“물가가 아직 목표보다 높다”고 당국자들이 밝힘", "The central bank kept its policy rate unchanged",
                       "가" * 80, "넷째 줄"],
                 body=long_body + " The central bank kept its policy rate unchanged at the meeting.",
                 numbers=["정책금리 4.25%", "지어낸 숫자 9.99%", "다음 회의 12월 17일", "하나 1", "둘 2", "셋 3", "넷 4"],
                 impact="코인에 부담 (추정)")
    s = {**clean_summary(raw, SRC + " 1 2 3 4", "원문"), "basis": "원문"}
    assert "The central bank kept" not in " ".join(s["lead"]) + s["body"]  # copied run dropped
    assert all(len(x) <= 60 for x in s["lead"]) and all("“" not in x for x in s["lead"])
    assert len(sentences(s["body"])) == 10  # capped at 10
    assert "지어낸 숫자 9.99%" not in s["numbers"] and len(s["numbers"]) == 5
    assert s["impact"] == "코인에 부담 (추정)"  # not doubled
    assert not [p for p in format_problems(s, SRC) if p.startswith(("copied", "quote", "too long"))]
    t = {**clean_summary(v2_raw(), "Rates held", "제목만"), "basis": "제목만"}
    assert len(t["lead"]) == 2 and len(sentences(t["body"])) <= 2 and format_problems(t, "Rates held") == []
    none = {**clean_summary(v2_raw(impact=""), SRC, "원문"), "basis": "원문"}
    assert none["impact"] == "코인 시장과 직접 관련은 적음 (추정)"
    short = {**clean_summary(v2_raw(body="한 문장뿐이다."), SRC, "원문"), "basis": "원문"}
    assert any(p.startswith("body 1 sentences") for p in format_problems(short, SRC))


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
        assert kw["thinking"] == {"type": "disabled"} and kw["model"] == "claude-haiku-5-5"
        lines = kw["messages"][0]["content"].split("\n")[1:]
        out = []
        for l in lines:
            it = json.loads(l)
            self.inputs.append(it)
            if it["basis"] == "원문":
                out.append({"id": it["id"], **v2_raw()})
            else:
                out.append({"id": it["id"], "lead": ["제목 기준: 금리 동결 소식"], "body": "제목으로 본 내용은 금리 동결이다.",
                            "numbers": [], "impact": "직접 관련 적음"})
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"summaries": out}, ensure_ascii=False))],
                               usage=SimpleNamespace(input_tokens=3000, output_tokens=600))


def make_summarizer(tmp_path, cap=0.30, global_cap=2.0):
    b = DailyBudget(tmp_path / "cost_ledger.json", global_cap, "claude-haiku-5-5")
    s = Summarizer("sk-test", "claude-haiku-5-5", b, cap)
    fake = FakeHaiku()
    s.client = SimpleNamespace(messages=fake)
    return s, fake, b


def test_summarizer_counts_cost_separately_and_stops_at_cap(tmp_path):
    s, fake, b = make_summarizer(tmp_path, cap=0.0045)  # each call costs $0.0006; worst-case pre-check ~$0.0026
    items = [{"id": f"i{k}", "source": "BBC", "title": "Rates held", "text": SRC, "basis": "원문", "importance": 5}
             for k in range(40)]
    done, stop = s.summarize(items)
    assert stop and stop.startswith(SUMMARY_STOPPED)
    assert b.spent_for("summarize") <= 0.0045 and b.spent_for("classify") == 0
    assert 0 < len(done) < 40 and fake.calls == len(done) // 3
    one = next(iter(done.values()))
    assert one["basis"] == "원문" and one["read_at"] and one["numbers"][0] == "정책금리 4.25%" and one["format"] == 2
    assert "claude-haiku-5-5:summarize" in b.day()["by_model"]


TITLES = ["Fed holds rates", "Tesla earnings beat", "XLM network upgrade", "Oil jumps on supply cut",
          "Japan yen weakens", "ECB keeps policy", "Gold demand rises", "Korea exports grow", "Solana outage ends",
          "Coinbase lists token"]


KO = ["연준 금리 동결", "테슬라 실적 호조", "스텔라 네트워크 개편", "감산에 유가 급등", "엔화 약세 지속", "유럽중앙은행 정책 유지",
      "금 수요 증가", "한국 수출 증가", "솔라나 장애 해소", "코인베이스 신규 상장"]


def row(i, imp=4, coins=(), url=None, hours=1):
    t = datetime(2026, 11, 17, 0, tzinfo=timezone.utc).timestamp() - hours * 3600
    iso = datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"id": f"r{i}", "collected_at": iso, "published_at": iso, "source": "BBC", "source_kind": "rss",
            "group": "macro", "title": TITLES[i % len(TITLES)], "summary": "The bank held rates.",
            "url": url or f"https://x/{i}",
            "classified": True, "importance": imp, "coins": list(coins), "summary_ko": KO[i % len(KO)]}


class FakeReader:
    def __init__(self, ok_urls):
        self.ok = ok_urls
        self.urls = []

    def read(self, url):
        self.urls.append(url)
        return (SRC, "ok") if url in self.ok else ("", "blocked:403")


def test_collector_summarizes_all_classified_except_noise_and_old(tmp_path):
    rows = [row(1, 5), row(2, 2), row(3, 1, coins=["XLM"]), row(4, 3), row(5, 1), row(6, 4, hours=30),
            {**row(7, None), "classified": False},
            {**row(8, 2), "title": "Bitcoin price prediction: will BTC hit $100K?"},  # noise
            {**row(9, 4), "group": "exchange", "source_kind": "binance_notice"}]       # notice, not my coin
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s, fake, b = make_summarizer(tmp_path)
    fr = FakeReader({"https://x/1", "https://x/4"})
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 2.0, "interval": 300}
    col = Collector(cfg, data_dir=tmp_path, macro=False, summarizer=s, reader=fr)
    now = datetime(2026, 11, 17, 0, tzinfo=timezone.utc)
    out = col.summarize_pending(now)
    assert fr.urls == ["https://x/1", "https://x/4", "https://x/2", "https://x/3", "https://x/5"]  # importance order
    assert out["summarized"] == 5 and out["read"] == {"ok": 2, "blocked": 3}
    assert out["read_by_source"] == {"BBC": {"ok": 2, "blocked": 3}}
    ev = {r["id"]: r for r in load_events(tmp_path)}
    assert ev["r1"]["body_summary"]["basis"] == "원문" and ev["r3"]["body_summary"]["basis"] == "제목만"
    assert all("body_summary" not in ev[k] for k in ("r6", "r7", "r8", "r9"))
    assert col.summarize_pending(now) is None  # nothing left
    f = build_feed(tmp_path, {}, [], {}, now, summary_status=col.summary_status)
    items = {i["id"]: i for i in f["items"]}
    assert items["r1"]["body_summary"]["lead"] and items["r8"]["body_summary"] is None
    assert f["summary_status"] == "ok" and f["counts"]["body_summaries"] == 5
    for k in ("id", "published_at", "source", "topic", "title", "url", "summary_ko", "why", "crypto_impact",
              "event_type", "direction", "importance", "importance_label", "important", "status", "coins",
              "my_coins", "classified", "body_summary"):
        assert k in items["r1"]  # older fields unchanged
    assert not (tmp_path / "articles").exists() and SRC not in (tmp_path / "summaries.jsonl").read_text(encoding="utf-8")


def test_same_story_summarised_once_and_counted(tmp_path):
    base = {**row(1, 4), "title": "Fed holds rates", "summary_ko": "연준 금리 동결"}
    rows = [base,
            {**row(2, 3), "title": "Fed holds rates steady", "summary_ko": "연준 금리 동결"},
            {**row(3, 2), "title": "Fed holds rates, signals patience", "source": "CNBC"},
            row(4, 3)]
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s, fake, b = make_summarizer(tmp_path)
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 2.0, "interval": 300}
    col = Collector(cfg, data_dir=tmp_path, macro=False, summarizer=s, reader=FakeReader({"https://x/1"}))
    now = datetime(2026, 11, 17, 0, tzinfo=timezone.utc)
    out = col.summarize_pending(now)
    assert out["summarized"] == 2 and out["same_story_linked"] == 2  # r1 + r4 summarised, r2 r3 linked to r1
    ev = {r["id"]: r for r in load_events(tmp_path)}
    assert ev["r1"]["same_story"] == {"count": 3, "ids": ["r2", "r3"]}
    assert ev["r2"]["same_as"] == "r1" and ev["r2"]["body_summary"] == ev["r1"]["body_summary"]
    # a later article on the same story is linked without another model call
    (tmp_path / "news_events.jsonl").open("a", encoding="utf-8").write(
        json.dumps({**row(5, 4), "title": "Fed holds rates again"}) + "\n")
    calls = fake.calls
    out2 = col.summarize_pending(now)
    assert out2["same_story_linked"] == 1 and out2["summarized"] == 0 and fake.calls == calls
    f = build_feed(tmp_path, {}, [], {}, now)
    covered = sum(1 for i in f["items"] if i["body_summary"])
    assert covered == 5  # 100% of the day's articles readable
    assert same_story({"title": "ECB keeps policy"}, {"title": "Tesla earnings beat"}) is False


def test_priority_near_cap_low_importance_stops_first(tmp_path):
    assert [min_importance(x, 0.30) for x in (0.0, 0.17, 0.19, 0.25, 0.26)] == [1, 1, 3, 3, 4]
    s, fake, b = make_summarizer(tmp_path, cap=0.30)
    b.record(SimpleNamespace(input_tokens=0, output_tokens=400_000), "claude-haiku-5-5", "summarize")  # $0.20 spent
    items = [{"id": f"i{k}", "source": "BBC", "title": "t", "text": SRC, "basis": "원문", "importance": imp,
              "my_coin": mine} for k, (imp, mine) in enumerate([(5, False), (2, False), (1, True), (3, False)])]
    done, stop = s.summarize(items)
    assert set(done) == {"i0", "i2", "i3"} and stop is None  # 67% used: importance >= 3 or my coin
    b.record(SimpleNamespace(input_tokens=0, output_tokens=120_000), "claude-haiku-5-5", "summarize")  # ~87%
    done, stop = s.summarize([{**items[3], "id": "j3"}, {**items[2], "id": "j2"}])
    assert set(done) == {"j2"} and stop is None or set(done) == {"j2"} and "일부 멈춤" in (stop or "")


def test_collector_cap_sets_status_for_the_day(tmp_path):
    rows = [row(i, 4) for i in range(10)]
    (tmp_path / "news_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s, fake, b = make_summarizer(tmp_path, cap=0.0029)  # worst case ~$0.0026 per call: room for exactly one
    cfg = {"api_key": "", "base_url": None, "model": "claude-haiku-5-5", "cap": 2.0, "interval": 300}
    col = Collector(cfg, data_dir=tmp_path, macro=False, summarizer=s, reader=FakeReader(set()))
    now = datetime(2026, 11, 17, 0, tzinfo=timezone.utc)
    out = col.summarize_pending(now, limit=8)
    assert out["status"] == SUMMARY_STOPPED and fake.calls == 1
    calls = fake.calls
    assert col.summarize_pending(now)["status"] == SUMMARY_STOPPED and fake.calls == calls  # stays stopped today
    assert build_feed(tmp_path, {}, [], {}, now, summary_status=col.summary_status)["summary_status"] == SUMMARY_STOPPED


def test_needs_summary():
    assert needs_summary({"classified": True, "importance": 1, "title": "x"})
    assert needs_summary({"classified": True, "importance": 1, "coins": ["XLM"], "group": "exchange", "title": "x"})
    assert not needs_summary({"classified": True, "importance": 4, "group": "exchange", "title": "x"})
    assert not needs_summary({"classified": True, "importance": 3, "title": "[광고] 코인 이벤트"})
    assert not needs_summary({"classified": True, "importance": 3, "title": "XRP price analysis: bulls eye $3"})
    assert not needs_summary({"classified": False, "importance": None, "coins": ["XLM"]})
    assert not needs_summary({"classified": True, "importance": 5, "body_summary": {"lead": ["x"]}})
    assert not needs_summary({"classified": True, "importance": 5, "same_as": "r1"})


def test_numbers_allow_unit_conversion_but_not_invented():
    s = clean_summary({"points": ["가"], "numbers": ["예상 연 매출 4,500만 달러", "지어낸 9.99%", "금리 4.25%"]},
                      "revenue of $45 million, rate 4.25%", "원문")
    assert s["numbers"] == ["예상 연 매출 4,500만 달러", "금리 4.25%"]
