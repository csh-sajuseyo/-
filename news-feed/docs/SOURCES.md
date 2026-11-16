# Sources and why they were chosen

Rule: free, no login, no API key, published for automated readers (RSS / iCalendar / CSV download).
No paid sources, no paywalled sources, no sites whose terms forbid scraping. Each source is fetched
independently; one failing never stops the others (`run.py --check-sources` shows the state).

## News and notices (every 5 minutes)

| name | URL | why |
|---|---|---|
| CoinDesk, Cointelegraph, Decrypt | official RSS | unchanged from v0.1 |
| Upbit, Binance | public notice APIs used by their own websites | unchanged from v0.1 |
| **Fed** | `federalreserve.gov/feeds/press_all.xml` | official press-release RSS; US-government work, public domain |
| **ECB** | `ecb.europa.eu/rss/press.html` | official press RSS; ECB allows reuse with source attribution |
| **BOJ** | `boj.or.jp/en/rss/whatsnew.xml` | official "What's new" RSS (RSS 1.0); reuse allowed with source |
| **BOK** | `bok.or.kr/portal/bbs/B0000338/news.rss?menuNo=200761` | 한국은행 보도자료 RSS (official) |
| **BLS** | `bls.gov/feed/bls_latest.rss` | official release RSS (CPI, jobs, PPI…); public domain. BLS blocks anonymous bots: if it returns 403, set `NEWS_FEED_CONTACT` in `.env` |
| **BEA** | `apps.bea.gov/rss/rss.xml` | official release RSS (GDP, PCE); public domain |
| **Yonhap 경제** | `yna.co.kr/rss/economy.xml` | 국문. 연합뉴스가 공개 RSS로 제공; headline + link only, personal use, always link back |
| **CNBC Economy** | `cnbc.com/id/20910258/device/rss/rss.html` | public RSS; headline + link, personal non-commercial use |
| **BBC Business** | `feeds.bbci.co.uk/news/business/rss.xml` | public RSS; BBC allows RSS use with link back |

Not used: Reuters (no public RSS any more), Bloomberg/WSJ/FT (paywall), Investing.com (terms forbid scraping).

We store only the headline, a short description (≤ 240 chars) and the link — never the full article.

## Economic calendar (every 6 hours)

| what | source | why |
|---|---|---|
| CPI, jobs report, PPI, JOLTS, ECI | BLS release schedule, official iCalendar `bls.gov/schedule/news_release/bls.ics` | the publisher's own schedule, machine-readable |
| GDP, PCE | BEA release schedule iCalendar `bea.gov/news/schedule/ics/online-calendar-subscription.ics` | same |
| FOMC | `federalreserve.gov/monetarypolicy/fomccalendars.htm` (official page, public domain) | the Fed publishes no feed for meeting dates; the 2026 dates are bundled as a fallback |

## Market indicators (every 15 minutes)

| indicator | primary (Stooq) | fallback (FRED, official daily) |
|---|---|---|
| 달러지수 | `dx.f` (DXY futures) | `DTWEXBGS` broad dollar index — **different index**, marked in `basis` |
| 미 국채 10년 | `10usy.b` | `DGS10` |
| S&P500 선물 | `es.f` | `SP500` (cash index, previous close) |
| 나스닥 선물 | `nq.f` | `NASDAQCOM` (composite, previous close) |
| 금 | `gc.f` | — (FRED has no free gold series) |
| WTI | `cl.f` | `DCOILWTICO` |
| 원/달러 | `usdkrw` | `DEXKOUS` |

Why: **Stooq** gives delayed quotes, futures included, as a plain CSV with no key; it is the only
key-less source with futures that does not forbid automated download in its terms (personal use).
It is an unofficial site, so every value also has an official fallback where one exists:
**FRED** (Federal Reserve Bank of St. Louis) — official, very stable, key-less CSV, but daily and a day late.
Not used: Yahoo Finance (terms forbid automated access), Investing.com (forbids scraping),
paid/keyed APIs (Alpha Vantage, Twelve Data…).
