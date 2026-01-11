from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

from notifyhub_digest.models import AnalysisResult


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"


SYSTEM_PROMPT = (
    "あなたはCSIRT実務者向けのアナリストです。\n"
    "与えられたRSS記事メタ情報から、実務判断に役立つ要約と分析を作成してください。\n"
    "必ずJSONのみを返してください。"
)


def _analysis_schema_hint() -> str:
    return (
        "出力JSONスキーマ:\n"
        "{\n"
        '  "summary_html": "<div>...</div>",\n'
        '  "technical_terms": [{"term":"...","explanation":"..."}],\n'
        '  "impact_level": "Critical|High|Medium|Low|Info",\n'
        '  "threat_type": "..."\n'
        "}\n"
        "summary_htmlには <p><ul><ol><li><strong><h4><code><br><div> 以外を含めないでください。属性は付けないでください。"
    )


def has_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def load_openai_config() -> OpenAIConfig | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return OpenAIConfig(api_key=api_key, model=model)


def analyze_item(
    client: httpx.Client,
    cfg: OpenAIConfig,
    *,
    title: str,
    source_name: str,
    published_at_iso: str,
    original_url: str,
    rule_severity: str,
    rule_reason: str,
) -> AnalysisResult:
    """OpenAI Chat Completionsを使いJSONを返させる（ローカル版）。

    - APIキー未設定時は呼び出し側でスキップする。
    - JSONパースに失敗した場合はUnknown/空で返す。
    """

    user = {
        "title": title,
        "source_name": source_name,
        "published_at": published_at_iso,
        "original_url": original_url,
        "rule_severity": rule_severity,
        "rule_reason": rule_reason,
    }

    payload = {
        "model": cfg.model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _analysis_schema_hint() + "\n\n入力:\n" + json.dumps(user, ensure_ascii=False)},
        ],
    }

    res = client.post(
        f"{cfg.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        json=payload,
    )
    res.raise_for_status()

    data = res.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

    try:
        parsed = json.loads(content)
        return AnalysisResult.model_validate(parsed)
    except Exception:
        return AnalysisResult(summary_html="", technical_terms=[], impact_level="Unknown", threat_type="-")
