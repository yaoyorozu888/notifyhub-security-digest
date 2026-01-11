from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin
from xml.etree import ElementTree

import feedparser
import httpx
from dateutil import parser as dtparser

from notifyhub_digest.models import Source
from notifyhub_digest.timeutils import UTC


@dataclass(frozen=True)
class RawEntry:
    entry_id: str
    title: str
    link: str
    published_at_utc: datetime
    summary: str | None


def _stable_entry_id(source_name: str, guid_or_link: str) -> str:
    h = hashlib.sha256(f"{source_name}|{guid_or_link}".encode("utf-8")).hexdigest()
    return h[:16]


def _parse_entry_published_utc(entry: dict) -> datetime | None:
    # feedparser may provide *_parsed (time.struct_time) OR string dates.
    for key in ("published_parsed", "updated_parsed"):
        if key in entry and entry[key]:
            try:
                dt = datetime(*entry[key][:6], tzinfo=UTC)
                return dt
            except Exception:
                pass

    for key in ("published", "updated"):
        if key in entry and entry[key]:
            try:
                dt = dtparser.parse(str(entry[key]))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
            except Exception:
                pass

    return None


def fetch_feed_entries(
    client: httpx.Client,
    source: Source,
    user_agent: str,
) -> list[RawEntry]:
    if source.fetch_method == "sitemap":
        return fetch_sitemap_entries(client, source, user_agent=user_agent)

    res = client.get(source.feed_url, headers=_make_request_headers(user_agent))
    res.raise_for_status()

    parsed = feedparser.parse(res.content)
    out: list[RawEntry] = []

    for e in parsed.entries or []:
        edict = dict(e)
        link = str(getattr(e, "link", "") or "")
        title = str(getattr(e, "title", "") or "").strip()
        guid = str(getattr(e, "id", "") or "")
        published = _parse_entry_published_utc(edict)

        if not link or not title or published is None:
            continue

        entry_id = _stable_entry_id(source.name, guid or link)
        summary = None
        if getattr(e, "summary", None):
            summary = str(getattr(e, "summary"))

        out.append(
            RawEntry(
                entry_id=entry_id,
                title=title,
                link=link,
                published_at_utc=published,
                summary=summary,
            )
        )

    if out:
        return out

    if _looks_like_html_response(res):
        fallback = _fallback_parse_entries_from_html(source, res.text)
        if fallback:
            return fallback

    return out


def fetch_sitemap_entries(
    client: httpx.Client,
    source: Source,
    user_agent: str,
) -> list[RawEntry]:
    res = client.get(source.feed_url, headers=_make_request_headers(user_agent))
    res.raise_for_status()

    urls = _parse_sitemap_urls(res.text)
    # If this is a sitemap index (no lastmod per-entry), follow a few child sitemaps.
    if urls and all(u.lastmod_utc is None for u in urls):
        child_urls: list[_SitemapUrl] = []
        # Keep this small to avoid excessive requests.
        for child in urls[:5]:
            try:
                child_res = client.get(child.loc, headers=_make_request_headers(user_agent))
                child_res.raise_for_status()
                child_urls.extend(_parse_sitemap_urls(child_res.text))
            except Exception:
                continue
        urls = child_urls

    # Normalize scheme (some sitemaps return http:// links).
    urls = [
        _SitemapUrl(
            loc=(u.loc.replace("http://", "https://", 1) if u.loc.startswith("http://") else u.loc),
            lastmod_utc=u.lastmod_utc,
        )
        for u in urls
    ]

    if source.url_include_prefix:
        want = source.url_include_prefix
        want_alt = (
            want.replace("https://", "http://", 1)
            if want.startswith("https://")
            else want.replace("http://", "https://", 1)
        )
        urls = [u for u in urls if u.loc.startswith(want) or u.loc.startswith(want_alt)]

    # Sort newest first if we have lastmod.
    urls.sort(key=lambda u: u.lastmod_utc or datetime.min.replace(tzinfo=UTC), reverse=True)

    max_entries = source.max_entries or 200
    urls = urls[:max_entries]

    out: list[RawEntry] = []
    for u in urls:
        if u.lastmod_utc is None:
            continue
        title = _title_from_url(u.loc)
        entry_id = _stable_entry_id(source.name, u.loc)
        out.append(
            RawEntry(
                entry_id=entry_id,
                title=title,
                link=u.loc,
                published_at_utc=u.lastmod_utc,
                summary=None,
            )
        )

    return out


@dataclass(frozen=True)
class _SitemapUrl:
    loc: str
    lastmod_utc: datetime | None


def _parse_sitemap_urls(xml_text: str) -> list[_SitemapUrl]:
    # Supports both <urlset> and <sitemapindex>.
    # For sitemapindex we return the sitemap locs with lastmod, since recursively fetching
    # would be expensive; callers should point feed_url to the concrete sitemap.xml.
    try:
        root = ElementTree.fromstring(xml_text)
    except Exception:
        return []

    def strip_ns(tag: str) -> str:
        return tag.split("}", 1)[1] if "}" in tag else tag

    root_tag = strip_ns(root.tag).lower()

    def find_text(node: ElementTree.Element, name: str) -> str | None:
        for child in list(node):
            if strip_ns(child.tag).lower() == name:
                if child.text:
                    return child.text.strip()
                return None
        return None

    items: list[_SitemapUrl] = []

    if root_tag == "urlset":
        for url_node in root:
            if strip_ns(url_node.tag).lower() != "url":
                continue
            loc = find_text(url_node, "loc")
            if not loc:
                continue
            lastmod = find_text(url_node, "lastmod")
            lastmod_utc = None
            if lastmod:
                try:
                    dt = dtparser.parse(lastmod)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    lastmod_utc = dt.astimezone(UTC)
                except Exception:
                    lastmod_utc = None
            items.append(_SitemapUrl(loc=loc, lastmod_utc=lastmod_utc))

    elif root_tag == "sitemapindex":
        for sm_node in root:
            if strip_ns(sm_node.tag).lower() != "sitemap":
                continue
            loc = find_text(sm_node, "loc")
            if not loc:
                continue
            lastmod = find_text(sm_node, "lastmod")
            lastmod_utc = None
            if lastmod:
                try:
                    dt = dtparser.parse(lastmod)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    lastmod_utc = dt.astimezone(UTC)
                except Exception:
                    lastmod_utc = None
            items.append(_SitemapUrl(loc=loc, lastmod_utc=lastmod_utc))

    return items


def _title_from_url(url: str) -> str:
    # Best-effort title for sitemap-only sources.
    path = url.split("?", 1)[0].rstrip("/")
    slug = path.rsplit("/", 1)[-1] if "/" in path else path
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug[:120] or url


def _make_request_headers(user_agent: str) -> dict[str, str]:
    # Some sources behave better with explicit Accept.
    return {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        # CISA等でbr/gzip圧縮のネゴシエーションが原因で切断されるケースがあるため、明示的に無圧縮を要求する。
        "Accept-Encoding": "identity",
    }


def _looks_like_html_response(res: httpx.Response) -> bool:
    ct = (res.headers.get("content-type") or "").lower()
    if "html" in ct:
        return True
    head = (res.text or "").lstrip()[:256].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def _fallback_parse_entries_from_html(source: Source, html: str) -> list[RawEntry]:
    url = source.feed_url.lower()
    if "shadowserver.org" in url:
        return _parse_shadowserver_news_insights_html(source, html)
    return []


_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


def _parse_shadowserver_news_insights_html(source: Source, html: str) -> list[RawEntry]:
    # Shadowserverのニュース一覧は、RSS/Atomを返さずHTMLのみ返すことがある。
    # その場合でも「記事タイトル + URL + 日付(記事の横に表示)」を拾えるようにする。
    out: list[RawEntry] = []
    seen_links: set[str] = set()

    # 例: <a href="https://www.shadowserver.org/news/.../">Title</a> のようなリンクを拾う
    link_re = re.compile(
        r'href="(?P<href>https?://(?:www\.)?shadowserver\.org/news/[^\"]+)"[^>]*>(?P<title>[^<]{2,200})</a>',
        re.IGNORECASE,
    )

    # 日付は「OCTOBER 12, 2023」のように出てくる。
    month_alt = "|".join(_MONTHS)
    date_re = re.compile(rf"\b(?:{month_alt})\b\s+\d{{1,2}},\s+\d{{4}}", re.IGNORECASE)

    for m in link_re.finditer(html):
        href = m.group("href").strip()
        title = m.group("title").strip()
        if not href or not title:
            continue
        href = urljoin(source.feed_url, href)
        if href in seen_links:
            continue

        # リンク近傍に表示される日付を探す
        tail = html[m.end() : m.end() + 2000]
        dm = date_re.search(tail)
        if not dm:
            # たまに日付がリンクより前にある可能性もあるので、前方も少し見る
            head = html[max(0, m.start() - 2000) : m.start()]
            dm = date_re.search(head)

        if not dm:
            continue

        try:
            published = dtparser.parse(dm.group(0))
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            published = published.astimezone(UTC)
        except Exception:
            continue

        entry_id = _stable_entry_id(source.name, href)
        out.append(
            RawEntry(
                entry_id=entry_id,
                title=title,
                link=href,
                published_at_utc=published,
                summary=None,
            )
        )
        seen_links.add(href)

    return out


def iter_enabled_sources(sources: Iterable[Source]) -> list[Source]:
    return [s for s in sources if s.enabled]
