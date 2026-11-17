"""Read an article's main text for summarising — politely, and without keeping it.

Rules
- robots.txt of each site is obeyed (cached 12 h). If robots.txt cannot be read because of a 5xx / network
  error the page is not fetched (conservative); a 4xx robots.txt means "no rules" (the standard reading).
- One request at a time per site, at least PER_HOST_GAP seconds apart. One retry on a network error / 5xx / 429.
- Login, paywall and blocked pages are not read: HTTP 401/402/403/405/451, a redirect to a login page,
  or schema.org isAccessibleForFree=false. Those items get a title-only summary.
- The text is returned to the caller and never written to disk. Only the first MAX_CHARS characters are used.

Extraction (stdlib only): schema.org JSON-LD `articleBody` if present, otherwise <p> paragraphs outside
navigation / header / footer / aside / figure / forms and outside blocks whose class names look like
related links, newsletters, captions, bylines or ads; ticker strips and menus are dropped by a
"does this look like a sentence" test. Reporter lines and copyright notes are removed.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests

MAX_CHARS = 3000
PER_HOST_GAP = 4.0          # seconds between two requests to the same site
ROBOTS_TTL = 12 * 3600
ROBOTS_AGENT = "news-feed"
BLOCKED_STATUS = {401, 402, 403, 405, 451}
RETRY_STATUS = {429, 500, 502, 503, 504}

SKIP_TAGS = {"nav", "header", "footer", "aside", "figure", "figcaption", "form", "script", "style", "noscript",
             "button", "select", "svg", "template", "iframe"}
SKIP_CLASS = re.compile(r"related|recommend|newsletter|subscribe|promo|advert|\bad-|-ad\b|\bads\b|share|social|"
                        r"caption|byline|author|copyright|disclaimer|footer|breadcrumb|ticker|tags|comment|sidebar|"
                        r"popular|trending|more-stories|read-more|photo|img|login|paywall", re.I)
VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param",
        "track"}

BOILER = re.compile(
    r"(무단\s*전재|재배포\s*금지|저작권자|Copyright|All rights reserved|제보는\s*카카오톡|기자\s*=|\S+@\S+\.\S+|"
    r"Sign up for|Subscribe to|Read more:|Follow us|Disclaimer:|This article was|Edited by|"
    r"사진\s*=|자료사진|\[사진|\(사진)", re.I)
YNA_LEAD = re.compile(r"^\([^)]{1,30}=\s*연합뉴스\)\s*(\S+\s+){0,3}?\S*\s*(기자|특파원)\s*=\s*")


# Where the article body sits on the sites we read (class / id / data-component patterns).
# A site rule only narrows the search; if it finds too little text the generic extraction is used.
SITE_RULES: dict[str, re.Pattern] = {
    "yna.co.kr": re.compile(r"story-news|article-txt|articleWrap", re.I),
    "coindesk.com": re.compile(r"document-body|article-body|at-body|articleBody", re.I),
    "cointelegraph.com": re.compile(r"post-content|post__content|article__body", re.I),
    "decrypt.co": re.compile(r"post-content|article-body|prose", re.I),
    "cnbc.com": re.compile(r"ArticleBody-articleBody|group", re.I),
    "bbc.co.uk": re.compile(r"text-block|article-body|story-body", re.I),
    "bbc.com": re.compile(r"text-block|article-body|story-body", re.I),
    "federalreserve.gov": re.compile(r"\barticle\b|col-md-8", re.I),
    "ecb.europa.eu": re.compile(r"\bsection\b|ecb-pressContent|main-wrapper", re.I),
    "boj.or.jp": re.compile(r"\bmain\b|outline|contents", re.I),
    "bok.or.kr": re.compile(r"dbdata|bd-view|view-cont|viewCont|bbsView", re.I),
    "bls.gov": re.compile(r"\bbodytext\b|main-content|normalnews", re.I),
    "bea.gov": re.compile(r"field--name-body|release-body|node__content", re.I),
}
BLOCK_TAGS = {"p", "div", "li", "td", "th", "tr", "section", "article", "main", "h1", "h2", "h3", "h4", "h5", "h6",
              "blockquote", "dd", "dt", "table", "ul", "ol"}


def site_rule(host: str | None) -> re.Pattern | None:
    host = (host or "").lower()
    return next((v for k, v in SITE_RULES.items() if host == k or host.endswith("." + k)), None)


class _Paragraphs(HTMLParser):
    def __init__(self, focus: re.Pattern | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[bool] = []  # True = inside something we skip
        self.fstack: list[bool] = []  # True = inside the site's article container
        self.focus = focus
        self.in_p = 0
        self.in_pre = 0
        self.buf: list[str] = []
        self.paras: list[tuple[str, bool]] = []  # (text, inside site container)
        self.blocks: list[str] = [""]  # text split at block tags (fallback for pages without <p>)
        self.pres: list[str] = []
        self.ld: list[str] = []
        self._ld = False

    def _skipping(self) -> bool:
        return any(self.stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._ld = True
            self.ld.append("")
        if tag in VOID:
            if tag == "br":
                if self.in_p:
                    self.buf.append(" ")
                self.blocks.append("")
            return
        cls = f"{a.get('class') or ''} {a.get('id') or ''} {a.get('role') or ''} {a.get('data-component') or ''}"
        self.stack.append(tag in SKIP_TAGS or bool(SKIP_CLASS.search(cls)) or a.get("aria-hidden") == "true")
        self.fstack.append(bool(self.focus and self.focus.search(cls)))
        if tag in BLOCK_TAGS:
            self.blocks.append("")
        if tag == "pre" and not self._skipping():
            self.in_pre += 1
            self.pres.append("")
        if tag == "p" and not self._skipping():
            self.in_p += 1
            self.buf = []

    def handle_endtag(self, tag):
        if tag == "script":
            self._ld = False
        if tag in VOID:
            return
        if tag == "p" and self.in_p:
            self.in_p -= 1
            text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if text:
                self.paras.append((text, any(self.fstack)))
            self.buf = []
        if tag == "pre" and self.in_pre:
            self.in_pre -= 1
        if tag in BLOCK_TAGS:
            self.blocks.append("")
        if self.stack:
            self.stack.pop()
        if self.fstack:
            self.fstack.pop()

    def handle_data(self, data):
        if self._ld:
            self.ld[-1] += data
            return
        if self._skipping():
            return
        if self.in_pre:
            self.pres[-1] += data
            return
        if self.in_p:
            self.buf.append(data)
        self.blocks[-1] += data


def _sentence_like(p: str) -> bool:
    if len(p) < 40:
        return False
    letters = sum(ch.isalpha() for ch in p)
    symbols = sum(ch in "$%+−-|·•" or ch.isdigit() for ch in p)
    if letters < len(p) * 0.5 or symbols > len(p) * 0.25:  # price tickers, menus
        return False
    words = p.split()
    if words and max(len(w) for w in words) > 40:  # glued menu text "NewsPredictPerpsLearn…"
        return False
    return bool(re.search(r"[.!?…다요]\s|[.!?…다요]\W*$|[.!?\"”’]\s*$", p))


def _clean(p: str) -> str:
    p = YNA_LEAD.sub("", p)
    return p.strip()


def _ld_items(raw: str):
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return
    stack = [data]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, dict):
            yield x
            stack.extend(v for k, v in x.items() if k in ("@graph", "mainEntity", "itemListElement"))


class Paywalled(Exception):
    pass


def _keep(paras) -> list[str]:
    keep: list[str] = []
    for p in paras:
        p = _clean(re.sub(r"\s+", " ", p).strip())
        if not _sentence_like(p) or BOILER.search(p):
            continue
        if keep and p == keep[-1]:
            continue
        keep.append(p)
    return keep


def extract_text(html_text: str, limit: int = MAX_CHARS, host: str | None = None) -> str:
    """Main article text (plain, one paragraph per line), at most `limit` characters. '' if none found.

    Order: JSON-LD articleBody -> <p> inside the site's article container -> all <p> -> <pre> blocks
    (BLS releases) -> text split at block tags (boards without <p>, e.g. BOK). The longest clean result wins
    among the last three.
    """
    parser = _Paragraphs(site_rule(host))
    parser.feed(html_text)
    parser.close()
    body = ""
    for raw in parser.ld:
        for item in _ld_items(raw):
            if item.get("isAccessibleForFree") in (False, "false", "False"):
                raise Paywalled("isAccessibleForFree=false")
            ab = item.get("articleBody")
            if isinstance(ab, str) and len(ab) > len(body):
                body = ab
    if len(body) >= 300:
        keep = _keep(re.split(r"\n+|(?<=[.!?다])\s{2,}", htmllib.unescape(body)))
        if len("".join(keep)) >= 300:
            return "\n".join(keep)[:limit].rstrip()
    focus = _keep(p for p, inside in parser.paras if inside)
    if len("".join(focus)) >= 300:
        return "\n".join(focus)[:limit].rstrip()
    cands = [_keep(p for p, _ in parser.paras)]
    pre_paras = [" ".join(x.split()) for pre in parser.pres for x in re.split(r"\n\s*\n", pre)]
    cands.append(_keep(pre_paras))
    cands.append(_keep(parser.blocks))
    best = max(cands, key=lambda k: len("".join(k)))
    return "\n".join(best)[:limit].rstrip()


def decode_html(content: bytes, header_encoding: str | None, ctype: str) -> str:
    """Charset from the header, else from <meta charset> (Korean sites may use EUC-KR), else UTF-8."""
    if header_encoding and "charset" in (ctype or "").lower():
        enc = header_encoding
    else:
        m = re.search(rb"charset=[\"']?([A-Za-z0-9_-]+)", content[:4000])
        enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    if enc.lower() in ("euc-kr", "ks_c_5601-1987", "ksc5601"):
        enc = "cp949"
    try:
        return content.decode(enc, "replace")
    except LookupError:
        return content.decode("utf-8", "replace")


UPBIT_NOTICE = re.compile(r"https://upbit\.com/service_center/notice\?id=(\d+)")
UPBIT_DETAIL = "https://api-manager.upbit.com/api/v1/announcements/{}"


def notice_text(body_html: str, limit: int = MAX_CHARS) -> str:
    """Plain text of an exchange notice (short lines are kept: notices are lists of facts)."""
    t = re.sub(r"<(br|/p|/li|/div|/h\d|/tr)[^>]*>", "\n", body_html or "", flags=re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", t))
    lines = [re.sub(r"\s+", " ", x).strip() for x in t.split("\n")]
    lines = [x for x in lines if len(x) >= 2 and not BOILER.search(x)]
    return "\n".join(lines)[:limit].rstrip()


class ArticleReader:
    """Fetches article pages with robots.txt, per-site spacing and one retry. Never stores bodies."""

    def __init__(self, session: requests.Session | None = None, user_agent: str = "news-feed/0.4 (+personal research)",
                 gap: float = PER_HOST_GAP, sleep=time.sleep, clock=time.monotonic) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
        self.gap = gap
        self.sleep = sleep
        self.clock = clock
        self._last: dict[str, float] = {}
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            delay = self.gap - (self.clock() - last)
            if delay > 0:
                self.sleep(delay)
        self._last[host] = self.clock()

    def _get(self, url: str) -> requests.Response:
        host = urlsplit(url).netloc
        self._wait(host)
        return self.session.get(url, timeout=15, allow_redirects=True)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        cached = self._robots.get(host)
        if not cached or self.clock() - cached[0] > ROBOTS_TTL:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                r = self._get(f"{parts.scheme}://{host}/robots.txt")
                if r.status_code >= 500:
                    rp = None
                elif r.status_code >= 400:
                    rp.parse([])  # no robots.txt -> no rules
                else:
                    rp.parse(r.content.decode("utf-8", "replace").splitlines())
            except requests.RequestException:
                rp = None
            cached = (self.clock(), rp)
            self._robots[host] = cached
        rp = cached[1]
        return rp is not None and rp.can_fetch(ROBOTS_AGENT, url)

    def read(self, url: str) -> tuple[str, str]:
        """Returns (text, status). status: ok | robots | paywall | blocked:<code> | http:<code> | error:<type> | empty."""
        if not url.startswith(("http://", "https://")):
            return "", "error:bad-url"
        m = UPBIT_NOTICE.match(url)
        if m:  # upbit.com notice pages are rendered by script; the same text comes from the public notice API
            return self._read_upbit(m.group(1))
        if not self.allowed(url):
            return "", "robots"
        for attempt in (1, 2):
            try:
                r = self._get(url)
            except requests.RequestException as exc:
                if attempt == 2:
                    return "", f"error:{type(exc).__name__}"
                continue
            if r.status_code in BLOCKED_STATUS:
                return "", f"blocked:{r.status_code}"
            if r.status_code in RETRY_STATUS:
                if attempt == 2:
                    return "", f"http:{r.status_code}"
                continue
            if r.status_code >= 400:
                return "", f"http:{r.status_code}"
            if re.search(r"/(login|signin|sign-in|subscribe|account)\b", urlsplit(r.url).path, re.I):
                return "", "paywall"
            ctype = r.headers.get("Content-Type", "text/html")
            if "html" not in ctype:
                return "", "error:not-html"
            try:
                text = extract_text(decode_html(r.content, r.encoding, ctype), host=urlsplit(r.url or url).netloc)
            except Paywalled:
                return "", "paywall"
            return (text, "ok") if len(text) >= 200 else ("", "empty")
        return "", "error:retry"

    def _read_upbit(self, notice_id: str) -> tuple[str, str]:
        url = UPBIT_DETAIL.format(notice_id)
        if not self.allowed(url):
            return "", "robots"
        for attempt in (1, 2):
            try:
                r = self._get(url)
            except requests.RequestException as exc:
                if attempt == 2:
                    return "", f"error:{type(exc).__name__}"
                continue
            if r.status_code in RETRY_STATUS and attempt == 1:
                continue
            if r.status_code >= 400:
                return "", (f"blocked:{r.status_code}" if r.status_code in BLOCKED_STATUS else f"http:{r.status_code}")
            try:
                data = json.loads(r.content.decode("utf-8", "replace")).get("data") or {}
            except (json.JSONDecodeError, AttributeError):
                return "", "error:json"
            text = notice_text(str(data.get("body") or data.get("content") or ""))
            return (text, "ok") if len(text) >= 80 else ("", "empty")
        return "", "error:retry"
