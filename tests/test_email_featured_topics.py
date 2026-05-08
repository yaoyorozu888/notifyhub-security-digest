from __future__ import annotations

from datetime import datetime

from notifyhub_digest.acs_email import build_digest_email_html
from notifyhub_digest.models import AnalysisResult, FeaturedTopic, InformationSource
from notifyhub_digest.timeutils import JST


def test_build_digest_email_html_renders_featured_topics_section() -> None:
    featured_topics = [
        FeaturedTopic(
            topic_id="featured-topic-1",
            title="Canvas Learning Platform Hit by Major Data Breach and Outage",
            source_name="CNN",
            published_at=datetime(2026, 5, 8, 8, 4, tzinfo=JST),
            original_url="https://example.com/featured",
            analysis=AnalysisResult(
                summary_html="<h4>概要</h4><p>summary</p>",
                technical_terms=[],
                lessons=[],
                impact_level="High",
                impact_reason="reason",
                threat_type="Data Breach",
                model_version="grok-4.3",
            ),
            selection_reason="Major disruption with broad user impact.",
            requested_category="Cybersecurity",
            information_sources=[
                InformationSource(title="CNN report", url="https://example.com/featured", source_type="news")
            ],
        )
    ]

    html = build_digest_email_html(
        day="2026-05-09",
        digest_root_url="https://notifyhub.site/digest/2026/05/09/",
        window_from_jst="2026-05-08T00:00:00+09:00",
        window_to_jst="2026-05-09T00:00:00+09:00",
        generated_at_jst="2026-05-09T12:00:00+09:00",
        items=[],
        featured_topics=featured_topics,
    )

    assert "今日の注目トピック" in html
    assert "Canvas Learning Platform Hit by Major Data Breach and Outage" in html
    assert "特集Web版" in html
    assert "Major disruption with broad user impact." in html
    assert "Impact: <strong>High</strong>" not in html
    assert "Threat: <strong>Data Breach</strong>" not in html