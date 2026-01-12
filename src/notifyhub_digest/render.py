from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from notifyhub_digest.models import FeedItem
from notifyhub_digest.timeutils import JST


def _safe_url(url: str) -> str:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return "#"
        return url
    except Exception:
        return "#"


def _load_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _render_template(template: str, mapping: dict[str, str]) -> str:
    out = template
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", v)
    return out


_TAG_RE = re.compile(r"<[^>]+>")


def _summary_preview(summary_html: str, *, max_len: int = 180) -> str:
    text = _TAG_RE.sub("", summary_html or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _term_cards_html(item: FeedItem) -> str:
    parts: list[str] = []
    for t in item.analysis.technical_terms:
        term = html.escape(t.term)
        exp = html.escape(t.explanation)
        parts.append(
            "\n".join(
                [
                    '<div class="termCard">',
                    f'  <div class="term">{term}</div>',
                    f'  <p class="exp">{exp}</p>',
                    "</div>",
                ]
            )
        )
    return "\n".join(parts)


@dataclass(frozen=True)
class DigestPaths:
    digest_dir: Path
    articles_dir: Path


def compute_digest_paths(out_dir: Path, day: str) -> DigestPaths:
    digest_dir = (out_dir / "digest" / day).resolve()
    articles_dir = (digest_dir / "articles").resolve()
    # out_dir外への書き出しを防ぐ（path traversal等）
    out_root = out_dir.resolve()
    if out_root not in digest_dir.parents and digest_dir != out_root:
        raise ValueError("Invalid out_dir")
    return DigestPaths(digest_dir=digest_dir, articles_dir=articles_dir)


def write_manifest(
    digest_dir: Path,
    *,
    day: str,
    window_from_iso: str,
    window_to_iso: str,
    generated_at_iso: str,
    items: list[FeedItem],
) -> None:
    payload = {
        "date": day,
        "window": {"from": window_from_iso, "to": window_to_iso},
        "counts": {"total": len(items)},
        "generated_at_jst": generated_at_iso,
        "items": [
            {
                "entry_id": it.entry_id,
                "title": it.title,
                "source_name": it.source_name,
                "category": it.category,
                "published_at": it.published_at.isoformat(),
                "rule_severity": it.rule_severity,
                "impact_level": it.analysis.impact_level,
                "threat_type": it.analysis.threat_type,
                "summary_preview": _summary_preview(it.analysis.summary_html),
                "article_path": it.article_path,
                "original_url": it.original_url,
            }
            for it in items
        ],
    }
    (digest_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8"
    )


def write_index_html(template_path: Path, digest_dir: Path) -> None:
    # index.htmlはmanifest.jsonをfetchするだけなので、そのままコピー
    digest_dir.mkdir(parents=True, exist_ok=True)
    (digest_dir / "index.html").write_text(_load_template(template_path), encoding="utf-8")


def write_article_html(
    template_path: Path,
    digest_dir: Path,
    item: FeedItem,
    *,
    window_from_jst: str,
    window_to_jst: str,
    generated_at_jst: str,
    digest_root_url: str,
) -> None:
    digest_index_path = "../index.html"
    mapping = {
        "digest_index_path": html.escape(digest_index_path),
        "digest_root_url": html.escape(_safe_url(digest_root_url)),
        "entry_id": html.escape(item.entry_id),
        "title": html.escape(item.title),
        "source_name": html.escape(item.source_name),
        "published_at_jst": html.escape(item.published_at.astimezone(JST).isoformat()),
        "original_url": html.escape(_safe_url(item.original_url)),
        "impact_level": html.escape(item.analysis.impact_level),
        "rule_severity": html.escape(item.rule_severity),
        "threat_type": html.escape(item.analysis.threat_type),
        "rule_reason": html.escape(item.rule_reason),
        # summary_htmlは既にサニタイズ済み前提（属性禁止/許可タグのみ）
        "summary_html": item.analysis.summary_html,
        "technical_terms_html": _term_cards_html(item),
        "window_from_jst": html.escape(window_from_jst),
        "window_to_jst": html.escape(window_to_jst),
        "generated_at_jst": html.escape(generated_at_jst),
    }

    template = _load_template(template_path)
    rendered = _render_template(template, mapping)
    out_path = (digest_dir / item.article_path).resolve()
    # digest_dir外への書き出し防止
    if digest_dir.resolve() not in out_path.parents:
        raise ValueError("Invalid article output path")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")


def _redirect_html(*, title: str, href: str) -> str:
    safe_title = html.escape(title)
    safe_href = html.escape(href, quote=True)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="ja">',
            "<head>",
            '  <meta charset="utf-8" />',
            '  <meta name="viewport" content="width=device-width,initial-scale=1" />',
            "  <meta name=\"robots\" content=\"noindex,nofollow\" />",
            f"  <title>{safe_title}</title>",
            f'  <meta http-equiv="refresh" content="0; url={safe_href}" />',
            "  <style>",
            "    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,\"Noto Sans JP\",sans-serif; padding:24px}",
            "    a{color:#2563eb}",
            "  </style>",
            "</head>",
            "<body>",
            f"  <p>Redirecting to <a href=\"{safe_href}\">{safe_href}</a> ...</p>",
            "  <script>",
            f"    location.replace(\"{safe_href}\");",
            "  </script>",
            "</body>",
            "</html>",
            "",
        ]
    )


def write_digest_landing_pages(out_dir: Path, *, day: str) -> None:
    """Write landing pages so `/` and `/digest/` work.

    The daily report lives at `/digest/<day>/`. Without these, users who open
    `/` or `/digest/` may land on a page that cannot fetch `manifest.json`.
    """

    out_dir = out_dir.resolve()

    # Root landing -> /digest/<day>/
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        _redirect_html(title="CSIRT 日次レポート", href=f"./digest/{day}/"), encoding="utf-8"
    )

    # /digest/ landing -> /digest/<day>/
    digest_root = (out_dir / "digest").resolve()
    if out_dir not in digest_root.parents and digest_root != out_dir:
        raise ValueError("Invalid out_dir")
    digest_root.mkdir(parents=True, exist_ok=True)
    (digest_root / "index.html").write_text(
        _redirect_html(title="CSIRT 日次レポート", href=f"./{day}/"), encoding="utf-8"
    )

    # Stable permalink: /digest/latest/ -> /digest/<day>/
    latest_dir = (digest_root / "latest").resolve()
    if digest_root not in latest_dir.parents and latest_dir != digest_root:
        raise ValueError("Invalid out_dir")
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "index.html").write_text(
        _redirect_html(title="CSIRT 日次レポート", href=f"../{day}/"), encoding="utf-8"
    )
