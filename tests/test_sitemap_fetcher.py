from __future__ import annotations

from datetime import datetime

from notifyhub_digest.rss import _parse_sitemap_urls
from notifyhub_digest.timeutils import UTC


def test_parse_sitemap_urlset_extracts_loc_and_lastmod():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.test/news/item-1/</loc>
    <lastmod>2026-01-11T12:34:56Z</lastmod>
  </url>
  <url>
    <loc>https://example.test/other/</loc>
    <lastmod>2026-01-10</lastmod>
  </url>
</urlset>
"""

    items = _parse_sitemap_urls(xml)
    assert len(items) == 2
    assert items[0].loc == "https://example.test/news/item-1/"
    assert items[0].lastmod_utc == datetime(2026, 1, 11, 12, 34, 56, tzinfo=UTC)


def test_parse_sitemap_index_extracts_sitemap_locs():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.test/sitemap-1.xml</loc>
    <lastmod>2026-01-11T00:00:00Z</lastmod>
  </sitemap>
</sitemapindex>
"""

    items = _parse_sitemap_urls(xml)
    assert len(items) == 1
    assert items[0].loc == "https://example.test/sitemap-1.xml"
    assert items[0].lastmod_utc == datetime(2026, 1, 11, 0, 0, 0, tzinfo=UTC)
