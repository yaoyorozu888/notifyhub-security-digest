from __future__ import annotations

from datetime import datetime

from notifyhub_digest.models import AnalysisResult, FeedItem, Lesson
from notifyhub_digest.render import write_article_html
from notifyhub_digest.timeutils import JST


def _feed_item(*, lessons: list[Lesson]) -> FeedItem:
    return FeedItem(
        entry_id="entry-1",
        title="Sample Title",
        source_name="Sample Source",
        category="reporting",
        published_at=datetime(2026, 4, 24, 9, 0, tzinfo=JST),
        original_url="https://example.com/article",
        analysis=AnalysisResult(
            summary_html="<h4>概要</h4><p>summary</p>",
            technical_terms=[],
            lessons=lessons,
            impact_level="High",
            impact_reason="important reason",
            threat_type="Vulnerability",
            model_version="gpt-5.4-2026-04-20",
        ),
    )


def test_write_article_html_renders_deep_dive(tmp_path) -> None:
    template_path = tmp_path / "article.html"
    output_dir = tmp_path / "out"
    template_path.write_text(
        "<html><body><section>{{technical_terms_html}}</section><section>{{deep_dive_html}}</section></body></html>",
        encoding="utf-8",
    )

    item: FeedItem = _feed_item(lessons=[Lesson(title="HTTPS証明書", body="例えばブラウザは証明書チェーンをたどって正当性を確認する。")])
    write_article_html(
        template_path,
        output_dir,
        item,
        window_from_jst="2026-04-24T00:00:00+09:00",
        window_to_jst="2026-04-25T00:00:00+09:00",
        generated_at_jst="2026-04-24T08:00:00+09:00",
        digest_root_url="https://notifyhub.site/digest/2026/04/24/",
    )

    content = (output_dir / "articles" / "entry-1.html").read_text(encoding="utf-8")
    assert "HTTPS証明書" in content
    assert "深掘り解説" not in content


def test_write_article_html_renders_none_when_lessons_empty(tmp_path) -> None:
    template_path = tmp_path / "article.html"
    output_dir = tmp_path / "out"
    template_path.write_text("<html><body>{{deep_dive_html}}</body></html>", encoding="utf-8")

    item: FeedItem = _feed_item(lessons=[])
    write_article_html(
        template_path,
        output_dir,
        item,
        window_from_jst="2026-04-24T00:00:00+09:00",
        window_to_jst="2026-04-25T00:00:00+09:00",
        generated_at_jst="2026-04-24T08:00:00+09:00",
        digest_root_url="https://notifyhub.site/digest/2026/04/24/",
    )

    content = (output_dir / "articles" / "entry-1.html").read_text(encoding="utf-8")
    assert "なし" in content
    assert content.count("なし") == 1


def test_write_article_html_renders_model_version_in_summary_heading(tmp_path) -> None:
    template_path = tmp_path / "article.html"
    output_dir = tmp_path / "out"
    template_path.write_text("<html><body><h2>{{analysis_heading}}</h2>{{summary_html}}</body></html>", encoding="utf-8")

    item: FeedItem = _feed_item(lessons=[])
    write_article_html(
        template_path,
        output_dir,
        item,
        window_from_jst="2026-04-24T00:00:00+09:00",
        window_to_jst="2026-04-25T00:00:00+09:00",
        generated_at_jst="2026-04-24T08:00:00+09:00",
        digest_root_url="https://notifyhub.site/digest/2026/04/24/",
    )

    content = (output_dir / "articles" / "entry-1.html").read_text(encoding="utf-8")
    assert "要約（ChatGPT / gpt-5.4-2026-04-20）" in content