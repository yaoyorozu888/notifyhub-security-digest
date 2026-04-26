from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date
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


def _lesson_cards_html(item: FeedItem) -> str:
    lessons = item.analysis.lessons or []
    if not lessons:
        lessons = [{"title": "なし", "body": "なし"}]  # type: ignore[assignment]

    parts: list[str] = []
    for lesson in lessons:
        title = html.escape(getattr(lesson, "title", "なし"))
        body = html.escape(getattr(lesson, "body", "なし"))
        body_html = f'  <p class="exp">{body}</p>'
        if title == "なし" and body == "なし":
            body_html = ""
        parts.append(
            "\n".join(
                [
                    '<div class="termCard deepDiveCard">',
                    f'  <div class="term">{title}</div>',
                    body_html,
                    "</div>",
                ]
            )
        )
    return "\n".join(parts)


def _analysis_heading(item: FeedItem) -> str:
    model_version = (item.analysis.model_version or "").strip()
    if not model_version:
        return "要約（ChatGPT）"
    return f"要約（ChatGPT / {html.escape(model_version)}）"


@dataclass(frozen=True)
class DigestPaths:
    digest_dir: Path
    articles_dir: Path


def compute_digest_url_path(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.year:04d}/{d.month:02d}/{d.day:02d}"


def compute_digest_paths(out_dir: Path, day: str) -> DigestPaths:
    digest_rel = compute_digest_url_path(day).split("/")
    digest_dir = (out_dir / "digest" / digest_rel[0] / digest_rel[1] / digest_rel[2]).resolve()
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
        "impact_reason": html.escape(item.analysis.impact_reason),
        "threat_type": html.escape(item.analysis.threat_type),
        "analysis_heading": _analysis_heading(item),
        # summary_htmlは既にサニタイズ済み前提（属性禁止/許可タグのみ）
        "summary_html": item.analysis.summary_html,
        "technical_terms_html": _term_cards_html(item),
        "deep_dive_html": _lesson_cards_html(item),
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
            '  <link rel="icon" type="image/png" sizes="32x32" href="/assets/icons/favicon-32.png" />',
            '  <link rel="icon" type="image/png" sizes="16x16" href="/assets/icons/favicon-16.png" />',
            '  <link rel="apple-touch-icon" sizes="180x180" href="/assets/icons/apple-touch-icon.png" />',
            '  <link rel="icon" type="image/png" sizes="192x192" href="/assets/icons/favicon-192.png" />',
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

    digest_url_path = compute_digest_url_path(day)

    # Root landing -> /digest/<yyyy>/<mm>/<dd>/
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        _redirect_html(title="NotifyHub Cybersecurity Daily Report", href=f"./digest/{digest_url_path}/"), encoding="utf-8"
    )

    # /digest/ landing -> /digest/<yyyy>/<mm>/<dd>/
    digest_root = (out_dir / "digest").resolve()
    if out_dir not in digest_root.parents and digest_root != out_dir:
        raise ValueError("Invalid out_dir")
    digest_root.mkdir(parents=True, exist_ok=True)
    (digest_root / "index.html").write_text(
        _redirect_html(title="NotifyHub Cybersecurity Daily Report", href=f"./{digest_url_path}/"), encoding="utf-8"
    )

    # Stable permalink: /digest/latest/ -> /digest/<yyyy>/<mm>/<dd>/
    latest_dir = (digest_root / "latest").resolve()
    if digest_root not in latest_dir.parents and latest_dir != digest_root:
        raise ValueError("Invalid out_dir")
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "index.html").write_text(
        _redirect_html(title="NotifyHub Cybersecurity Daily Report", href=f"../{digest_url_path}/"), encoding="utf-8"
    )


def _calendar_html(*, default_day: str) -> str:
    safe_day = html.escape(default_day, quote=True)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="ja">',
            "<head>",
            '  <meta charset="utf-8" />',
            '  <meta name="viewport" content="width=device-width,initial-scale=1" />',
            '  <meta name="robots" content="noindex,nofollow" />',
            '  <link rel="icon" type="image/png" sizes="32x32" href="/assets/icons/favicon-32.png" />',
            '  <link rel="icon" type="image/png" sizes="16x16" href="/assets/icons/favicon-16.png" />',
            '  <link rel="apple-touch-icon" sizes="180x180" href="/assets/icons/apple-touch-icon.png" />',
            '  <link rel="icon" type="image/png" sizes="192x192" href="/assets/icons/favicon-192.png" />',
            '  <link rel="stylesheet" href="/pagefind/pagefind-ui.css" />',
            '  <script src="/pagefind/pagefind-ui.js"></script>',
            "  <title>NotifyHub Calendar</title>",
            "  <style>",
            "    :root{color-scheme:dark}",
            "    body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,\"Segoe UI\",\"Noto Sans JP\",sans-serif;background:#0b1020;color:#e7ecff}",
            "    .wrap{max-width:940px;margin:0 auto;padding:28px 18px 36px}",
            "    .panel{border:1px solid #22305b;background:#0f1730;border-radius:18px;padding:22px}",
            "    h1{margin:0 0 8px;font-size:22px}",
            "    p{margin:0;color:#a9b4df;line-height:1.6}",
            "    .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:18px}",
            "    input[type=date]{background:#0b1020;color:#e7ecff;border:1px solid #22305b;border-radius:12px;padding:10px 12px}",
            "    button,a{display:inline-flex;align-items:center;justify-content:center;padding:9px 12px;border-radius:12px;border:1px solid #22305b;background:#121c3a;color:#e7ecff;text-decoration:none}",
            "    button:hover,a:hover{border-color:#7aa2ff}",
            "    .tiny{margin-top:14px;font-size:12px;color:#a9b4df}",
            "    .grid{display:grid;grid-template-columns:1fr;gap:14px;margin-top:16px}",
            "    .card{border:1px solid #22305b;background:#121c3a;border-radius:14px;padding:14px}",
            "    .card h2{margin:0 0 8px;font-size:15px}",
            "    .path{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#e7ecff;background:#0b1020;border:1px solid #22305b;border-radius:10px;padding:8px 10px;font-size:12px;word-break:break-all}",
            "    .hero{margin-top:8px}",
            "    .searchCard{margin-top:16px;border:1px solid #2a3d73;background:#121c3a;border-radius:14px;padding:14px}",
            "    .searchTitle{margin:0 0 8px;font-size:15px}",
            "    #searchMsg{margin-top:8px;color:#a9b4df;font-size:12px}",
            "    .pagefind-ui__search-input{background:#0b1020!important;color:#e7ecff!important;border:1px solid #22305b!important}",
            "  </style>",
            "</head>",
            "<body>",
            '  <div class="wrap">',
            '    <div class="panel">',
            "      <h1>📅 NotifyHub Calendar</h1>",
            '      <p class="hero">日付を選択して、対象日のセキュリティダイジェストへ移動できます。</p>',
            '      <div class="row">',
            f'        <input id="day" type="date" value="{safe_day}" />',
            '        <button id="go" type="button">この日を開く</button>',
            '        <a href="/digest/latest/">最新を開く</a>',
            "      </div>",
            '      <div class="tiny" id="msg"></div>',
            '      <div class="grid">',
            '        <div class="card">',
            '          <h2>URL形式</h2>',
            '          <p class="tiny" style="margin:0 0 8px;">日次ページは以下の構造で公開されます。</p>',
            '          <div class="path">/digest/YYYY/MM/DD/</div>',
            '          <p class="tiny" style="margin-top:10px;">例（既定日）</p>',
            f'          <div class="path">/digest/{safe_day.replace("-", "/")}/</div>',
            '        </div>',
            '      </div>',
            '      <div class="searchCard">',
            '        <h2 class="searchTitle">サイト全体を検索</h2>',
            '        <p class="tiny" style="margin:0 0 10px;">ダイジェスト本文と記事ページを横断検索できます。</p>',
            '        <div id="search"></div>',
            '        <div id="searchMsg"></div>',
            "      </div>",
            "    </div>",
            "  </div>",
            "  <script>",
            "    const input = document.getElementById('day');",
            "    const msg = document.getElementById('msg');",
            "    const searchMsg = document.getElementById('searchMsg');",
            "    const go = () => {",
            "      const v = input.value;",
            "      if(!v){ msg.textContent = '日付を選択してください。'; return; }",
            "      const [y,m,d] = v.split('-');",
            "      if(!y || !m || !d){ msg.textContent = '日付形式が不正です。'; return; }",
            "      location.href = `/digest/${y}/${m}/${d}/`;",
            "    };",
            "    document.getElementById('go').addEventListener('click', go);",
            "    input.addEventListener('keydown', (e) => { if(e.key === 'Enter') go(); });",
            "    const addStylesheet = (href) => {",
            "      if (document.querySelector(`link[data-pagefind=\"${href}\"]`)) return;",
            "      const link = document.createElement('link');",
            "      link.rel = 'stylesheet';",
            "      link.href = href;",
            "      link.dataset.pagefind = href;",
            "      document.head.appendChild(link);",
            "    };",
            "    const loadScript = (src) => new Promise((resolve, reject) => {",
            "      const existing = document.querySelector(`script[data-pagefind=\"${src}\"]`);",
            "      if (existing) {",
            "        if (existing.dataset.loaded === 'true') { resolve(); return; }",
            "        existing.addEventListener('load', () => resolve(), { once: true });",
            "        existing.addEventListener('error', () => reject(new Error('load failed')), { once: true });",
            "        return;",
            "      }",
            "      const script = document.createElement('script');",
            "      script.src = src;",
            "      script.dataset.pagefind = src;",
            "      script.addEventListener('load', () => { script.dataset.loaded = 'true'; resolve(); }, { once: true });",
            "      script.addEventListener('error', () => reject(new Error('load failed')), { once: true });",
            "      document.head.appendChild(script);",
            "    });",
            "    const bundleCandidates = ['../pagefind/', '/pagefind/', '/site/pagefind/'];",
            "    const initPagefind = async () => {",
            "      if (window.PagefindUI) {",
            "        return '/pagefind/';",
            "      }",
            "      for (const bundlePath of bundleCandidates) {",
            "        try {",
            "          addStylesheet(`${bundlePath}pagefind-ui.css`);",
            "          await loadScript(`${bundlePath}pagefind-ui.js`);",
            "          if (window.PagefindUI) return bundlePath;",
            "        } catch (_) {",
            "          // try next candidate path",
            "        }",
            "      }",
            "      return null;",
            "    };",
            "    document.addEventListener('DOMContentLoaded', async () => {",
            "      const bundlePath = await initPagefind();",
            "      if (!bundlePath || !window.PagefindUI) {",
            "        searchMsg.textContent = '検索インデックスを読み込めませんでした。後でもう一度お試しください。';",
            "        return;",
            "      }",
            "      new PagefindUI({",
            "        element: '#search',",
            "        bundlePath,",
            "        showImages: false,",
            "        resetStyles: false,",
            "        placeholder: 'キーワードで検索（例: ランサムウェア, CVE）'",
            "      });",
            "      searchMsg.textContent = '検索対象: サイト全体（digest と articles）';",
            "    });",
            "  </script>",
            "</body>",
            "</html>",
            "",
        ]
    )


def write_calendar_page(out_dir: Path, *, default_day: str) -> None:
    out_root = out_dir.resolve()
    calendar_dir = (out_root / "calendar").resolve()
    if out_root not in calendar_dir.parents and calendar_dir != out_root:
        raise ValueError("Invalid out_dir")
    calendar_dir.mkdir(parents=True, exist_ok=True)
    (calendar_dir / "index.html").write_text(_calendar_html(default_day=default_day), encoding="utf-8")
