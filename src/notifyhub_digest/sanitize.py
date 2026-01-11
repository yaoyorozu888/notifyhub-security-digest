from __future__ import annotations

import bleach

_ALLOWED_TAGS = ["p", "ul", "ol", "li", "strong", "h4", "code", "br", "div"]


def sanitize_summary_html(summary_html: str) -> str:
    """仕様: 許可タグのみ、属性はすべて禁止。

    失敗時は固定メッセージに置換。
    """

    fallback = "<p>分析に失敗しました（HTML検証エラー）</p>"
    raw = (summary_html or "").strip()
    if not raw:
        return fallback

    try:
        cleaned = bleach.clean(
            raw,
            tags=_ALLOWED_TAGS,
            attributes={},
            protocols=[],
            strip=True,
        ).strip()

        # 仕様: 許可タグ以外や属性が含まれていた場合は「検証エラー」として扱い、固定文言に置換。
        # （OpenAI出力は必ずサーバ側で安全検証する前提）
        if cleaned != raw:
            return fallback

        return cleaned if cleaned else fallback
    except Exception:
        return fallback
