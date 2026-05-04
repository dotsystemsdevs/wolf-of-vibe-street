"""News fetchers — Reddit JSON + RSS feeds.

Stdlib-only HTTP (urllib) so we don't add new deps. Each fetcher returns a
list of normalized records with timestamp/headline/url/body fields; the
caller (tools/news_fetcher) tags symbols + scores sentiment + persists.

Sources chosen for breadth + free access:
  - Reddit r/CryptoCurrency, r/Bitcoin, r/ethereum, r/CryptoMarkets
  - CoinDesk RSS, Decrypt RSS, Cointelegraph RSS

Failure modes: any single source going down (network, rate limit, format
change) returns [] silently. The caller should not fail just because one
feed is broken.
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = "traderbot/1.0 (paper-only crypto research bot)"
TIMEOUT_S = 10

REDDIT_SUBS = [
    "CryptoCurrency",
    "Bitcoin",
    "ethereum",
    "CryptoMarkets",
]

RSS_FEEDS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("decrypt", "https://decrypt.co/feed"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
]


@dataclass(frozen=True, slots=True)
class RawArticle:
    timestamp_ms: int
    source: str
    headline: str
    url: str
    body: str | None


def _fetch_url(url: str) -> bytes | None:
    """GET with timeout + UA. Returns None on any error so the caller doesn't crash."""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.read()
    except (HTTPError, URLError, TimeoutError, OSError):
        return None


def fetch_reddit(sub: str, *, limit: int = 25) -> list[RawArticle]:
    """Pull recent posts from a public subreddit's .json endpoint."""
    url = f"https://www.reddit.com/r/{sub}/new.json?limit={limit}"
    raw = _fetch_url(url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[RawArticle] = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {}) or {}
        title = (post.get("title") or "").strip()
        if not title:
            continue
        ts = post.get("created_utc")
        if ts is None:
            continue
        permalink = post.get("permalink") or ""
        body = (post.get("selftext") or "").strip() or None
        out.append(
            RawArticle(
                timestamp_ms=int(float(ts) * 1000),
                source=f"reddit/{sub}",
                headline=title,
                url=f"https://www.reddit.com{permalink}",
                body=body,
            )
        )
    return out


def _parse_rss_date(s: str | None) -> int:
    """RSS pubDate strings → epoch ms. 0 if missing/unparseable."""
    if not s:
        return 0
    try:
        dt = parsedate_to_datetime(s)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def fetch_rss(source: str, url: str) -> list[RawArticle]:
    """Generic RSS 2.0 / Atom parser. Tolerant of feed quirks."""
    raw = _fetch_url(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out: list[RawArticle] = []
    # RSS 2.0 puts items in channel/item; Atom uses entry under root.
    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry"
    )
    for item in items:
        title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
        link_el = item.find("link") or item.find("{http://www.w3.org/2005/Atom}link")
        pub_el = (
            item.find("pubDate")
            or item.find("{http://www.w3.org/2005/Atom}published")
            or item.find("{http://www.w3.org/2005/Atom}updated")
        )
        desc_el = (
            item.find("description")
            or item.find("{http://www.w3.org/2005/Atom}summary")
            or item.find("{http://www.w3.org/2005/Atom}content")
        )
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue
        # Atom <link href="...">; RSS <link>...</link>
        link = ""
        if link_el is not None:
            link = link_el.get("href") or (link_el.text or "")
        link = link.strip()
        ts_ms = _parse_rss_date(pub_el.text if pub_el is not None else None)
        if ts_ms == 0:
            ts_ms = int(time.time() * 1000)
        body = (desc_el.text or "").strip() if desc_el is not None else None
        out.append(
            RawArticle(
                timestamp_ms=ts_ms,
                source=f"rss/{source}",
                headline=title,
                url=link or f"https://example.invalid/{source}/{ts_ms}",
                body=body,
            )
        )
    return out


def fetch_all() -> list[RawArticle]:
    """Best-effort fetch from every configured source. Skips silent on errors."""
    out: list[RawArticle] = []
    for sub in REDDIT_SUBS:
        out.extend(fetch_reddit(sub))
    for source, url in RSS_FEEDS:
        out.extend(fetch_rss(source, url))
    return out
