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
from notifyhub_digest.models import AnalysisResult, FeedItem
from notifyhub_digest.openai_client import analyze_item, load_openai_config
from notifyhub_digest.render import compute_digest_paths, write_article_html, write_index_html, write_manifest
from notifyhub_digest.rss import fetch_feed_entries, iter_enabled_sources
from notifyhub_digest.rules import evaluate_rule
from notifyhub_digest.sanitize import sanitize_summary_html
from notifyhub_digest.sources import load_sources
from notifyhub_digest.store import get_read_store
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
    window_from_jst: datetime
    window_to_jst: datetime
    digest_root_url: str
    items: list[FeedItem]
    succeeded_entry_ids: list[str]
    digest_dir: Path


def build_digest_outputs(
    *,
    out_dir: Path,
    sources_path: Path,
    state_dir: Path,
    run_at_iso: str | None,
) -> BuiltDigest:
    """Generate digest outputs (index/articles/manifest) and return in-memory context.

    Note: This function does NOT update the read store.
    """

    run_at_jst = _parse_run_at(run_at_iso)
    window = compute_daily_window(run_at_jst)

    sources = load_sources(sources_path)
    enabled = list(iter_enabled_sources(sources))

    store = get_read_store(state_dir)

    user_agent = os.getenv("NOTIFYHUB_USER_AGENT", "notifyhub-security-digest/0.1")
    timeout = float(os.getenv("NOTIFYHUB_HTTP_TIMEOUT", "20"))
    retries = int(os.getenv("NOTIFYHUB_HTTP_RETRIES", "2"))

    openai_cfg = load_openai_config()

    items: list[FeedItem] = []
    succeeded_entry_ids: list[str] = []

    transport = httpx.HTTPTransport(retries=retries)
    with httpx.Client(timeout=timeout, follow_redirects=True, transport=transport) as client:
        for src in enabled:
            try:
                entries = fetch_feed_entries(client, src, user_agent=user_agent)
            except Exception as e:
                logger.warning("Fetch failed: %s (%s)", src.name, e)
                continue

            for e in entries:
                if not (window.start_utc <= e.published_at_utc < window.end_utc):
                    continue
                if store.has(e.entry_id):
                    continue

                rule = evaluate_rule(e.title, e.summary)

                analysis = AnalysisResult(summary_html="", technical_terms=[], impact_level="Unknown", threat_type="-")
                if openai_cfg is not None:
                    try:
                        analysis = analyze_item(
                            client,
                            openai_cfg,
                            title=e.title,
                            source_name=src.name,
                            published_at_iso=e.published_at_utc.isoformat(),
                            original_url=e.link,
                            rule_severity=rule.severity,
                            rule_reason=rule.reason,
                        )
                    except Exception as ai_e:
                        logger.warning("OpenAI analysis failed: %s (%s)", e.entry_id, ai_e)
                        analysis = AnalysisResult(
                            summary_html="", technical_terms=[], impact_level="Unknown", threat_type="-"
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
                    rule_severity=rule.severity,
                    rule_reason=rule.reason,
                    analysis=analysis,
                )

                items.append(item)

    # 出力先
    day = run_at_jst.date().isoformat()
    paths = compute_digest_paths(out_dir, day)
    paths.articles_dir.mkdir(parents=True, exist_ok=True)

    digest_base_url = normalize_digest_base_url(os.getenv("DIGEST_BASE_URL", "https://notifyhub.site/digest"))
    digest_root_url = f"{digest_base_url}/{day}/"

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
            generated_at_jst=run_at_jst.isoformat(),
            digest_root_url=digest_root_url,
        )
        succeeded_entry_ids.append(it.entry_id)

    # manifest
    write_manifest(
        paths.digest_dir,
        day=day,
        window_from_iso=window.start_jst.isoformat(),
        window_to_iso=window.end_jst.isoformat(),
        generated_at_iso=run_at_jst.isoformat(),
        items=items,
    )

    return BuiltDigest(
        day=day,
        run_at_jst=run_at_jst,
        window_from_jst=window.start_jst,
        window_to_jst=window.end_jst,
        digest_root_url=digest_root_url,
        items=items,
        succeeded_entry_ids=succeeded_entry_ids,
        digest_dir=paths.digest_dir,
    )


def run_digest(
    *,
    out_dir: Path,
    sources_path: Path,
    state_dir: Path,
    run_at_iso: str | None,
    send_email: bool = False,
) -> None:
    built = build_digest_outputs(out_dir=out_dir, sources_path=sources_path, state_dir=state_dir, run_at_iso=run_at_iso)

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
            generated_at_jst=built.run_at_jst.isoformat(),
            items=built.items,
        )

        if not send_acs_email(cfg=cfg, subject=subject, html_body=html_body):
            logger.error("ACS Email send did not succeed; skipping read-store update")
            return

    # 成功分のみ既読更新
    store = get_read_store(state_dir)
    store.mark_many(built.succeeded_entry_ids)
    store.save()
