# data/feed.json — format (version 1)

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
| `version` | int | `1` |
| `generated_at` | string (UTC ISO) | when this file was written |
| `brief` | object \| null | the latest world-economy briefing (see below); `null` until the first one exists |
| `markets` | object | `line_ko` (one-line Korean summary, may be `""`) and `items` (by key, see below) |
| `calendar_7d` | array | scheduled releases/meetings in the next 7 days, oldest first |
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
| `importance` | int 1–5 \| null | 5 = market-moving now … 1 = noise |
| `coins` | string[] | tickers the item is about |
| `my_coins` | string[] | the subset of `coins` in BTC, XLM (for the 「내 코인」 filter) |
| `classified` | bool | `false` = AI fields are `null` (no key, or the daily cap was reached) |

Filters in the tab map directly: 전체 = all; 코인 = `topic=="crypto"`; 세계경제 = `topic=="macro"`;
거래소 공지 = `topic=="exchange"`; 내 코인 = `my_coins` not empty; 중요도 = `importance >= N`.

## `markets.items.<key>`

Keys: `dxy` 달러지수, `us10y` 미 국채 10년, `spx_fut` S&P500 선물, `ndx_fut` 나스닥 선물, `gold` 금, `wti` WTI, `usdkrw` 원/달러.
A key is missing if every source for it failed and there is no earlier value.

| field | meaning |
|---|---|
| `label`, `unit` | Korean name; unit `""`, `"%"`, `"$"`, `"원"` |
| `value` | latest value |
| `prev`, `change_pct` | reference value and % change (may be missing) |
| `change_bp` | for yields only: change in basis points |
| `change_basis` | what the change is measured against (`"24시간 전 대비"`, `"전 거래일 대비"`) |
| `as_of` | time / date of the value as given by the source |
| `source`, `basis`, `url` | `Stooq` (delayed quotes) or `FRED` (official daily; `basis` says when it is a different instrument) |

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
| `next_7d[]` | `{date_kst, time_kst, text, refs}` |
| `coin_impact[]` | `{coin: "BTC"\|"XLM", direction: bullish/bearish/neutral/mixed, text, refs}` |
| `confidence` | `{level: low/medium/high, reason}` |
| `sources[]` | `{ref, title, url, source}` for every ref used — `N*` news, `M:*` market, `C*` calendar |
| `unverified_numbers` | numbers in the text not found in the input; normally `[]`. Show a warning if not empty |
| `inputs` | counts of news / markets / calendar rows the briefing was given |
| `cost_usd` | cost of this briefing |
