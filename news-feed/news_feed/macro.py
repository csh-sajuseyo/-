"""Market indicators and the economic calendar (all key-less, official or feed-style sources).

Markets (why: docs/SOURCES.md)
  BTC, XLM      : Binance public market-data API (no key) — live price and 24h change
  원/달러        : ECB euro reference rates (official, same day ~16:00 CET), KRW per USD = KRW/EUR ÷ USD/EUR;
                  fallback FRED DEXKOUS (Fed H.10)
  달러·미10년·S&P500·나스닥·WTI : FRED graph CSV (St. Louis Fed, official, daily, 1+ day late)
  Every value carries its real series name and the date it refers to. No futures, no DXY, no gold:
  there is no free source that allows automated download for those (Stooq is blocked; Yahoo and
  Investing.com forbid it; FRED no longer carries a gold price).

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
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .sources import Fetcher

KST = timezone(timedelta(hours=9))
ET_TZ = ZoneInfo("America/New_York")

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ECB_HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
BINANCE_HOSTS = ("https://api.binance.com", "https://data-api.binance.vision", "https://www.binance.com")

# key, FRED series, real name (Korean), short name for the one-line strip, unit, what the date means
FRED_SERIES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("usd_broad", "DTWEXBGS", "달러(무역가중 광의지수)", "달러(무역가중)", "", "기준"),
    ("us10y", "DGS10", "미 국채 10년 금리", "미10년", "%", "기준"),
    ("sp500", "SP500", "S&P500 지수", "S&P500", "", "종가"),
    ("nasdaq", "NASDAQCOM", "나스닥 종합지수", "나스닥", "", "종가"),
    ("wti", "DCOILWTICO", "WTI 유가(현물)", "WTI", "$", "기준"),
)
CRYPTO = (("btc", "BTCUSDT", "BTC"), ("xlm", "XLMUSDT", "XLM"))
ORDER = ("btc", "xlm", "usd_broad", "us10y", "sp500", "nasdaq", "wti", "usdkrw")


def _num(v: str | None) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
        return x if x == x else None  # NaN
    except (TypeError, ValueError):
        return None


def _md(iso_day: str) -> str:
    try:
        d = date.fromisoformat(iso_day[:10])
        return f"{d.month}/{d.day}"
    except ValueError:
        return iso_day


def _with_change(q: dict) -> dict:
    prev = q.get("prev")
    if prev:
        q["change_pct"] = round((q["value"] - prev) / prev * 100, 2)
        if q.get("unit") == "%":  # yields: percentage points
            q["change_pp"] = round(q["value"] - prev, 2)
    return q


def fred_latest(f: Fetcher, key: str, series: str, name: str, short: str, unit: str, kind: str) -> dict | None:
    start = (date.today() - timedelta(days=30)).isoformat()
    text = f.text(FRED_URL, params={"id": series, "cosd": start})
    obs = [(row[0], _num(row[1])) for row in csv.reader(io.StringIO(text)) if len(row) >= 2]
    obs = [(d, v) for d, v in obs[1:] if v is not None]
    if not obs:
        return None
    d, v = obs[-1]
    return _with_change({
        "name_ko": name, "label": f"{name}({_md(d)} {kind})", "short_ko": short, "unit": unit,
        "value": v, "prev": obs[-2][1] if len(obs) >= 2 else None, "as_of": d,
        "change_basis": "직전 발표값 대비" if len(obs) >= 2 else None,
        "source": "FRED", "series": series, "url": f"https://fred.stlouisfed.org/series/{series}"})


def parse_ecb_hist(xml_bytes: bytes) -> list[tuple[str, dict[str, float]]]:
    """[(date, {currency: rate per EUR})], newest first."""
    root = ET.fromstring(xml_bytes)
    days = []
    for el in root.iter():
        t = el.attrib.get("time")
        if t:
            rates = {c.attrib["currency"]: float(c.attrib["rate"]) for c in el
                     if "currency" in c.attrib and "rate" in c.attrib}
            days.append((t, rates))
    return sorted(days, reverse=True)


def ecb_usdkrw(f: Fetcher) -> dict | None:
    days = [(d, r["KRW"] / r["USD"]) for d, r in parse_ecb_hist(f.get(ECB_HIST_URL).content)
            if "KRW" in r and "USD" in r]
    if not days:
        return None
    d, v = days[0]
    return _with_change({
        "name_ko": "원/달러 환율(ECB 기준환율 환산)", "label": f"원/달러(ECB 환산, {_md(d)} 기준)",
        "short_ko": "원/달러", "unit": "원", "value": round(v, 2),
        "prev": round(days[1][1], 2) if len(days) > 1 else None, "as_of": d,
        "change_basis": "전 영업일 대비" if len(days) > 1 else None,
        "source": "ECB", "url": "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"})


def binance_24h(f: Fetcher) -> dict[str, dict]:
    syms = json.dumps([s for _, s, _ in CRYPTO], separators=(",", ":"))
    last: Exception | None = None
    for host in BINANCE_HOSTS:
        try:
            data = f.get(host + "/api/v3/ticker/24hr", params={"symbols": syms}).json()
            break
        except Exception as exc:  # try the public market-data mirror
            last = exc
    else:
        raise last or RuntimeError("binance unavailable")
    by = {d["symbol"]: d for d in data}
    out = {}
    for key, sym, name in CRYPTO:
        d = by.get(sym)
        if not d:
            continue
        at = datetime.fromtimestamp(int(d["closeTime"]) / 1000, timezone.utc)
        out[key] = {"name_ko": f"{name} 현재가(바이낸스, USDT)", "label": f"{name}(바이낸스)", "short_ko": name,
                    "unit": "$", "value": float(d["lastPrice"]), "change_pct": round(float(d["priceChangePercent"]), 2),
                    "change_basis": "24시간 변화", "as_of": at.isoformat(timespec="seconds"),
                    "source": "Binance", "url": f"https://www.binance.com/en/trade/{name}_USDT"}
    return out


def fetch_markets(f: Fetcher, *_ignored) -> tuple[dict[str, Any], dict[str, str]]:
    out: dict[str, Any] = {}
    status: dict[str, str] = {}
    try:
        got = binance_24h(f)
        out.update(got)
        for key, *_ in CRYPTO:
            status[key] = "ok:Binance" if key in got else "error:no data"
    except Exception as exc:
        for key, *_ in CRYPTO:
            status[key] = f"error:binance {type(exc).__name__}"
    for spec in FRED_SERIES:
        try:
            q = fred_latest(f, *spec)
            if q:
                out[spec[0]] = q
            status[spec[0]] = "ok:FRED" if q else "error:no data"
        except Exception as exc:
            status[spec[0]] = f"error:fred {type(exc).__name__}"
    q, errs = None, []
    try:
        q = ecb_usdkrw(f)
    except Exception as exc:
        errs.append(f"ecb {type(exc).__name__}")
    if q is None:
        try:
            q = fred_latest(f, "usdkrw", "DEXKOUS", "원/달러 환율(연준 H.10)", "원/달러", "원", "기준")
        except Exception as exc:
            errs.append(f"fred {type(exc).__name__}")
    if q:
        out["usdkrw"] = q
        status["usdkrw"] = f"ok:{q['source']}"
    else:
        status["usdkrw"] = "error:" + (", ".join(errs) or "no data")
    return {k: out[k] for k in ORDER if k in out}, status


def _fmt(v: float) -> str:
    return f"{v:,.4f}" if v < 1 else f"{v:,.2f}" if v < 100 else f"{v:,.1f}" if v < 10000 else f"{v:,.0f}"


def market_line(markets: dict[str, Any]) -> str:
    """One-line Korean summary for the UI, e.g. 'BTC 95,120(-1.2%) · 미10년 4.41%(+0.03%p, 11/14) · …'."""
    parts = []
    for key in ORDER:
        m = markets.get(key)
        if not m:
            continue
        txt = _fmt(m["value"]) + ("%" if m["unit"] == "%" else "")
        if m.get("change_pp") is not None:
            txt += f"({m['change_pp']:+.2f}%p"
        elif m.get("change_pct") is not None:
            txt += f"({m['change_pct']:+.1f}%"
        else:
            txt += "("
        if m.get("source") != "Binance":
            txt += (", " if not txt.endswith("(") else "") + _md(m.get("as_of", ""))
        txt = txt + ")" if not txt.endswith("(") else txt[:-1]
        parts.append(f"{m.get('short_ko') or m.get('label')} {txt}")
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
    for line in text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line.rstrip())
    return out


def parse_ics(text: str) -> tuple[list[dict], str | None]:
    """Returns (events, calendar default timezone from X-WR-TIMEZONE)."""
    events, cur, default_tz = [], None, None
    for line in _ics_unfold(text):
        up = line.upper()
        if up == "BEGIN:VEVENT":
            cur = {}
        elif up == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif ":" in line:
            head, val = line.split(":", 1)
            name, *params = head.split(";")
            pdict = {k.upper(): v.strip('"') for k, v in (p.split("=", 1) for p in params if "=" in p)}
            if cur is None:
                if name.upper() == "X-WR-TIMEZONE":
                    default_tz = val.strip()
            else:
                cur[name.upper()] = (val.strip(), pdict)
    return events, default_tz


def _zone(tzid: str | None) -> Any:
    if not tzid:
        return ET_TZ
    try:
        return ZoneInfo(tzid)
    except Exception:
        return ET_TZ  # e.g. "US-Eastern", "Eastern Standard Time": both publishers are in Washington DC


def _ics_dt(val: str, params: dict, default_tz: str | None = None) -> datetime | None:
    """DTSTART in any of: 20261125 (all-day), 20261125T083000, ...Z, with/without TZID, dashed ISO."""
    v = val.strip().replace("-", "").replace(":", "")
    try:
        if params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and v.isdigit()):
            # all-day entry: US data releases come out at 08:30 ET
            return datetime.strptime(v[:8], "%Y%m%d").replace(hour=8, minute=30, tzinfo=ET_TZ)
        utc = v.endswith("Z")
        v = v.rstrip("Z")
        fmt = "%Y%m%dT%H%M%S" if len(v) >= 15 else "%Y%m%dT%H%M"
        dt = datetime.strptime(v[:15] if len(v) >= 15 else v[:13], fmt)
        if utc:
            return dt.replace(tzinfo=timezone.utc)
        return dt.replace(tzinfo=_zone(params.get("TZID") or default_tz))
    except ValueError:
        return None


def calendar_from_ics(text: str, source: str, url: str) -> list[dict]:
    rows = []
    events, default_tz = parse_ics(text)
    for ev in events:
        summary = ev.get("SUMMARY", ("", {}))[0].replace("\\,", ",").replace("\\;", ";").strip()
        hit = next((k for k in CAL_KEYWORDS if k[0].lower() in summary.lower()), None)
        if not hit or "DTSTART" not in ev:
            continue
        dt = _ics_dt(*ev["DTSTART"], default_tz)
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
            got = calendar_from_ics(f.text(url, accept="text/calendar, */*"), name.split("-")[0], url)
            rows += got
            status[name] = f"ok:{len(got)}"
        except Exception as exc:
            status[name] = f"error:{type(exc).__name__}: {str(exc)[:100]}"
    try:
        meetings = parse_fomc_page(f.text(FOMC_URL))
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


def _at(r: dict) -> datetime | None:
    try:
        dt = datetime.fromisoformat(r["at_utc"])
    except (KeyError, TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def upcoming(calendar: list[dict], now: datetime, days: int = 7, start_days: int = 0) -> list[dict]:
    """Events from now+start_days to now+days (inclusive), oldest first."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    lo, hi = now + timedelta(days=start_days), now + timedelta(days=days)
    rows = [(at, r) for r in calendar if (at := _at(r)) and lo <= at <= hi]
    return [r for _, r in sorted(rows, key=lambda x: x[0])]


def later(calendar: list[dict], now: datetime, n: int = 5) -> list[dict]:
    """The next few events after the 7-day window (within 45 days) — used when the week is quiet."""
    return upcoming(calendar, now, 45, start_days=7)[:n]


def calendar_summary(calendar: list[dict], now: datetime) -> dict[str, str]:
    """Per source: total / future / in next 7 days / next date — shown by --check-sources."""
    out: dict[str, str] = {}
    for src in sorted({r["source"] for r in calendar}):
        rows = [r for r in calendar if r["source"] == src]
        fut = upcoming(rows, now, 3650)
        nxt = f"{fut[0]['date_kst']} {fut[0]['time_kst']} {fut[0]['name_ko']}" if fut else "없음"
        out[src] = f"전체 {len(rows)} · 앞으로 {len(fut)} · 7일 안 {len(upcoming(rows, now, 7))} · 다음: {nxt}"
    return out


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
