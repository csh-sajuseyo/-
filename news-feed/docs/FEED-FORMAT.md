# data/feed.json — format (version 2)

The News tab of the chart helper reads this one file. It is **read-only** for the reader:
only the news-feed collector writes it.

- Written after every poll (default every 5 minutes) and right after each briefing.
- Written **atomically**: the collector writes `feed.json.tmp` and then renames it over `feed.json`,
  so a reader never sees half a file. Readers should simply re-read the whole file; never write to it.
- UTF-8 JSON. Times are ISO-8601. `*_kst` fields are already in Korea time.
- `version` changes only on breaking changes. New fields may be added at any time; ignore unknown fields.

## Top level

| field | type | meaning |
|---|---|---|
| `version` | int | `2` (v2: real indicator names, BTC/XLM prices, `calendar_later`, `counts`, 「분류 전」 fields) |
| `generated_at` | string (UTC ISO) | when this file was written |
| `brief` | object \| null | the latest world-economy briefing (see below); `null` until the first one exists |
| `markets` | object | `line_ko` (one-line Korean summary, may be `""`) and `items` (by key, see below) |
| `calendar_7d` | array | scheduled releases/meetings in the next 7 days, oldest first (often empty in a quiet week) |
| `calendar_later` | array | up to 5 events after those 7 days (within 45 days), same fields — show 「다음 일정」 when `calendar_7d` is empty |
| `counts` | object | `items`, `classified`, `unclassified`, `important`, `body_summaries` (numbers among `items`) |
| `summary_status`, `ai_plus`, `weekly` | | see below |
| `items` | array | the newest **200** news / notices, newest first |
| `sources` | object | per-source status of the last poll: `"ok:25"`, `"error:..."`, `"fallback:..."` |

## `items[]` — one news item or exchange notice

| field | type | meaning |
|---|---|---|
| `id` | string | stable id (16 hex) |
| `published_at` | string \| null | UTC ISO; collection time if the source gives none |
| `source` | string | `CoinDesk`, `Cointelegraph`, `Decrypt`, `Upbit`, `Binance`, `Fed`, `ECB`, `BOJ`, `BOK`, `BLS`, `BEA`, `Yonhap`, `CNBC`, `BBC` |
| `topic` | string | main filter: `crypto` (코인) / `macro` (세계경제) / `exchange` (거래소 공지) |
| `title` | string | original headline (original language) |
| `url` | string | original article / notice link |
| `summary_ko` | string \| null | Korean one-line summary (AI) |
| `why` | string \| null | Korean: why it matters (AI); `""` for noise |
| `crypto_impact` | string \| null | Korean: effect on crypto (AI) |
| `event_type` | string \| null | `hack`, `withdrawal_halt`, `listing`, `delisting`, `regulation`, `rates`, `inflation`, `jobs`, `fx`, `geopolitics`, `equities`, `macro`, `other` |
| `direction` | string \| null | `bullish` / `bearish` / `neutral` |
| `importance` | int 1–5 \| null | 5 = market-moving now … 1 = noise; `null` when not classified yet |
| `importance_label` | string | `매우 중요`(5) `중요`(4) `보통`(3) `낮음`(2) `참고`(1), or **`분류 전`** when not classified |
| `important` | bool | `true` only when classified and `importance >= 4` — the 「중요한 것만」 filter |
| `status` | string | `classified` / `unclassified` |
| `coins` | string[] | tickers the item is about |
| `my_coins` | string[] | the subset of `coins` in BTC, XLM (for the 「내 코인」 filter) |
| `body_summary` | object \| null | summary of the article itself (below); `null` until made. Only for important items (importance ≥ 3) and items about BTC/XLM |
| `classified` | bool | `false` = AI fields are `null` (no key yet, an API error, or the daily cap). The collector retries items from the last 36 h, so an item can turn classified on a later write |

Filters in the tab map directly: 전체 = all; 코인 = `topic=="crypto"`; 세계경제 = `topic=="macro"`;
거래소 공지 = `topic=="exchange"`; 내 코인 = `my_coins` not empty; **중요한 것만 = `important == true`**
(unclassified items are always excluded; show them with a grey 「분류 전」 tag in the normal list).

## `items[].body_summary`

Fields are only ever added (feed `version` stays 2). `format: 2` marks the long form (2026-11-17);
older summaries have only `points`, `numbers`, `basis`, `read_at`.

| field | meaning |
|---|---|
| `lead` | 3 lines 「무슨 일 · 왜 · 그래서」 (each ≤ 60 chars); 1–2 lines when `basis` is `제목만` |
| `body` | 6–10 Korean sentences in 2–3 paragraphs separated by a blank line (`"\n\n"`) — enough to skip the article; 1–2 sentences when `제목만` |
| `numbers` | 0–5 key figures with a short label, e.g. `"BTC 64,800달러 (-3.5%)"`; only numbers found in the article (만·억 ↔ million conversions allowed) |
| `impact` | 「코인에 주는 뜻」 one line, always ending in `(추정)` |
| `points` | = `lead` (kept for screens written for format 1) |
| `basis` | `원문` = from the article text (page or full text in the RSS feed); `제목만` = the page could not be read (blocked 429/405, login/paywall, robots.txt, no text) |
| `read_at` | UTC time the summary was made |
| `format` | `2` for the long form |

Rules checked in code: no line shares more than 15 consecutive characters with the article, no quotation marks,
reporter names / captions / ads left out, the article text itself is never stored.
UI: detail screen = `lead` (bold, 3 lines) → `body` paragraphs → `numbers` → `impact` (grey, with 「추정」);
with `basis == "제목만"` add 「원문을 읽지 못해 제목으로 요약」.

Which items get one: every classified crypto / world-economy item (exchange notices only when about BTC/XLM),
except ads, promotions and price-alert / price-prediction pieces; most important first.

## `items[].same_story`

Several articles about the same event are summarised once.

- on the summarised item: `{"count": n, "ids": [other item ids]}` → show 「같은 소식 n건」
- on the other items: `{"count": n, "of": "<summarised item id>"}`; their `body_summary` is the summarised item's
  (so every item stays readable). `null` when the article stands alone.

## `summary_status`, `ai_plus`, `weekly`

| field | meaning |
|---|---|
| `summary_status` | `ok`; `요약 일부 멈춤(상한 근접: 중요도 N 이상·내 코인만)` near the daily summary cap; `요약 멈춤(오늘 상한)` (resumes 00:00 KST); `요약 멈춤(AI+ 월 한도)`; `off` (AI+ off or no key) |
| `ai_plus` | `{"on": bool, "month_spent_usd": x, "monthly_limit_usd": y}` (this project's AI+ month, KST) |
| `weekly` | latest weekly digest or `null`: `{week: "2026-W47", sunday, created_at, model, lines: [5 × {text, refs}], next_week: [≤3 × {date_kst, time_kst, text, refs}], sources: [{ref, title, url, source}], unverified_numbers, cost_usd, batch: true}` — also `data/weekly/YYYY-Www.md` |

## `markets.items.<key>`

Keys, in display order: `btc`, `xlm` (Binance, live), `usd_broad` 달러(무역가중 광의지수), `us10y` 미 국채 10년 금리,
`sp500` S&P500 지수, `nasdaq` 나스닥 종합지수, `wti` WTI 유가(현물) (FRED, daily), `usdkrw` 원/달러 (ECB, daily).
A key is missing if its source failed and there is no earlier value. There are no futures, no DXY and no gold.

| field | meaning |
|---|---|
| `name_ko` | the real series name, e.g. `나스닥 종합지수`, `BTC 현재가(바이낸스, USDT)` |
| `label` | name + the date it refers to, e.g. `나스닥 종합지수(11/14 종가)` — use this when space allows |
| `short_ko` | short name for the one-line strip: `BTC`, `달러(무역가중)`, `미10년`, `나스닥` … |
| `unit` | `""`, `"%"`, `"$"`, `"원"` |
| `value` | latest value |
| `change_pct` | % change; for BTC/XLM the 24h change, otherwise vs the previous published value |
| `change_pp` | yields only: change in percentage points |
| `change_basis` | `24시간 변화`, `직전 발표값 대비`, `전 영업일 대비` |
| `as_of` | the date the value refers to (FRED/ECB, `YYYY-MM-DD`) or the time (Binance, UTC ISO) |
| `source`, `series`, `url` | `Binance` / `FRED` (+ series id) / `ECB` |

`markets.line_ko` example: `BTC 95,120(-1.2%) · XLM 0.2841(+2.1%) · 달러(무역가중) 121.2(+0.4%, 11/14) · 미10년 4.41%(+0.03%p, 11/14) · …`

## `calendar_7d[]`

| field | meaning |
|---|---|
| `at_utc` | UTC time |
| `date_kst`, `time_kst` | Korea date / time (`"2026-12-10"`, `"22:30"`) |
| `name_ko` | e.g. `미국 소비자물가(CPI)`, `FOMC 금리 결정(성명 발표)` |
| `title` | original title |
| `kind` | `inflation`, `jobs`, `growth`, `rates` |
| `importance` | 3–5 |
| `source`, `url` | `BLS`, `BEA`, `Fed`, or `Fed (bundled 2026 list)` when the Fed page could not be read |

## `brief`

Also appended to `data/briefs.jsonl` (one per line, never rewritten).

| field | meaning |
|---|---|
| `id`, `slot` | `"2026-11-16 08:20"` (scheduled) or `"... manual"` |
| `created_at`, `model`, `window_hours` | UTC; `claude-sonnet-5-5` (or the fallback model that served it); `12` |
| `situation[]` | exactly 3 × `{text, refs}` — 「지금 세계경제 상황 3줄」 |
| `risks[]` | `{text, refs}` |
| `next_7d[]` | `{date_kst, time_kst, text, refs}` (empty in a quiet week) |
| `later[]` | up to 3 important events after the 7 days, same shape (refs `L*`) |
| `coin_impact[]` | `{coin: "BTC"\|"XLM", direction: bullish/bearish/neutral/mixed, text, refs}` |
| `confidence` | `{level: low/medium/high, reason}` |
| `sources[]` | `{ref, title, url, source}` for every ref used — `N*` news, `M:*` market, `C*` calendar, `L*` later calendar |
| `unverified_numbers` | numbers in the text not found in the input; normally `[]`. Show a warning if not empty |
| `inputs` | counts of news / markets / calendar / calendar_later rows the briefing was given |
| `cost_usd` | cost of this briefing |

## Other files in data/ (not for the UI)

- `news_events.jsonl` — every collected item, append-only, never rewritten.
- `labels.jsonl` — AI labels added later for items that were stored unclassified; merged on read.
- `summaries.jsonl` — body summaries (`{id, body_summary}`) and same-story links (`{id, same_as}`), append-only; merged on read.
- `weekly/` — `YYYY-Www.json`, `YYYY-Www.md`, `state.json` (pending batch).
