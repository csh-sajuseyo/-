"""Key-less public sources: crypto RSS, exchange notices, central banks, US data releases, economic news.

RSS feed list and the RSS 2.0 / Atom parsing approach are reused from
coin-auto-trader `coinai/news_engine.py` (NewsEngine.DIRECT_CRYPTO_FEEDS,
NewsEngine._parse_xml). RSS 1.0 (RDF) support was added for the Bank of Japan.

Every source here is free, needs no login or key, and is published as a feed
for automated readers. Why each one was chosen: docs/SOURCES.md.
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import requests

USER_AGENT = "news-feed/0.2 (+personal research; public RSS/notice collector)"
TIMEOUT = 15


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    group: str          # "crypto" | "macro"  (exchange notices are their own group)
    reliability: float
    lang: str = "en"


RSS_FEEDS: tuple[Feed, ...] = (
    # crypto — same feeds as coin-auto-trader NewsEngine
    Feed("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss", "crypto", 1.00),
    Feed("Cointelegraph", "https://cointelegraph.com/rss", "crypto", 0.92),
    Feed("Decrypt", "https://decrypt.co/feed", "crypto", 0.88),
    # central banks (official press releases)
    Feed("Fed", "https://www.federalreserve.gov/feeds/press_all.xml", "macro", 1.00),
    Feed("ECB", "https://www.ecb.europa.eu/rss/press.html", "macro", 1.00),
    Feed("BOJ", "https://www.boj.or.jp/en/rss/whatsnew.xml", "macro", 1.00),
    Feed("BOK", "https://www.bok.or.kr/portal/bbs/B0000338/news.rss?menuNo=200761", "macro", 1.00, "ko"),
    # US official statistics releases
    Feed("BLS", "https://www.bls.gov/feed/bls_latest.rss", "macro", 1.00),
    Feed("BEA", "https://apps.bea.gov/rss/rss.xml", "macro", 1.00),
    # economic news (1 Korean + 2 English)
    Feed("Yonhap", "https://www.yna.co.kr/rss/economy.xml", "macro", 0.90, "ko"),
    Feed("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "macro", 0.85),
    Feed("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml", "macro", 0.88),
)

UPBIT_URL = "https://api-manager.upbit.com/api/v1/announcements"
BINANCE_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
# 48 New Listing, 49 Latest News, 161 Delisting, 157 Maintenance (wallet/deposit halts)
BINANCE_CATALOGS = {48: "listing", 49: "news", 161: "delisting", 157: "maintenance"}

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "rss1": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


@dataclass
class RawItem:
    source: str
    kind: str                 # "rss" | "upbit_notice" | "binance_notice"
    title: str
    url: str
    published_at: str | None  # ISO-8601 UTC
    reliability: float = 0.8
    category: str | None = None
    group: str = "crypto"     # "crypto" | "macro" | "exchange"
    summary: str = ""         # feed description, plain text, short
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
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return _iso(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


_TAG = re.compile(r"<[^>]+>")


def clean_text(value: str | None, limit: int = 240) -> str:
    text = html.unescape(_TAG.sub(" ", value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_feed(xml_text: str | bytes, source: str, reliability: float, group: str = "crypto") -> list[RawItem]:
    """RSS 2.0, Atom and RSS 1.0 (RDF)."""
    if isinstance(xml_text, bytes):
        root = ET.fromstring(xml_text.lstrip(b"\xef\xbb\xbf \r\n\t"))
    else:
        root = ET.fromstring(xml_text.lstrip("\ufeff \r\n\t"))

    def mk(title, url, date, desc, full=None):
        item = _mk(title, url, date, desc)
        # Full text in the feed (content:encoded / Atom content / a long description) is kept in memory only
        # (RawItem.extra, never written to disk) and summarised instead of reading the page.
        raw = full if full and len(full) > len(desc or "") else desc
        if raw and len(raw) >= 600:
            item.extra["rss_html"] = raw
        return item

    def _mk(title, url, date, desc):
        return RawItem(source=source, kind="rss", title=clean_text(title, 300), url=(url or "").strip(),
                       published_at=_parse_date(date or ""), reliability=reliability,
                       group=group, summary=clean_text(desc))

    rows: list[RawItem] = []
    for node in root.findall("./channel/item")[:80]:  # RSS 2.0
        rows.append(mk(node.findtext("title"), node.findtext("link"),
                       node.findtext("pubDate") or node.findtext("dc:date", namespaces=NS),
                       node.findtext("description"), node.findtext("content:encoded", namespaces=NS)))
    if not rows:  # Atom
        for node in root.findall("a:entry", NS)[:80]:
            link = node.find("a:link[@rel='alternate']", NS)
            if link is None:
                link = node.find("a:link", NS)
            rows.append(mk(node.findtext("a:title", namespaces=NS),
                           link.attrib.get("href", "") if link is not None else "",
                           node.findtext("a:published", namespaces=NS) or node.findtext("a:updated", namespaces=NS),
                           node.findtext("a:summary", namespaces=NS), node.findtext("a:content", namespaces=NS)))
    if not rows:  # RSS 1.0 / RDF (Bank of Japan)
        for node in root.findall("rss1:item", NS)[:80]:
            rows.append(mk(node.findtext("rss1:title", namespaces=NS), node.findtext("rss1:link", namespaces=NS),
                           node.findtext("dc:date", namespaces=NS),
                           node.findtext("rss1:description", namespaces=NS)))
    return [r for r in rows if r.title]


# Hosts that ask automated readers to identify themselves with a contact address
# (bls.gov answers 403 to anonymous scripts). The contact comes from .env NEWS_FEED_CONTACT.
CONTACT_HOSTS = ("bls.gov",)


class Fetcher:
    def __init__(self, session: requests.Session | None = None, contact: str = "") -> None:
        self.session = session or requests.Session()
        self.contact = contact.strip()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _headers(self, url: str, accept: str | None) -> dict[str, str]:
        h: dict[str, str] = {}
        if accept:
            h["Accept"] = accept
        if self.contact and any(host in url for host in CONTACT_HOSTS):
            # BLS: "include contact information (e-mail) in the User-Agent"; From is the standard header for it
            h["User-Agent"] = f"news-feed/0.3 (personal research; contact: {self.contact})"
            h["From"] = self.contact
            h.setdefault("Accept-Language", "en-US,en;q=0.8")
        return h

    def get(self, url: str, accept: str | None = None, **kw) -> requests.Response:
        headers = {**self._headers(url, accept), **(kw.pop("headers", None) or {})}
        r = self.session.get(url, timeout=TIMEOUT, headers=headers, **kw)
        r.raise_for_status()
        return r

    def text(self, url: str, accept: str | None = None, **kw) -> str:
        """Body as text, decoded as UTF-8 (servers often omit the charset; requests would guess Latin-1)."""
        return self.get(url, accept, **kw).content.decode("utf-8", errors="replace")

    def rss(self, feed: Feed) -> list[RawItem]:
        r = self.get(feed.url)
        return parse_feed(r.content, feed.name, feed.reliability, feed.group)  # bytes: XML decl decides encoding

    def upbit(self) -> list[RawItem]:
        r = self.get(UPBIT_URL, headers={"Accept": "application/json"},
                     params={"os": "web", "page": 1, "per_page": 20, "category": "all"})
        data = r.json().get("data") or {}
        rows = []
        for n in data.get("notices") or data.get("list") or []:
            nid = n.get("id")
            rows.append(RawItem(
                source="Upbit", kind="upbit_notice", title=str(n.get("title") or "").strip(),
                url=f"https://upbit.com/service_center/notice?id={nid}" if nid else "",
                published_at=_parse_date(str(n.get("listed_at") or n.get("first_listed_at") or "")),
                reliability=1.0, category=n.get("category"), group="exchange",
            ))
        return [x for x in rows if x.title]

    def binance(self) -> list[RawItem]:
        r = self.get(BINANCE_URL, params={"type": 1, "pageNo": 1, "pageSize": 20})
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
                    reliability=1.0, category=label, group="exchange",
                ))
        return [x for x in rows if x.title]

    def jobs(self) -> list[tuple[str, Callable[[], list[RawItem]]]]:
        out: list[tuple[str, Callable[[], list[RawItem]]]] = [
            (f.name, (lambda f=f: self.rss(f))) for f in RSS_FEEDS
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
