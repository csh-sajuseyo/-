"""Key-less public sources: crypto RSS feeds, Upbit notices, Binance announcements.

RSS feed list and the RSS 2.0 / Atom parsing approach are reused from
coin-auto-trader `coinai/news_engine.py` (NewsEngine.DIRECT_CRYPTO_FEEDS,
NewsEngine._parse_xml), trimmed to what a collector needs.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import requests

USER_AGENT = "news-feed/0.1 (+public RSS/notice collector)"
TIMEOUT = 15

# (source name, url, reliability) — same feeds as coin-auto-trader NewsEngine.
RSS_FEEDS: tuple[tuple[str, str, float], ...] = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss", 1.00),
    ("Cointelegraph", "https://cointelegraph.com/rss", 0.92),
    ("Decrypt", "https://decrypt.co/feed", 0.88),
)

UPBIT_URL = "https://api-manager.upbit.com/api/v1/announcements"
BINANCE_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
# 48 New Listing, 49 Latest News, 161 Delisting, 157 Maintenance (wallet/deposit halts)
BINANCE_CATALOGS = {48: "listing", 49: "news", 161: "delisting", 157: "maintenance"}


@dataclass
class RawItem:
    source: str
    kind: str                 # "rss" | "upbit_notice" | "binance_notice"
    title: str
    url: str
    published_at: str | None  # ISO-8601 UTC
    reliability: float = 0.8
    category: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        key = f"{self.source}|{self.url or self.title}".encode("utf-8")
        return hashlib.sha1(key).hexdigest()[:16]


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_date(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return _iso(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        pass
    try:
        return _iso(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_feed(xml_text: str, source: str, reliability: float) -> list[RawItem]:
    root = ET.fromstring(xml_text)
    rows: list[RawItem] = []
    for node in root.findall("./channel/item")[:80]:  # RSS 2.0
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        rows.append(RawItem(
            source=source, kind="rss", title=title,
            url=(node.findtext("link") or "").strip(),
            published_at=_parse_date(node.findtext("pubDate") or ""),
            reliability=reliability,
        ))
    if rows:
        return rows
    ns = {"a": "http://www.w3.org/2005/Atom"}  # Atom fallback
    for node in root.findall("a:entry", ns)[:80]:
        title = (node.findtext("a:title", default="", namespaces=ns) or "").strip()
        if not title:
            continue
        link = node.find("a:link", ns)
        rows.append(RawItem(
            source=source, kind="rss", title=title,
            url=link.attrib.get("href", "") if link is not None else "",
            published_at=_parse_date(node.findtext("a:published", default="", namespaces=ns)
                                     or node.findtext("a:updated", default="", namespaces=ns)),
            reliability=reliability,
        ))
    return rows


class Fetcher:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def rss(self, source: str, url: str, reliability: float) -> list[RawItem]:
        r = self.session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_feed(r.text, source, reliability)

    def upbit(self) -> list[RawItem]:
        r = self.session.get(UPBIT_URL, timeout=TIMEOUT, headers={"Accept": "application/json"},
                             params={"os": "web", "page": 1, "per_page": 20, "category": "all"})
        r.raise_for_status()
        data = r.json().get("data") or {}
        rows = []
        for n in data.get("notices") or data.get("list") or []:
            nid = n.get("id")
            rows.append(RawItem(
                source="Upbit", kind="upbit_notice", title=str(n.get("title") or "").strip(),
                url=f"https://upbit.com/service_center/notice?id={nid}" if nid else "",
                published_at=_parse_date(str(n.get("listed_at") or n.get("first_listed_at") or "")),
                reliability=1.0, category=n.get("category"),
            ))
        return [x for x in rows if x.title]

    def binance(self) -> list[RawItem]:
        r = self.session.get(BINANCE_URL, timeout=TIMEOUT,
                             params={"type": 1, "pageNo": 1, "pageSize": 20})
        r.raise_for_status()
        rows = []
        for cat in (r.json().get("data") or {}).get("catalogs") or []:
            label = BINANCE_CATALOGS.get(cat.get("catalogId"))
            if not label:
                continue
            for a in cat.get("articles") or []:
                ts = a.get("releaseDate")
                rows.append(RawItem(
                    source="Binance", kind="binance_notice", title=str(a.get("title") or "").strip(),
                    url=f"https://www.binance.com/en/support/announcement/{a.get('code')}",
                    published_at=_iso(datetime.fromtimestamp(ts / 1000, timezone.utc)) if ts else None,
                    reliability=1.0, category=label,
                ))
        return [x for x in rows if x.title]

    def jobs(self) -> list[tuple[str, Callable[[], list[RawItem]]]]:
        out: list[tuple[str, Callable[[], list[RawItem]]]] = [
            (name, (lambda n=name, u=url, rel=rel: self.rss(n, u, rel))) for name, url, rel in RSS_FEEDS
        ]
        out.append(("Upbit", self.upbit))
        out.append(("Binance", self.binance))
        return out

    def collect(self) -> tuple[list[RawItem], dict[str, str]]:
        """Fetch every source; a failing source never stops the others."""
        items: list[RawItem] = []
        status: dict[str, str] = {}
        for name, job in self.jobs():
            try:
                got = job()
                items.extend(got)
                status[name] = f"ok:{len(got)}"
            except Exception as exc:  # network/HTTP/parse — keep going
                status[name] = f"error:{type(exc).__name__}: {str(exc)[:120]}"
        return items, status
