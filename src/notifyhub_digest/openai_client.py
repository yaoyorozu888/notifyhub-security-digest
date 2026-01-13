from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from notifyhub_digest.models import AnalysisResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 700
    temperature: float = 0.2


SYSTEM_PROMPT = (
    "あなたは経験豊富なCSIRT（Computer Security Incident Response Team）実務者向けのアナリストです。\n"
    "実務経験5年程度の担当者でも学びがある深さで、実務判断に役立つ要約と分析を作成してください。\n"
    "入力に含まれる参考URLや引用の文脈がある場合は、その情報も考慮してください。\n"
    "本文中でソフトウェア名・ライブラリ名・組織名が出てくる場合は、それが何かを短く補足してください。\n"
    "出力は簡潔にし、無駄な繰り返しや長い前置きを避けてください。\n"
    "もしCVE番号（Common Vulnerabilities and Exposures）が含まれていれば、CVE番号とCVSSスコアの対応を記載してください。\n"
    "必ずJSONのみを返してください。前置きやコードフェンス(``` )は不要です。"
)


def _analysis_schema_hint() -> str:
    return (
        "出力JSONスキーマ:\n"
        "{\n"
        '  "summary_html": "<div>...</div>",\n'
        '  "technical_terms": [{"term":"...","explanation":"..."}],\n'
        '  "impact_level": "Critical|High|Medium|Low|Info",\n'
        '  "impact_reason": "...",\n'
        '  "threat_type": "..."\n'
        "}\n"
        "summary_htmlには <p><ul><ol><li><strong><h4><code><br><div> 以外を含めないでください。属性は付けないでください。\n"
        "summary_htmlは <h4>概要</h4><p>..</p><h4>現場の学び</h4><ul><li>..</li></ul>"
        "<h4>初動・継続対応の示唆</h4><ul><li>..</li></ul> の流れを意識してください。\n"
        "各セクションは短く、合計で600〜800文字程度に収めてください。\n"
        "technical_terms は最大4件、各 explanation は2〜4文に収めてください。\n"
        "technical_terms.term は英語表記が一般的ではない場合、英語の用語名を先に書き、その後に日本語の用語名を付けてください（例: "
        '"Exploit Chain / 攻撃連鎖"）。explanation は日本語で簡潔に説明してください。\n'
        "impact_levelはサイバーセキュリティの観点で判断してください。基準の目安:\n"
        "- Critical: 広範囲に影響し即時対応が必要、既に悪用/大規模被害が確認、または安全性に重大な影響。\n"
        "- High: 重大な影響が想定され、悪用容易・被害が大きいがCriticalほどの緊急性ではない。\n"
        "- Medium: 影響はあるが限定的、悪用には条件が必要、または回避策/緩和策が有効。\n"
        "- Low: 影響は軽微、限定環境のみ、情報提供や注意喚起レベル。\n"
        "- Info: 影響や脅威が不明、もしくは更新情報・観測情報で直接的なリスクが低い。\n"
        "impact_reasonはimpact_levelの判断理由を2～3行程度で簡潔に書いてください。"
    )


def has_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def load_openai_config() -> OpenAIConfig | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    model = (os.getenv("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or "https://api.openai.com/v1"
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


def _extract_response_text(data: dict[str, Any]) -> str:
    """Extract output text from Responses API payload."""

    if not isinstance(data, dict):
        return ""

    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text

    output = data.get("output")
    if not isinstance(output, list):
        return ""

    for item in output:
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                return part.get("text")
            if part.get("type") == "output_json" and isinstance(part.get("json"), dict):
                return json.dumps(part.get("json"), ensure_ascii=False)
    return ""


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
        return AnalysisResult(summary_html="", technical_terms=[], impact_level="Unknown", impact_reason="", threat_type="Unknown")

    def _normalize_threat_type(raw: str) -> str:
        v = (raw or "").strip()
        if not v or v == "-":
            return "Unknown"

        v_l = v.lower()

        # Prefer explicit English labels.
        allowed = {
            "vulnerability": "Vulnerability",
            "exploit": "Exploit",
            "malware": "Malware",
            "ransomware": "Ransomware",
            "phishing": "Phishing",
            "credential theft": "Credential Theft",
            "intrusion": "Intrusion",
            "data breach": "Data Breach",
            "ddos": "DDoS",
            "supply chain": "Supply Chain",
            "advisory": "Advisory",
            "other": "Other",
            "unknown": "Unknown",
        }
        for k, mapped in allowed.items():
            if v_l == k:
                return mapped

        # Accept a few common variants.
        if v_l in {"cred theft", "credential-theft", "credentials"}:
            return "Credential Theft"
        if v_l in {"dos", "ddos attack", "denial of service"}:
            return "DDoS"
        if v_l in {"supply-chain", "supplychain"}:
            return "Supply Chain"

        # Japanese -> English mapping (best-effort).
        if any(x in v for x in ("脆弱性", "ぜいじゃくせい", "vuln", "cve")):
            return "Vulnerability"
        if any(x in v for x in ("エクスプロイト", "攻撃コード", "悪用", "exploit")):
            return "Exploit"
        if any(x in v for x in ("マルウェア", "ウイルス", "トロイ", "botnet", "ボットネット")):
            return "Malware"
        if any(x in v for x in ("ランサム", "身代金", "ransom")):
            return "Ransomware"
        if any(x in v for x in ("フィッシング", "phish")):
            return "Phishing"
        if any(x in v for x in ("認証情報", "資格情報", "credential", "パスワード")):
            return "Credential Theft"
        if any(x in v for x in ("侵害", "不正アクセス", "侵入", "compromise")):
            return "Intrusion"
        if any(x in v for x in ("情報漏えい", "漏えい", "流出", "data breach")):
            return "Data Breach"
        if any(x in v for x in ("DDoS", "ddos", "サービス妨害", "dos")):
            return "DDoS"
        if any(x in v for x in ("サプライチェーン", "供給網", "supply")):
            return "Supply Chain"
        if any(x in v for x in ("注意喚起", "アドバイザリ", "更新", "リリース", "advisory")):
            return "Advisory"

        return "Other"

    # Normalize common variations.
    if "impact_level" in parsed and isinstance(parsed["impact_level"], str):
        v = parsed["impact_level"].strip().capitalize()
        if v not in {"Critical", "High", "Medium", "Low", "Info"}:
            parsed["impact_level"] = "Unknown"
        else:
            parsed["impact_level"] = v

    if "threat_type" in parsed and isinstance(parsed["threat_type"], str):
        parsed["threat_type"] = _normalize_threat_type(parsed["threat_type"])

    try:
        return AnalysisResult.model_validate(parsed)
    except Exception:
        # Partial fallback
        return AnalysisResult(
            summary_html=str(parsed.get("summary_html") or ""),
            technical_terms=[],
            impact_level=str(parsed.get("impact_level") or "Unknown"),
            impact_reason=str(parsed.get("impact_reason") or ""),
            threat_type=_normalize_threat_type(str(parsed.get("threat_type") or "")),
        )


def _fallback_summary_html(summary: str | None) -> str:
    s = (summary or "").strip()
    if not s:
        return ""
    if "<" in s and ">" in s:
        return s
    return f"<p>{html.escape(s)}</p>"


def analyze_item(
    client: httpx.Client,
    cfg: OpenAIConfig,
    *,
    title: str,
    source_name: str,
    published_at_iso: str,
    original_url: str,
    summary: str | None,
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
    }

    payload = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_output_tokens": cfg.max_tokens,
        "text": {"format": {"type": "json_object"}},
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": _analysis_schema_hint()
                        + "\n\n入力:\n"
                        + json.dumps(user, ensure_ascii=False),
                    }
                ],
            },
        ],
    }

    res = client.post(
        f"{cfg.base_url}/responses",
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        json=payload,
    )
    try:
        res.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text
        except Exception:
            body = ""
        if body:
            logger.warning(
                "OpenAI API error: status=%s body=%s",
                e.response.status_code,
                body[:2000],
            )
        else:
            logger.warning("OpenAI API error: status=%s", e.response.status_code)
        raise

    data = res.json()
    content = _extract_response_text(data)

    try:
        parsed = _extract_json_object(content)
        result = _coerce_analysis_result(parsed)
        if not result.summary_html:
            result.summary_html = _fallback_summary_html(summary)
        return result
    except Exception as e:
        if content.strip():
            logger.warning("OpenAI response parse failed: %s body=%s", e, content[:2000])
        else:
            logger.warning("OpenAI response parse failed: %s (empty body)", e)
        return AnalysisResult(
            summary_html=_fallback_summary_html(summary),
            technical_terms=[],
            impact_level="Unknown",
            impact_reason="",
            threat_type="Unknown",
        )
