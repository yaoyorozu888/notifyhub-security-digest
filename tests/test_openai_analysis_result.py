from __future__ import annotations

from notifyhub_digest.openai_client import SYSTEM_PROMPT, OpenAIConfig, _analysis_schema_hint, _build_analysis_payload, _coerce_analysis_result


def test_coerce_analysis_result_accepts_lesson_payload() -> None:
    result = _coerce_analysis_result(
        {
            "summary_html": "<h4>概要</h4><p>summary</p>",
            "technical_terms": [{"term": "mTLS", "explanation": "相互認証。"}],
            "lessons": [{"title": "公開鍵認証", "body": "TLSでは証明書検証で相手を確認する。例えば社内PKIでも同じ考え方を使う。"}],
            "impact_level": "High",
            "impact_reason": "reason",
            "threat_type": "Vulnerability",
        }
    )

    assert len(result.lessons) == 1
    assert result.lessons[0].title == "公開鍵認証"
    assert "TLS" in result.lessons[0].body


def test_coerce_analysis_result_missing_lessons_defaults_empty() -> None:
    result = _coerce_analysis_result(
        {
            "summary_html": "<h4>概要</h4><p>summary</p>",
            "technical_terms": [],
            "impact_level": "Low",
            "impact_reason": "reason",
            "threat_type": "Patch",
        }
    )

    assert result.lessons == []


def test_coerce_analysis_result_ignores_removed_read_action_fields() -> None:
    result = _coerce_analysis_result(
        {
            "summary_html": "<h4>概要</h4><p>summary</p>",
            "technical_terms": [],
            "lessons": [{"title": "なし", "body": "なし"}],
            "impact_level": "Medium",
            "impact_reason": "reason",
            "threat_type": "Advisory",
            "read_action": "Read",
            "action_reason": "unused",
        }
    )

    assert result.impact_level == "Medium"
    assert result.lessons[0].title == "なし"


def test_build_analysis_payload_omits_temperature_for_gpt5_models() -> None:
    cfg = OpenAIConfig(api_key="test", model="gpt-5.5", temperature=0.4)

    payload = _build_analysis_payload(cfg, {"title": "sample"})

    assert payload["model"] == "gpt-5.5"
    assert "temperature" not in payload


def test_build_analysis_payload_keeps_temperature_for_non_gpt5_models() -> None:
    cfg = OpenAIConfig(api_key="test", model="gpt-4.1", temperature=0.4)

    payload = _build_analysis_payload(cfg, {"title": "sample"})

    assert payload["temperature"] == 0.4


def test_analysis_prompts_require_plain_japanese_style() -> None:
    assert "常体" in SYSTEM_PROMPT
    assert "です・ます調" in SYSTEM_PROMPT
    assert "「〜だ」「〜である」で終える文を避ける" in SYSTEM_PROMPT

    schema_hint = _analysis_schema_hint()
    assert "常体" in schema_hint
    assert "です・ます調" in schema_hint
    assert "「〜だ」「〜である」で終えない" in schema_hint