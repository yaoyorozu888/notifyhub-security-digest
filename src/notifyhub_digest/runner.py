from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from notifyhub_digest.acs_email import (
    build_digest_email_html,
    build_digest_email_subject,
    load_acs_email_config,
    normalize_digest_base_url,
    require_acs_email_env,
    send_acs_email,
    should_send_email,
)
from notifyhub_digest.featured_topic import (
    build_featured_topics,
    load_featured_topics_settings,
    load_grok_config,
)
from notifyhub_digest.models import AnalysisResult, FeedItem, FeaturedTopic
from notifyhub_digest.openai_client import analyze_item, load_openai_config
from notifyhub_digest.render import (
    _information_sources_html,
    compute_digest_url_path,
    compute_digest_paths,
    write_article_html,
    write_calendar_page,
    write_digest_landing_pages,
    write_index_html,
    write_manifest,
)
from notifyhub_digest.rss import fetch_feed_entries, iter_enabled_sources
from notifyhub_digest.sanitize import sanitize_summary_html
from notifyhub_digest.sources import load_sources
from notifyhub_digest.timeutils import JST, compute_daily_window


logger = logging.getLogger(__name__)


def _parse_run_at(run_at_iso: str | None) -> datetime:
    if not run_at_iso:
        return datetime.now(tz=JST)

    dt = datetime.fromisoformat(run_at_iso)
    if dt.tzinfo is None:
        # 仕様上JST前提の入力が多いので、naiveはJST扱い
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


@dataclass(frozen=True)
class BuiltDigest:
    day: str
    run_at_jst: datetime
    generated_at_jst: datetime
    window_from_jst: datetime
    window_to_jst: datetime
    digest_root_url: str
    items: list[FeedItem]
    featured_topics: list[FeaturedTopic]
    digest_dir: Path


def build_digest_outputs(
    *,
    out_dir: Path,
    sources_path: Path,
    run_at_iso: str | None,
    max_items: int | None = None,
) -> BuiltDigest:
    """Generate digest outputs (index/articles/manifest) and return in-memory context."""

    run_at_jst = _parse_run_at(run_at_iso)
    window = compute_daily_window(run_at_jst)

    sources = load_sources(sources_path)
    enabled = list(iter_enabled_sources(sources))

    user_agent = os.getenv("NOTIFYHUB_USER_AGENT", "notifyhub-security-digest/0.1")
    timeout = float(os.getenv("NOTIFYHUB_HTTP_TIMEOUT", "20"))
    retries = int(os.getenv("NOTIFYHUB_HTTP_RETRIES", "2"))

    openai_cfg = load_openai_config()
    grok_cfg = load_grok_config()
    featured_settings = load_featured_topics_settings()

    if max_items is not None and max_items < 0:
        raise ValueError("max_items must be >= 0")

    items: list[FeedItem] = []
    featured_topics: list[FeaturedTopic] = []

    transport = httpx.HTTPTransport(retries=retries)
    with httpx.Client(timeout=timeout, follow_redirects=True, transport=transport) as client:
        if max_items != 0:
            for src in enabled:
                if max_items is not None and len(items) >= max_items:
                    break

                try:
                    entries = fetch_feed_entries(client, src, user_agent=user_agent)
                except Exception as e:
                    logger.warning("Fetch failed: %s (%s)", src.name, e)
                    continue

                for e in entries:
                    if max_items is not None and len(items) >= max_items:
                        break

                    if not (window.start_utc <= e.published_at_utc < window.end_utc):
                        continue

                    analysis = AnalysisResult(summary_html="", technical_terms=[], impact_level="Unknown", threat_type="Unknown")
                    if openai_cfg is not None:
                        try:
                            analysis = analyze_item(
                                client,
                                openai_cfg,
                                title=e.title,
                                source_name=src.name,
                                published_at_iso=e.published_at_utc.isoformat(),
                                original_url=e.link,
                                summary=e.summary,
                            )
                        except Exception as ai_e:
                            logger.warning("OpenAI analysis failed: %s (%s)", e.entry_id, ai_e)
                            analysis = AnalysisResult(
                                summary_html="", technical_terms=[], impact_level="Unknown", threat_type="Unknown"
                            )

                    # summary_htmlは必ずサニタイズ（仕様必須）
                    analysis.summary_html = sanitize_summary_html(analysis.summary_html)

                    item = FeedItem(
                        entry_id=e.entry_id,
                        title=e.title,
                        source_name=src.name,
                        category=src.category,
                        published_at=e.published_at_utc,
                        original_url=e.link,
                        analysis=analysis,
                    )

                    items.append(item)

        if max_items != 0 and grok_cfg is not None and featured_settings.count > 0:
            try:
                featured_topics = build_featured_topics(
                    client,
                    cfg=grok_cfg,
                    window_start_utc=window.start_utc,
                    window_end_utc=window.end_utc,
                    settings=featured_settings,
                )
                for topic in featured_topics:
                    topic.analysis.summary_html = sanitize_summary_html(topic.analysis.summary_html)
            except Exception as exc:
                logger.warning("Featured topic generation failed: %s", exc)
                featured_topics = []

    generated_at_jst = datetime.now(tz=JST)

    # 出力先
    day = run_at_jst.date().isoformat()
    paths = compute_digest_paths(out_dir, day)
    paths.articles_dir.mkdir(parents=True, exist_ok=True)

    digest_base_url = normalize_digest_base_url(os.getenv("DIGEST_BASE_URL", "https://notifyhub.site/digest"))
    digest_root_url = f"{digest_base_url}/{compute_digest_url_path(day)}/"

    template_dir = Path(__file__).resolve().parent / "templates"
    index_tpl = template_dir / "index.html"
    article_tpl = template_dir / "article.html"

    # index
    write_index_html(index_tpl, paths.digest_dir)

    # articles
    for it in items:
        write_article_html(
            article_tpl,
            paths.digest_dir,
            it,
            window_from_jst=window.start_jst.isoformat(),
            window_to_jst=window.end_jst.isoformat(),
            generated_at_jst=generated_at_jst.isoformat(),
            digest_root_url=digest_root_url,
        )

    for featured_topic in featured_topics:
        write_article_html(
            article_tpl,
            paths.digest_dir,
            featured_topic.as_feed_item(),
            window_from_jst=window.start_jst.isoformat(),
            window_to_jst=window.end_jst.isoformat(),
            generated_at_jst=generated_at_jst.isoformat(),
            digest_root_url=digest_root_url,
            selection_reason=featured_topic.selection_reason,
            information_sources_html=_information_sources_html(featured_topic.information_sources),
        )

    # manifest
    write_manifest(
        paths.digest_dir,
        day=day,
        window_from_iso=window.start_jst.isoformat(),
        window_to_iso=window.end_jst.isoformat(),
        generated_at_iso=generated_at_jst.isoformat(),
        items=items,
        featured_topics=featured_topics,
        featured_topics_config={
            "count": featured_settings.count,
            "categories": featured_settings.categories,
        },
    )

    # landing pages (/, /digest/, /digest/latest/)
    write_digest_landing_pages(out_dir, day=day)
    write_calendar_page(out_dir, default_day=day)

    return BuiltDigest(
        day=day,
        run_at_jst=run_at_jst,
        generated_at_jst=generated_at_jst,
        window_from_jst=window.start_jst,
        window_to_jst=window.end_jst,
        digest_root_url=digest_root_url,
        items=items,
        featured_topics=featured_topics,
        digest_dir=paths.digest_dir,
    )


def run_digest(
    *,
    out_dir: Path,
    sources_path: Path,
    run_at_iso: str | None,
    send_email: bool = False,
    max_items: int | None = None,
) -> None:
    built = build_digest_outputs(
        out_dir=out_dir,
        sources_path=sources_path,
        run_at_iso=run_at_iso,
        max_items=max_items,
    )

    # Email送信（有効時は送信成功時のみ既読更新）
    if should_send_email(send_email):
        require_acs_email_env()
        cfg = load_acs_email_config()
        if cfg is None:
            raise RuntimeError("ACS Email config is invalid")

        logger.info("Sending digest email: day=%s items=%d", built.day, len(built.items))
        subject = build_digest_email_subject(day=built.day)
        html_body = build_digest_email_html(
            day=built.day,
            digest_root_url=built.digest_root_url,
            window_from_jst=built.window_from_jst.isoformat(),
            window_to_jst=built.window_to_jst.isoformat(),
            generated_at_jst=built.generated_at_jst.isoformat(),
            items=built.items,
            featured_topics=built.featured_topics,
        )

        if not send_acs_email(cfg=cfg, subject=subject, html_body=html_body):
            logger.error("ACS Email send did not succeed")
            return

    return
