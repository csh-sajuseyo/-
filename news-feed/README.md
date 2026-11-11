# news-feed

Public crypto news + exchange notices → Claude Haiku classification → `data/news_events.jsonl`.

Standalone project. It does **not** import or touch the CoinAI / BTC-futures engines in coin-auto-trader.

## Sources (no API key needed)
| Source | Endpoint |
|---|---|
| CoinDesk RSS | `https://www.coindesk.com/arc/outboundfeeds/rss` |
| Cointelegraph RSS | `https://cointelegraph.com/rss` |
| Decrypt RSS | `https://decrypt.co/feed` |
| Upbit notices | `https://api-manager.upbit.com/api/v1/announcements` (public web API) |
| Binance announcements | `https://www.binance.com/bapi/composite/v1/public/cms/article/list/query` — catalogs: listing(48), news(49), delisting(161), maintenance(157) |

If one source fails, the others are still collected. The failure is written to `data/run_log.jsonl`.

## Reused from coin-auto-trader
- The RSS feed list and RSS 2.0/Atom parsing approach: `coinai/news_engine.py` (`DIRECT_CRYPTO_FEEDS`, `_parse_xml`)
- The keyword rules `RISK_WORDS` and the Upbit notice keywords: `news_engine.py`, `announcement_stream.py`. They are stored as `rule_tag` (CRITICAL/WATCH/POSITIVE/NONE) and are kept even when AI classification is stopped.

## AI classification
- Model: `claude-haiku-5-5` ($0.10 / $0.50 per 1M input/output tokens). Up to 15 items per request, structured output (JSON schema).
- Fields: `coins`, `event_type` (hack / withdrawal_halt / listing / delisting / regulation / macro / other), `direction` (bullish / bearish / neutral), `importance` 1–5, `summary_ko`
- **Daily cost cap**: `NEWS_FEED_DAILY_USD_CAP` (default $1.00, the day resets at 00:00 KST). Before each call it checks whether a worst-case call (max_tokens fully used) would push past the cap. If it would, it stops the call, so the actual spend never exceeds the cap. After the cap is hit, items are still collected and stored with `classified=false`. The ledger is `data/cost_ledger.json`.

## Setup (Windows)
```bat
cd C:\CodexProjects\news-feed
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   &rem then put your ANTHROPIC_API_KEY in .env
```
The API key is read **only from `.env`** (git-ignored). System environment variables are ignored, and `ANTHROPIC_BASE_URL` too unless it is set in `.env`.

## Running
```bat
START_NEWS_FEED.cmd                         &rem poll every 5 min indefinitely
.venv\Scripts\python run.py --duration 3600 &rem run for 1 hour, then exit
.venv\Scripts\python run.py --once
.venv\Scripts\python run.py --report        &rem counts and today's cost
.venv\Scripts\python -m pytest -q tests
```
On the first run, only items published within the last 24 hours are stored (`--backfill-hours`). Older ones are just marked as seen.

## Output files (`data/`, git-ignored)
- `news_events.jsonl` — one event per line
- `run_log.jsonl` — per-poll source status, new item count, cost
- `cost_ledger.json` — USD and tokens per day
- `seen_ids.json` — dedup state
