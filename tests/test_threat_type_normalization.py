from __future__ import annotations

from notifyhub_digest.openai_client import _coerce_analysis_result


def test_threat_type_normalizes_japanese_to_english() -> None:
    r = _coerce_analysis_result(
        {
            "summary_html": "<p>x</p>",
            "impact_level": "Low",
            "impact_reason": "x",
            "threat_type": "脆弱性",
        }
    )
    assert r.threat_type == "Vulnerability"


def test_threat_type_unknown_when_empty() -> None:
    r = _coerce_analysis_result(
        {
            "summary_html": "<p>x</p>",
            "impact_level": "Low",
            "impact_reason": "x",
            "threat_type": "",
        }
    )
    assert r.threat_type == "Unknown"

