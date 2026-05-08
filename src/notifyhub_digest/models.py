from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ImpactLevel = Literal["Critical", "High", "Medium", "Low", "Info", "Unknown"]
ThreatType = Literal[
    "Vulnerability",
    "Exploit",
    "Zero-day",
    "Vulnerability Disclosure",
    "Patch",
    "Misconfiguration",
    "Malware",
    "Ransomware",
    "Botnet",
    "Cryptojacking",
    "Phishing",
    "Business Email Compromise",
    "Scam/Fraud",
    "Credential Theft",
    "Intrusion",
    "Data Breach",
    "DDoS",
    "Supply Chain",
    "Advisory",
    "Other",
    "Unknown",
]


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


class Lesson(BaseModel):
    title: str
    body: str


class InformationSource(BaseModel):
    title: str
    url: str
    source_type: str = "web"


class AnalysisResult(BaseModel):
    summary_html: str = ""
    technical_terms: list[TechnicalTerm] = Field(default_factory=list)
    lessons: list[Lesson] = Field(default_factory=list)
    impact_level: ImpactLevel = "Unknown"
    impact_reason: str = ""
    threat_type: ThreatType = "Unknown"
    model_version: str = ""


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


class FeaturedTopic(BaseModel):
    topic_id: str = "featured-topic-1"
    title: str
    source_name: str
    published_at: datetime
    original_url: str
    analysis: AnalysisResult
    selection_reason: str = ""
    requested_category: str = ""
    information_sources: list[InformationSource] = Field(default_factory=list)

    @property
    def article_path(self) -> str:
        return f"articles/{self.topic_id}.html"

    def as_feed_item(self) -> FeedItem:
        impact_reason = (self.analysis.impact_reason or "").strip()
        selection_reason = (self.selection_reason or "").strip()
        if selection_reason:
            impact_reason = f"AI選定理由: {selection_reason}\n\n{impact_reason}".strip()

        return FeedItem(
            entry_id=self.topic_id,
            title=self.title,
            source_name=self.source_name,
            category="featured",
            published_at=self.published_at,
            original_url=self.original_url,
            analysis=self.analysis.model_copy(update={"impact_reason": impact_reason}),
        )
