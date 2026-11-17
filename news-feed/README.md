# news-feed

코인 뉴스 + 거래소 공지 + **세계경제**(중앙은행·미국 경제지표·경제 뉴스·시장 지표·경제 일정)를 모아
Claude Haiku로 분류하고, 하루 2번(08:20, 21:50 KST) Claude Sonnet으로 세계경제 브리핑을 만든다.
차트 도우미 「뉴스」 탭은 `data/feed.json` 하나만 읽으면 된다 (`docs/FEED-FORMAT.md`).

## 버튼 (더블클릭)
| 파일 | 하는 일 |
|---|---|
| `UPDATE_NEWS_FEED.cmd` | GitHub 브랜치에서 새 코드 받기 → 설치 → 커밋 → 소스 점검 → 수집기 재시작 |
| `START_HIDDEN.cmd` / `STOP_HIDDEN.cmd` | 창 없이 수집기 켜기 / 끄기 |
| `REPORT_NEWS_FEED.cmd` | 분류 건수, 하루 비용, 중요도 분포, 중요 뉴스 5개, 최근 브리핑 |
| `CHECK_SOURCES.cmd` | 모든 소스를 한 번씩 받아 보고 OK / 실패 표시 (아무것도 저장 안 함) |
| `BRIEF_NOW.cmd` | 지금 바로 브리핑 1건 만들기 (약 $0.03–0.06) |

**본문 요약**: 분류된 코인·세계경제 기사 전부(광고·가격 알림 제외, 거래소 공지는 BTC·XLM만)를 중요도 높은 순으로, 5분마다 12건씩 원문 페이지를 읽어(robots.txt 준수, 같은 사이트 4초 간격, 실패 시 1번만 재시도, 로그인·유료·차단 사이트 제외, 앞 3,000자; RSS에 본문이 있으면 그것 먼저) Haiku로 읽을거리를 만든다: `lead` 3줄 · `body` 6~10문장 · `numbers` 0~5개 · `impact`(추정). 같은 사건 기사는 한 번만 요약하고 「같은 소식 n건」으로 묶는다. 원문은 저장하지 않는다. 요약 하루 상한 `NEWS_FEED_SUMMARY_USD_CAP`(기본 $0.30) — 상한이 가까우면 중요도 낮은 것부터 멈춘다. 지난 24시간 채우기: `.venv\Scripts\python run.py --summarize-backfill`.

**AI+** (`NEWS_AI_PLUS=on`, 기본): 본문 요약·브리핑·주간 정리. `off`면 분류만 한다. 모든 Claude 호출은 `cost_ledger.json`과 함께 공용 장부 `C:\CodexProjects\_ai\ledger.jsonl`에도 한 줄씩 남고, `_ai\limits.json`의 `"news-feed"` 월 한도(기본 $15)에 닿으면 요약·브리핑·주간 정리만 멈춘다(수집·분류는 계속).

**주간 정리**: 일요일 08:30 KST, Sonnet 5.5 Batch(반값). 지난 7일 중요도 3 이상 기사의 요약으로 「이번 주 5줄 + 다음 주 볼 일정 3개」 → `feed.json`의 `weekly`, `data\weekly\YYYY-Www.md`. PC가 꺼져 있었으면 월요일 08:30까지 만든다.

비용 한도: `.env`의 `NEWS_FEED_DAILY_USD_CAP` (기본 $2.00, 분류 + 브리핑 합계, 00:00 KST 초기화).
소스와 고른 이유: `docs/SOURCES.md`.

---

Public crypto news + exchange notices + world-economy sources → Claude Haiku classification → `data/news_events.jsonl`; twice-daily Sonnet briefing → `data/briefs.jsonl`; UI summary → `data/feed.json`.

Standalone project. It does **not** import or touch the CoinAI / BTC-futures engines in coin-auto-trader.

## Sources (no API key needed) — crypto part; world-economy sources are in docs/SOURCES.md
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
- Fields: `coins`, `event_type` (hack / withdrawal_halt / listing / delisting / regulation / rates / inflation / jobs / fx / geopolitics / equities / macro / other), `direction` (bullish / bearish / neutral), `importance` 1–5, `summary_ko`, `why`, `crypto_impact`
- **Briefing**: `claude-sonnet-5-5`, structured output, at `NEWS_FEED_BRIEF_TIMES` (08:20, 21:50 KST; a slot missed by up to 3 h is made late). Uses only the last 12 h of news, the latest indicators and the next 7 days of the calendar; every line cites refs that map to source links; numbers not found in the input are listed in `unverified_numbers`. Server-side refusal fallback is on (`fallbacks: "default"`).
- **Daily cost cap**: `NEWS_FEED_DAILY_USD_CAP` (default $2.00, classification + briefings share one ledger; classification keeps $0.12 per remaining briefing of the day free; the day resets at 00:00 KST). Before each call it checks whether a worst-case call (max_tokens fully used) would push past the cap. If it would, it stops the call, so the actual spend never exceeds the cap. After the cap is hit, items are still collected and stored with `classified=false`. The ledger is `data/cost_ledger.json`.

## Extract into a standalone repo (Windows, one time)
This folder currently lives on the `claude/wonderful-hawking-86v0bn` branch of **`csh-sajuseyo/-`**. Not in coin-auto-trader.
```powershell
powershell -ExecutionPolicy Bypass -File EXTRACT_STANDALONE.ps1   # -> C:\CodexProjects\news-feed (git init, venv, empty .env)
```
It does not merge or modify any branch on the source repo.

## Background run with the window hidden
```bat
START_HIDDEN.cmd   &rem pythonw, no console window; log: data\collector.log, PID: data\collector.pid
STOP_HIDDEN.cmd
REPORT_NEWS_FEED.cmd
```
If `.env` has no `ANTHROPIC_API_KEY`, AI classification is skipped and it only collects (`classified=false`).

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
- `briefs.jsonl` — world-economy briefings (append-only)
- `feed.json` — read-only summary for the News tab (atomic write) — `docs/FEED-FORMAT.md`
- `summaries.jsonl` — body summaries of important articles (`body_summary`; article text is never stored)
- `labels.jsonl` — AI labels added later for items stored unclassified (merged on read)
- `markets.json`, `markets_history.jsonl`, `calendar.json` — latest indicators (BTC/XLM, FRED, ECB), indicator snapshots, calendar
