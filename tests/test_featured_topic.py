from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from notifyhub_digest.models import AnalysisResult, FeaturedTopic, InformationSource, Source
from notifyhub_digest.rss import RawEntry
from notifyhub_digest.featured_topic import FEATURED_SYSTEM_PROMPT, _build_user_prompt, _infer_category_policy, _looks_mismatched_for_category, _resolve_requested_category, _schema_hint, load_featured_topics_settings
from notifyhub_digest.runner import build_digest_outputs
from notifyhub_digest.timeutils import JST, compute_daily_window


def _patch_digest_sources(monkeypatch, entry: RawEntry) -> None:
    src = Source(name="Digest Source", feed_url="https://example.com/feed", enabled=True)
    monkeypatch.setattr("notifyhub_digest.runner.load_sources", lambda _p: [src])
    monkeypatch.setattr("notifyhub_digest.runner.iter_enabled_sources", lambda sources: list(sources))
    monkeypatch.setattr("notifyhub_digest.runner.fetch_feed_entries", lambda _client, _src, user_agent: [entry])


def test_build_digest_outputs_keeps_existing_items_when_featured_topics_enabled(tmp_path: Path, monkeypatch) -> None:
    run_at_iso = "2026-01-12T06:00:00+09:00"
    window = compute_daily_window(datetime.fromisoformat(run_at_iso))
    digest_entry = RawEntry(
        entry_id="digest-1",
        title="Digest Title",
        link="https://example.com/digest",
        published_at_utc=window.start_utc + timedelta(minutes=1),
        summary="digest summary",
    )
    _patch_digest_sources(monkeypatch, digest_entry)

    featured = [
        FeaturedTopic(
            topic_id="featured-topic-1",
            title="Featured Topic 1",
            source_name="Grok x_search",
            published_at=window.start_utc + timedelta(hours=1),
            original_url="https://example.com/featured-1",
            analysis=AnalysisResult(
                summary_html="<h4>概要</h4><p>featured summary 1</p>",
                technical_terms=[],
                lessons=[],
                impact_level="High",
                impact_reason="featured reason 1",
                threat_type="Advisory",
                model_version="grok-4.3",
            ),
            selection_reason="今日の運用判断にもっとも効くトピックです。",
            requested_category="Advisory",
            information_sources=[
                InformationSource(title="Official blog", url="https://example.com/source-1", source_type="official"),
                InformationSource(title="X post", url="https://x.com/example/status/1", source_type="x"),
            ],
        ),
        FeaturedTopic(
            topic_id="featured-topic-2",
            title="Featured Topic 2",
            source_name="Grok web_search",
            published_at=window.start_utc + timedelta(hours=2),
            original_url="https://example.com/featured-2",
            analysis=AnalysisResult(
                summary_html="<h4>概要</h4><p>featured summary 2</p>",
                technical_terms=[],
                lessons=[],
                impact_level="Medium",
                impact_reason="featured reason 2",
                threat_type="Ransomware",
                model_version="grok-4.3",
            ),
            selection_reason="X とニュース双方で確認できる話題です。",
            requested_category="Ransomware",
            information_sources=[
                InformationSource(title="News article", url="https://example.com/source-2", source_type="news"),
            ],
        ),
    ]

    class _Settings:
        count = 2
        categories = ["Advisory", "Ransomware"]

    monkeypatch.setattr("notifyhub_digest.runner.build_featured_topics", lambda *args, **kwargs: featured)
    monkeypatch.setattr("notifyhub_digest.runner.load_grok_config", lambda: object())
    monkeypatch.setattr("notifyhub_digest.runner.load_featured_topics_settings", lambda: _Settings())

    built = build_digest_outputs(
        out_dir=tmp_path / "out",
        sources_path=tmp_path / "sources.json",
        run_at_iso=run_at_iso,
    )

    assert len(built.items) == 1
    assert built.items[0].entry_id == "digest-1"
    assert len(built.featured_topics) == 2
    assert built.featured_topics[0].title == "Featured Topic 1"

    manifest_path = tmp_path / "out" / "digest" / "2026" / "01" / "12" / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["items"][0]["entry_id"] == "digest-1"
    assert payload["featured_topics_config"]["count"] == 2
    assert payload["featured_topics"][0]["title"] == "Featured Topic 1"
    assert payload["featured_topics"][1]["requested_category"] == "Ransomware"
    assert payload["featured_topics"][0]["information_sources"][0]["source_type"] == "official"
    assert (tmp_path / "out" / "digest" / "2026" / "01" / "12" / "articles" / "featured-topic-1.html").exists()
    assert (tmp_path / "out" / "digest" / "2026" / "01" / "12" / "articles" / "featured-topic-2.html").exists()
    article_html = (tmp_path / "out" / "digest" / "2026" / "01" / "12" / "articles" / "featured-topic-1.html").read_text(encoding="utf-8")
    assert "Official blog" in article_html
    assert "情報ソース" in article_html
    assert article_html.index("📌 キーワード解説") < article_html.index("情報ソース")
    assert article_html.index("深掘り解説") < article_html.index("情報ソース")
    assert "Impact判定理由" not in article_html
    assert "Impact: High" not in article_html
    assert "Advisory</span>" not in article_html
    assert 'class="infoSourceRow"' in article_html


def test_build_digest_outputs_ignores_featured_topic_failures(tmp_path: Path, monkeypatch) -> None:
    run_at_iso = "2026-01-12T06:00:00+09:00"
    window = compute_daily_window(datetime.fromisoformat(run_at_iso))
    digest_entry = RawEntry(
        entry_id="digest-1",
        title="Digest Title",
        link="https://example.com/digest",
        published_at_utc=window.start_utc + timedelta(minutes=1),
        summary="digest summary",
    )
    _patch_digest_sources(monkeypatch, digest_entry)

    class _Settings:
        count = 2
        categories = ["Advisory", "Ransomware"]

    monkeypatch.setattr("notifyhub_digest.runner.build_featured_topics", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("notifyhub_digest.runner.load_grok_config", lambda: object())
    monkeypatch.setattr("notifyhub_digest.runner.load_featured_topics_settings", lambda: _Settings())

    built = build_digest_outputs(
        out_dir=tmp_path / "out",
        sources_path=tmp_path / "sources.json",
        run_at_iso=run_at_iso,
    )

    assert len(built.items) == 1
    assert built.featured_topics == []
    assert (tmp_path / "out" / "digest" / "2026" / "01" / "12" / "manifest.json").exists()


def test_build_digest_outputs_respects_max_items_and_stops_early(tmp_path: Path, monkeypatch) -> None:
    run_at_iso = "2026-01-12T06:00:00+09:00"
    window = compute_daily_window(datetime.fromisoformat(run_at_iso))

    first = Source(name="First", feed_url="https://example.com/first", enabled=True)
    second = Source(name="Second", feed_url="https://example.com/second", enabled=True)
    monkeypatch.setattr("notifyhub_digest.runner.load_sources", lambda _p: [first, second])
    monkeypatch.setattr("notifyhub_digest.runner.iter_enabled_sources", lambda sources: list(sources))

    fetch_calls: list[str] = []

    def _fetch(_client, src, user_agent):
        fetch_calls.append(src.name)
        return [
            RawEntry(
                entry_id=f"{src.name}-1",
                title=f"{src.name} Title",
                link=f"https://example.com/{src.name.lower()}",
                published_at_utc=window.start_utc + timedelta(minutes=1),
                summary="summary",
            )
        ]

    monkeypatch.setattr("notifyhub_digest.runner.fetch_feed_entries", _fetch)
    monkeypatch.setattr("notifyhub_digest.runner.load_grok_config", lambda: None)

    built = build_digest_outputs(
        out_dir=tmp_path / "out",
        sources_path=tmp_path / "sources.json",
        run_at_iso=run_at_iso,
        max_items=1,
    )

    assert len(built.items) == 1
    assert built.items[0].entry_id == "First-1"
    assert fetch_calls == ["First"]


def test_build_digest_outputs_skips_items_and_featured_topics_when_max_items_zero(tmp_path: Path, monkeypatch) -> None:
    run_at_iso = "2026-01-12T06:00:00+09:00"
    src = Source(name="Digest Source", feed_url="https://example.com/feed", enabled=True)
    monkeypatch.setattr("notifyhub_digest.runner.load_sources", lambda _p: [src])
    monkeypatch.setattr("notifyhub_digest.runner.iter_enabled_sources", lambda sources: list(sources))

    fetch_calls: list[str] = []
    featured_calls: list[bool] = []

    monkeypatch.setattr(
        "notifyhub_digest.runner.fetch_feed_entries",
        lambda _client, _src, user_agent: fetch_calls.append("called") or [],
    )
    monkeypatch.setattr("notifyhub_digest.runner.load_grok_config", lambda: object())

    class _Settings:
        count = 2
        categories = ["Advisory", "Ransomware"]

    monkeypatch.setattr("notifyhub_digest.runner.load_featured_topics_settings", lambda: _Settings())
    monkeypatch.setattr(
        "notifyhub_digest.runner.build_featured_topics",
        lambda *args, **kwargs: featured_calls.append(True) or [],
    )

    built = build_digest_outputs(
        out_dir=tmp_path / "out",
        sources_path=tmp_path / "sources.json",
        run_at_iso=run_at_iso,
        max_items=0,
    )

    assert built.items == []
    assert built.featured_topics == []
    assert fetch_calls == []
    assert featured_calls == []


def test_build_user_prompt_adds_generic_tech_trend_guidance() -> None:
    class _Settings:
        count = 3
        categories = ["Cybersecurity", "AI", "cloud platform trends"]

    prompt = _build_user_prompt(
        window_start_utc=datetime(2026, 5, 8, 0, 0),
        window_end_utc=datetime(2026, 5, 9, 0, 0),
        settings=_Settings(),
    )

    assert "cloud platform trends" in prompt
    assert "クラウド、開発者ツール、半導体、AI基盤" in prompt
    assert "サイバー攻撃・脆弱性・情報漏えい・インシデント対応そのものは原則として選ばない" in prompt


def test_build_user_prompt_adds_generic_guidance_for_arbitrary_categories() -> None:
    class _Settings:
        count = 3
        categories = ["教育", "宗教", "健康政策"]

    prompt = _build_user_prompt(
        window_start_utc=datetime(2026, 5, 8, 0, 0),
        window_end_utc=datetime(2026, 5, 9, 0, 0),
        settings=_Settings(),
    )

    assert "categories は固定候補ではなく自由なテーマ名として解釈" in prompt
    assert "requested_category には指定されたカテゴリ名をそのまま使う" in prompt
    assert "カテゴリ「教育」では、その分野そのものの動き" in prompt
    assert "カテゴリ「宗教」では、その分野そのものの動き" in prompt
    assert "カテゴリ「健康政策」では、その分野そのものの動き" in prompt


def test_generic_tech_trend_category_rejects_cybersecurity_incident_topics() -> None:
    assert _looks_mismatched_for_category(
        requested_category="cloud platform trends",
        threat_type="Data Breach",
        title="Canvas LMS大規模侵害",
        summary_html="<h4>概要</h4><p>学生データ漏えいとサイバー攻撃の話題</p>",
    )

    assert not _looks_mismatched_for_category(
        requested_category="cloud platform trends",
        threat_type="Other",
        title="新しいクラウドネイティブ開発基盤が登場",
        summary_html="<h4>概要</h4><p>開発者ツールとAI基盤の最新動向をまとめる</p>",
    )


def test_infer_category_policy_is_not_hardcoded_to_one_literal() -> None:
    policy = _infer_category_policy("enterprise IT technology trends")
    assert policy.exclude_cybersecurity_incidents is True
    assert "技術トレンド" in policy.guidance

    security_policy = _infer_category_policy("product security updates")
    assert security_policy.exclude_cybersecurity_incidents is False
    assert "セキュリティ実務" in security_policy.guidance


def test_infer_category_policy_avoids_substring_false_positives() -> None:
    policy = _infer_category_policy("retail operations")
    assert "カテゴリ「retail operations」" in policy.guidance
    assert policy.exclude_cybersecurity_incidents is False


def test_load_featured_topics_settings_dedupes_arbitrary_categories(monkeypatch) -> None:
    monkeypatch.setenv("FEATURED_TOPIC_COUNT", "3")
    monkeypatch.setenv("FEATURED_TOPIC_CATEGORIES", "教育, health policy, 教育, Health-Policy")

    settings = load_featured_topics_settings()

    assert settings.count == 3
    assert settings.categories == ["教育", "health policy"]


def test_resolve_requested_category_maps_back_to_requested_label() -> None:
    resolved = _resolve_requested_category("Health-Policy", ["教育", "health policy"])

    assert resolved == "health policy"


def test_featured_topic_prompts_require_plain_japanese_style() -> None:
    assert "常体" in FEATURED_SYSTEM_PROMPT
    assert "です・ます調" in FEATURED_SYSTEM_PROMPT
    assert "「〜だ」「〜である」で終えない" in FEATURED_SYSTEM_PROMPT
    assert "速報性、影響度、話題性" in FEATURED_SYSTEM_PROMPT
    assert "英語の文や英語だけの箇条書きは禁止" in FEATURED_SYSTEM_PROMPT
    assert "article title に相当する title を除き" in FEATURED_SYSTEM_PROMPT

    schema_hint = _schema_hint(1)
    assert "summary_html・lessons.body は常体" in schema_hint
    assert "です・ます調" in schema_hint
    assert "requested_category は categories に指定された文字列をそのまま使う" in schema_hint
    assert "article title に相当する title を除き、すべての出力項目は日本語で書く" in schema_hint
    assert "technical_terms（用語解説）ルール" in schema_hint
    assert "「〜だ」「〜である」で終えない" in schema_hint
    assert "同じ用語が過去にも使われていることを前提" in schema_hint
    assert "lessons（深掘り解説）ルール" in schema_hint
    assert "title=なし, body=なし" in schema_hint
    assert "4〜6文で書く" in schema_hint
    assert "最大8箇所" in schema_hint
    assert "summary_html 全体で500〜800文字程度" in schema_hint


def test_featured_topic_prompts_discourage_repetitive_trend_reports() -> None:
    assert "同じ発信元、同じ企業・団体、同じ調査レポート、同じ年次予測" in FEATURED_SYSTEM_PROMPT
    assert "Gartner などの調査会社による IT トレンド予測" in FEATURED_SYSTEM_PROMPT
    assert "日次ダイジェスト全体の変化" in FEATURED_SYSTEM_PROMPT

    class _Settings:
        count = 1
        categories = ["IT trends"]

    prompt = _build_user_prompt(
        window_start_utc=datetime(2026, 5, 8, 0, 0),
        window_end_utc=datetime(2026, 5, 9, 0, 0),
        settings=_Settings(),
    )

    assert "同種の調査会社レポートや年次予測だけに寄せない" in prompt
    assert "Gartner の IT トレンド予測" in prompt
    assert "製品発表、規制変更、研究成果、導入事例" in prompt
    assert "新規性・具体性・分野の多様性" in prompt
