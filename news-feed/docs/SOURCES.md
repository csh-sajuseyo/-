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
| **BLS** | `bls.gov/feed/bls_latest.rss` | official release RSS (CPI, jobs, PPI…); public domain. BLS asks scripts to identify themselves: with `NEWS_FEED_CONTACT` in `.env`, requests to bls.gov carry it in `User-Agent` and `From` (no other site gets it) |
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

| key | shown as | source | freshness |
|---|---|---|---|
| `btc`, `xlm` | BTC / XLM 현재가(바이낸스, USDT), 24시간 변화 | Binance public market-data API `/api/v3/ticker/24hr` (no key); hosts tried in order: api.binance.com → data-api.binance.vision → www.binance.com | live |
| `usdkrw` | 원/달러 환율(ECB 기준환율 환산) | ECB euro reference rates XML (`eurofxref-hist-90d.xml`): KRW per USD = (KRW per EUR) ÷ (USD per EUR) | same day, ~16:00 CET |
| ↳ fallback | 원/달러 환율(연준 H.10) | FRED `DEXKOUS` | weekly update |
| `usd_broad` | 달러(무역가중 광의지수) | FRED `DTWEXBGS` | weekly update, daily values |
| `us10y` | 미 국채 10년 금리 | FRED `DGS10` | 1 business day late |
| `sp500` | S&P500 지수 (종가) | FRED `SP500` | 1 business day late |
| `nasdaq` | 나스닥 종합지수 (종가) | FRED `NASDAQCOM` | 1 business day late |
| `wti` | WTI 유가(현물) | FRED `DCOILWTICO` | several days late (EIA weekly) |

Why:
- On the first real run (PC, 2026-11-16) Stooq was blocked, so every value came from FRED while the screen still
  called them 「선물」「달러지수」. Now each value carries its **real** name and the date it refers to, and Stooq is removed.
- No free source that allows automated download gives live index futures, DXY or gold
  (Yahoo Finance and Investing.com forbid it; CME and ICE data are paid). So these are **not shown** rather than shown wrong.
  FRED no longer carries a gold price (the LBMA series were withdrawn), so **gold was removed**.
- **Binance** public market data needs no key and the API is meant for programs; BTC/XLM are what the user holds.
- **ECB** reference rates are official, published as a machine-readable XML for reuse with attribution, and arrive the
  same day (FRED's KRW series is updated weekly). ECB's site was already reachable from the PC.
- **FRED** (Federal Reserve Bank of St. Louis): official, very stable, key-less CSV download intended for reuse.
