from __future__ import annotations

from notifyhub_digest.sanitize import sanitize_summary_html


def test_sanitize_strips_script_and_attributes():
    html = '<div onclick="alert(1)"><script>alert(1)</script><p>ok</p></div>'
    out = sanitize_summary_html(html)
    assert "分析に失敗しました" in out


def test_sanitize_invalid_returns_fallback_when_empty():
    out = sanitize_summary_html("<span>nope</span>")
    assert "分析に失敗しました" in out
