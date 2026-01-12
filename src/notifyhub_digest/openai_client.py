from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from notifyhub_digest.models import AnalysisResult


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 700
    temperature: float = 0.2


SYSTEM_PROMPT = (
    "あなたはCSIRT実務者向けのアナリストです。\n"
    "実務経験3年程度の担当者でも学びがある深さで、実務判断に役立つ要約と分析を作成してください。\n"
    "入力に含まれる参考URLや引用の文脈がある場合は、その情報も考慮してください。\n"
    "必ずJSONのみを返してください。前置きやコードフェンス(``` )は不要です。"
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
        "summary_htmlには <p><ul><ol><li><strong><h4><code><br><div> 以外を含めないでください。属性は付けないでください。\n"
        "summary_htmlは <h4>概要</h4><p>..</p><h4>現場の学び</h4><ul><li>..</li></ul>"
        "<h4>初動・継続対応の示唆</h4><ul><li>..</li></ul> の流れを意識してください。\n"
        "technical_terms.term は英語の表記を先に書き、その後に日本語の用語名を付けてください（例: "
        '"Exploit Chain / 攻撃連鎖"）。explanation は日本語で簡潔に説明してください。'
    )


def has_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def load_openai_config() -> OpenAIConfig | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    try:
        max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "700"))
    except Exception:
        max_tokens = 700
    try:
        temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
    except Exception:
        temperature = 0.2
    return OpenAIConfig(
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_tokens=max(200, min(max_tokens, 2000)),
        temperature=max(0.0, min(temperature, 1.0)),
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s)\"']+")


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from model output.

    The model *should* return JSON only, but in practice it may wrap with code fences
    or add a short preface. We try a few safe heuristics.
    """

    s = (text or "").strip()
    if not s:
        raise ValueError("empty model output")

    # 1) Plain JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) Code-fenced JSON
    s2 = _FENCE_RE.sub("", s).strip()
    try:
        obj = json.loads(s2)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 3) Best-effort: take substring from first '{' to last '}'
    i = s2.find("{")
    j = s2.rfind("}")
    if 0 <= i < j:
        try:
            obj = json.loads(s2[i : j + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    raise ValueError("could not parse JSON object")


def _coerce_analysis_result(parsed: dict[str, Any]) -> AnalysisResult:
    """Be tolerant to minor schema drift and fill defaults."""

    if not isinstance(parsed, dict):
        return AnalysisResult(summary_html="", technical_terms=[], impact_level="Unknown", threat_type="-")

    # Normalize common variations.
    if "impact_level" in parsed and isinstance(parsed["impact_level"], str):
        v = parsed["impact_level"].strip().capitalize()
        if v not in {"Critical", "High", "Medium", "Low", "Info"}:
            parsed["impact_level"] = "Unknown"
        else:
            parsed["impact_level"] = v

    if "threat_type" in parsed and isinstance(parsed["threat_type"], str):
        parsed["threat_type"] = parsed["threat_type"].strip() or "-"

    try:
        return AnalysisResult.model_validate(parsed)
    except Exception:
        # Partial fallback
        return AnalysisResult(
            summary_html=str(parsed.get("summary_html") or ""),
            technical_terms=[],
            impact_level=str(parsed.get("impact_level") or "Unknown"),
            threat_type=str(parsed.get("threat_type") or "-"),
        )


def analyze_item(
    client: httpx.Client,
    cfg: OpenAIConfig,
    *,
    title: str,
    source_name: str,
    published_at_iso: str,
    original_url: str,
    summary: str | None,
    rule_severity: str,
    rule_reason: str,
) -> AnalysisResult:
    """OpenAI Chat Completionsを使いJSONを返させる（ローカル版）。

    - APIキー未設定時は呼び出し側でスキップする。
    - JSONパースに失敗した場合はUnknown/空で返す。
    """

    refs: list[str] = []
    if summary:
        refs = _URL_RE.findall(summary)

    user = {
        "title": title,
        "source_name": source_name,
        "published_at": published_at_iso,
        "original_url": original_url,
        "summary": summary or "",
        "reference_urls": refs[:5],
        "rule_severity": rule_severity,
        "rule_reason": rule_reason,
    }

    payload = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
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
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    try:
        parsed = _extract_json_object(content)
        return _coerce_analysis_result(parsed)
    except Exception:
        return AnalysisResult(summary_html="", technical_terms=[], impact_level="Unknown", threat_type="-")
