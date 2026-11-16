"""Market indicators and the economic calendar (all key-less, official or feed-style sources).

Markets
  primary  : Stooq quote CSV  (https://stooq.com/q/l/) — free, no key, delayed quotes incl. futures
  fallback : FRED graph CSV   (https://fred.stlouisfed.org/graph/fredgraph.csv) — St. Louis Fed, official, daily
  Each value carries `source` and `basis`, because the FRED fallback is not always the same instrument
  (e.g. broad dollar index instead of DXY, NASDAQ Composite instead of NQ futures).
  Yahoo Finance and Investing.com are NOT used: their terms forbid automated access / scraping.

Calendar
  BLS release schedule (official iCalendar)   — CPI, jobs report, PPI, JOLTS, ECI
  BEA release schedule (official iCalendar)   — GDP, PCE (Personal Income and Outlays)
  FOMC meeting dates (federalreserve.gov official calendar page, US-government public domain),
  with the published 2026 dates bundled as a fallback.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .sources import Fetcher

KST = timezone(timedelta(hours=9))
ET_TZ = ZoneInfo("America/New_York")

# key, Korean label, unit, stooq symbol, (fred series, basis of the fallback)
INDICATORS: tuple[tuple[str, str, str, str | None, tuple[str, str] | None], ...] = (
    ("dxy", "달러지수", "", "dx.f", ("DTWEXBGS", "FRED 광의 달러지수(DXY와 다른 지수)")),
    ("us10y", "미 국채 10년", "%", "10usy.b", ("DGS10", "FRED 10년물 일별")),
    ("spx_fut", "S&P500 선물", "", "es.f", ("SP500", "FRED S&P500 현물 일별 종가")),
    ("ndx_fut", "나스닥 선물", "", "nq.f", ("NASDAQCOM", "FRED 나스닥 종합 일별 종가")),
    ("gold", "금", "$", "gc.f", None),
    ("wti", "WTI", "$", "cl.f", ("DCOILWTICO", "FRED WTI 현물 일별")),
    ("usdkrw", "원/달러", "원", "usdkrw", ("DEXKOUS", "FRED 원/달러 일별(H.10)")),
)
STOOQ_URL = "https://stooq.com/q/l/"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _num(v: str | None) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
        return x if x == x else None  # NaN
    except (TypeError, ValueError):
        return None


def stooq_quote(f: Fetcher, symbol: str) -> dict | None:
    r = f.get(STOOQ_URL, params={"s": symbol, "f": "sd2t2ohlc", "h": "", "e": "csv"})
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows:
        return None
    row = rows[0]
    close = _num(row.get("Close"))
    if close is None:  # "N/D" = no data for this symbol
        return None
    d, t = row.get("Date", ""), row.get("Time", "")
    return {"value": close, "open": _num(row.get("Open")), "as_of": f"{d} {t}".strip(),
            "source": "Stooq", "basis": "지연 시세", "url": f"https://stooq.com/q/?s={symbol}"}


def fred_latest(f: Fetcher, series: str, basis: str) -> dict | None:
    start = (date.today() - timedelta(days=21)).isoformat()
    r = f.get(FRED_URL, params={"id": series, "cosd": start})
    obs = [(row[0], _num(row[1])) for row in csv.reader(io.StringIO(r.text)) if len(row) >= 2]
    obs = [(d, v) for d, v in obs[1:] if v is not None]
    if not obs:
        return None
    (d, v) = obs[-1]
    prev = obs[-2][1] if len(obs) >= 2 else None
    return {"value": v, "prev": prev, "as_of": d, "source": "FRED", "basis": basis,
            "url": f"https://fred.stlouisfed.org/series/{series}"}


def _history_ref(history: list[dict], key: str, now: datetime) -> float | None:
    """Value about 24h ago from our own snapshots (for Stooq, which gives no previous close)."""
    best = None
    for snap in history:
        try:
            at = datetime.fromisoformat(snap["at"])
        except (KeyError, ValueError):
            continue
        age = now - at
        if timedelta(hours=20) <= age <= timedelta(hours=40):
            v = (snap.get("values") or {}).get(key)
            if v is not None:
                best = v  # history is chronological; keep the newest qualifying one
    return best


def fetch_markets(f: Fetcher, history: list[dict] | None = None, now: datetime | None = None
                  ) -> tuple[dict[str, Any], dict[str, str]]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {}
    status: dict[str, str] = {}
    for key, label, unit, sym, fred in INDICATORS:
        q, errs = None, []
        if sym:
            try:
                q = stooq_quote(f, sym)
                if q:
                    ref = _history_ref(history or [], key, now)
                    q["prev"] = ref
                    q["change_basis"] = "24시간 전 대비" if ref is not None else None
            except Exception as exc:
                errs.append(f"stooq {type(exc).__name__}")
        if q is None and fred:
            try:
                q = fred_latest(f, *fred)
                if q:
                    q["change_basis"] = "전 거래일 대비" if q.get("prev") is not None else None
            except Exception as exc:
                errs.append(f"fred {type(exc).__name__}")
        if q is None:
            status[key] = "error:" + (", ".join(errs) or "no data")
            continue
        prev = q.get("prev")
        if prev:
            q["change_pct"] = round((q["value"] - prev) / prev * 100, 2)
            if unit == "%":  # yields: points, not percent
                q["change_bp"] = round((q["value"] - prev) * 100, 1)
        out[key] = {"label": label, "unit": unit, **q}
        status[key] = f"ok:{q['source']}"
    return out, status


def market_line(markets: dict[str, Any]) -> str:
    """One-line Korean summary for the UI, e.g. '달러지수 104.2 · 미 국채 10년 4.41%(+3bp) · …'."""
    parts = []
    for key, *_ in INDICATORS:
        m = markets.get(key)
        if not m:
            continue
        v = m["value"]
        txt = f"{v:,.2f}" if v < 100 else f"{v:,.1f}" if v < 10000 else f"{v:,.0f}"
        if m["unit"] == "%":
            txt += "%"
            if m.get("change_bp") is not None:
                txt += f"({m['change_bp']:+.0f}bp)"
        elif m.get("change_pct") is not None:
            txt += f"({m['change_pct']:+.1f}%)"
        parts.append(f"{m['label']} {txt}")
    return " · ".join(parts)


# ----------------------------------------------------------------------------- calendar

BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_ICS = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# (keyword in SUMMARY, Korean name, kind, importance)
CAL_KEYWORDS = (
    ("Consumer Price Index", "미국 소비자물가(CPI)", "inflation", 5),
    ("Employment Situation", "미국 고용보고서(비농업 고용)", "jobs", 5),
    ("Producer Price Index", "미국 생산자물가(PPI)", "inflation", 4),
    ("Job Openings and Labor Turnover", "미국 구인건수(JOLTS)", "jobs", 3),
    ("Employment Cost Index", "미국 고용비용지수(ECI)", "jobs", 3),
    ("Gross Domestic Product", "미국 GDP", "growth", 4),
    ("GDP", "미국 GDP", "growth", 4),
    ("Personal Income and Outlays", "미국 개인소득·지출(PCE 물가)", "inflation", 5),
)

# Published on federalreserve.gov (2026 FOMC meetings). Used only if the page cannot be read.
FOMC_2026_FALLBACK = (("2026-01-27", "2026-01-28"), ("2026-03-17", "2026-03-18"), ("2026-04-28", "2026-04-29"),
                      ("2026-06-16", "2026-06-17"), ("2026-07-28", "2026-07-29"), ("2026-09-15", "2026-09-16"),
                      ("2026-10-27", "2026-10-28"), ("2026-12-08", "2026-12-09"))


def _ics_unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def parse_ics(text: str) -> list[dict]:
    events, cur = [], None
    for line in _ics_unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, val = line.split(":", 1)
            name, *params = head.split(";")
            cur[name.upper()] = (val, dict(p.split("=", 1) for p in params if "=" in p))
    return events


def _ics_dt(val: str, params: dict) -> datetime | None:
    val = val.strip()
    try:
        if len(val) == 8:  # VALUE=DATE
            return datetime.strptime(val, "%Y%m%d").replace(hour=8, minute=30, tzinfo=ET_TZ)
        if val.endswith("Z"):
            return datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        dt = datetime.strptime(val[:15], "%Y%m%dT%H%M%S")
        tzid = params.get("TZID", "")
        try:
            tz = ZoneInfo(tzid) if tzid else ET_TZ
        except Exception:
            tz = ET_TZ  # e.g. "US-Eastern"
        return dt.replace(tzinfo=tz)
    except ValueError:
        return None


def calendar_from_ics(text: str, source: str, url: str) -> list[dict]:
    rows = []
    for ev in parse_ics(text):
        summary = ev.get("SUMMARY", ("", {}))[0].replace("\\,", ",").strip()
        hit = next((k for k in CAL_KEYWORDS if k[0].lower() in summary.lower()), None)
        if not hit or "DTSTART" not in ev:
            continue
        dt = _ics_dt(*ev["DTSTART"])
        if dt is None:
            continue
        rows.append(_cal_row(dt, hit[1], summary, hit[2], hit[3], source, url))
    return rows


def _cal_row(dt: datetime, name_ko: str, title: str, kind: str, imp: int, source: str, url: str) -> dict:
    k = dt.astimezone(KST)
    return {"at_utc": dt.astimezone(timezone.utc).isoformat(timespec="minutes"),
            "date_kst": k.date().isoformat(), "time_kst": k.strftime("%H:%M"),
            "name_ko": name_ko, "title": title, "kind": kind, "importance": imp,
            "source": source, "url": url}


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


_MON3 = {k[:3]: v for k, v in _MONTHS.items()}


def parse_fomc_page(html_text: str) -> list[tuple[str, str]]:
    """Return [(first_day, last_day)] ISO dates from the Fed's FOMC calendar page."""
    out = []
    # the page has one panel per year: "<year> FOMC Meetings", then month / date cells
    chunks = re.split(r"(\d{4})\s+FOMC Meetings", html_text)
    for i in range(1, len(chunks) - 1, 2):
        year = int(chunks[i])
        body = chunks[i + 1]
        for m in re.finditer(r"fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z/]+)\s*(?:</strong>)?.*?"
                             r"fomc-meeting__date[^>]*>\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?", body, re.S):
            months = [_MONTHS.get(x.lower()) or _MON3.get(x.lower()[:3]) for x in m.group(1).split("/")]
            if not months[0]:
                continue
            d1 = int(m.group(2))
            d2 = int(m.group(3) or d1)
            m2 = months[-1] if (len(months) > 1 and months[-1]) else months[0]
            try:
                out.append((date(year, months[0], d1).isoformat(), date(year, m2, d2).isoformat()))
            except ValueError:
                continue
    return out


def fomc_events(meetings: list[tuple[str, str]], source: str) -> list[dict]:
    rows = []
    for first, last in meetings:
        d = date.fromisoformat(last)
        dt = datetime(d.year, d.month, d.day, 14, 0, tzinfo=ET_TZ)  # statement 2:00 p.m. ET
        rows.append(_cal_row(dt, "FOMC 금리 결정(성명 발표)", f"FOMC meeting {first}..{last}", "rates", 5,
                             source, FOMC_URL))
    return rows


def fetch_calendar(f: Fetcher) -> tuple[list[dict], dict[str, str]]:
    rows: list[dict] = []
    status: dict[str, str] = {}
    for name, url in (("BLS-schedule", BLS_ICS), ("BEA-schedule", BEA_ICS)):
        try:
            got = calendar_from_ics(f.get(url).text, name.split("-")[0], url)
            rows += got
            status[name] = f"ok:{len(got)}"
        except Exception as exc:
            status[name] = f"error:{type(exc).__name__}: {str(exc)[:100]}"
    try:
        meetings = parse_fomc_page(f.get(FOMC_URL).text)
        if not meetings:
            raise ValueError("no meetings parsed")
        rows += fomc_events(meetings, "Fed")
        status["FOMC-calendar"] = f"ok:{len(meetings)}"
    except Exception as exc:
        rows += fomc_events(list(FOMC_2026_FALLBACK), "Fed (bundled 2026 list)")
        status["FOMC-calendar"] = f"fallback:{type(exc).__name__}"
    # dedup (GDP keyword can match twice), sort
    uniq = {(r["at_utc"], r["name_ko"]): r for r in rows}
    return sorted(uniq.values(), key=lambda r: r["at_utc"]), status


def upcoming(calendar: list[dict], now: datetime, days: int = 7) -> list[dict]:
    end = now + timedelta(days=days)
    return [r for r in calendar if now <= datetime.fromisoformat(r["at_utc"]) <= end]


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
