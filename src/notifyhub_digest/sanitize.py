from __future__ import annotations

import bleach

_ALLOWED_TAGS = ["p", "ul", "ol", "li", "strong", "h4", "code", "br", "div"]


def sanitize_summary_html(summary_html: str) -> str:
    """仕様: 許可タグのみ、属性はすべて禁止。

    失敗時は固定メッセージに置換。
    """

    fallback = ""
    raw = (summary_html or "").strip()
    if not raw:
        return ""

    try:
        cleaned = bleach.clean(
            raw,
            tags=_ALLOWED_TAGS,
            attributes={},
            protocols=[],
            strip=True,
        ).strip()

        # Allowlist sanitization is sufficient; accept cleaned output to avoid
        # unnecessary fallbacks caused by minor formatting differences.
        return cleaned if cleaned else ""
    except Exception:
        return fallback
