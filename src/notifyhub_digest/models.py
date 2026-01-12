from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ImpactLevel = Literal["Critical", "High", "Medium", "Low", "Info", "Unknown"]


class Source(BaseModel):
    name: str
    category: str = "reporting"
    feed_url: str
    enabled: bool = True
    weight: int = 1

    # How to fetch entries from this source.
    # - auto: try RSS/Atom first, then fall back for known sites
    # - rss: force RSS/Atom parsing
    # - sitemap: parse XML sitemap <urlset>/<sitemapindex>
    # - json: fetch JSON feed (json_kind decides schema)
    fetch_method: Literal["auto", "rss", "sitemap", "json"] = "auto"

    # Optional filtering/tuning for non-RSS sources.
    url_include_prefix: str | None = None
    max_entries: int | None = None
    json_kind: Literal["cisa-kev", "nvd", "msrc-cvrf"] | None = None


class TechnicalTerm(BaseModel):
    term: str
    explanation: str


class AnalysisResult(BaseModel):
    summary_html: str = ""
    technical_terms: list[TechnicalTerm] = Field(default_factory=list)
    impact_level: ImpactLevel = "Unknown"
    impact_reason: str = ""
    threat_type: str = "-"


class FeedItem(BaseModel):
    entry_id: str
    title: str
    source_name: str
    category: str = "reporting"
    published_at: datetime
    original_url: str

    analysis: AnalysisResult

    @property
    def article_path(self) -> str:
        return f"articles/{self.entry_id}.html"
