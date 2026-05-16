from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from notifyhub_digest.models import AnalysisResult, Lesson


logger = logging.getLogger(__name__)


_TEMPERATURE_UNSUPPORTED_MODEL_RE = re.compile(r"^gpt-5(?:[.-]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 900
    temperature: float = 0.4


SYSTEM_PROMPT = (
    "あなたは大規模組織・官民混在環境での対応経験を持つ、CSIRT向けのシニアアナリストです。\n"
    "主な読者は実務経験7〜10年程度のCSIRT担当者であり、\n"
    "『知っている話の再確認』ではなく『判断のブレを減らすための補助線』を提供することが目的です。\n"
    "生成する記事本文・用語解説・深掘り解説は、すべて日本語の常体で書く。です・ます調の敬体は使わない。\n"
    "「〜だ」「〜である」で終える文を避ける。\n"
    "入力記事の title は原文のままでよいが、それ以外の出力項目は固有名詞や製品名、CVE、URL を除いて日本語で書く。英語の文や英語だけの箇条書きは禁止する。\n"
    "\n"
    "入力情報の前提（情報源）:\n"
    "- 入力は以下の情報源由来の記事・アドバイザリ・観測情報です（主にセキュリティ、時にIT一般）:\n"
    "  * 公的: CISA(KEV), NIST/NVD, JPCERT/CC, IPA, NICT, CERT/CC など\n"
    "  * ベンダー: Microsoft(MSRC), Azure Security Blog, Google Security Blog, Cisco, Palo Alto(Unit42), Mandiant など\n"
    "  * メディア/コミュニティ: BleepingComputer, The Hacker News, SANS ISC など\n"
    "  * 観測/テレメトリ: Shadowserver など\n"
    "- IT一般記事の場合でも、CSIRT業務との関係（関係が薄い場合はその旨）を整理してください。\n"
    "\n"
    "必須の分析原則:\n"
    "- 単なる要約は禁止。意思決定に効く観点（前提条件・攻撃者視点・被害の広がり方・検知/封じ込め）を必ず含める\n"
    "- 一般論・教科書的説明ではなく、企業・組織ネットワーク運用の現実を前提に評価する\n"
    "- 不確実な点は推測で補わず、未確認事項として明示する\n"
    "- CVSSや話題性だけで重要度を引き上げない（実運用上の影響を優先）\n"
    "\n"
    "RSS種別ごとのimpact補正ロジック（重要）:\n"
    "- まず本文から『ベースimpact_level』を決め、その後に情報源種別で補正をかけて最終impact_levelを出す。\n"
    "- source_type を本文/URL/ドメイン/フィードから推定してよい（推定は明示不要）。\n"
    "- 補正の考え方:\n"
    "  * 公的（CISA KEV / CERT / JPCERT / IPA等）:\n"
    "    - KEV掲載や注意喚起は『悪用現実性』が高いシグナル。ベースより+1段階（上限Critical）を検討。\n"
    "    - ただし単なる情報更新・再掲・手順周知なら据え置き/下げも可。\n"
    "  * ベンダー（MSRC/製品セキュリティブログ等）:\n"
    "    - 修正済み・更新配布は『対処可能性』が高い。緊急性はベース据え置き〜-1を検討。\n"
    "    - ただしRCE/認証前/既に悪用確認/広範囲既定有効などは下げない。\n"
    "  * メディア（ニュース/まとめ）:\n"
    "    - 誇張や二次情報の可能性。ベースより+1しない（原則据え置き）。\n"
    "    - 悪用確認・一次情報リンク・再現手順/PoCの成熟度が明確なら据え置きで評価。\n"
    "  * 観測/テレメトリ（Shadowserver等）:\n"
    "    - 露出・スキャン増加・攻撃波及のシグナル。『自組織の露出可能性』が高い場合は+1を検討。\n"
    "    - ただし観測が広域でも自組織に関係が薄い場合は据え置き/下げも可。\n"
    "- 補正を適用した根拠は impact_reason に必ず含める（例: 公的注意喚起/KEV、ベンダーパッチ済み、観測増加、メディア二次情報 など）。\n"
    "\n"
    "technical_terms（用語解説）ルール:\n"
    "- 同じ用語が過去にも使われていることを前提とし、毎回同じ説明を繰り返さない\n"
    "- 今回の記事文脈で『なぜ重要か』に焦点を当てる\n"
    "- 可能な限り対立概念・混同されやすい概念と対比して説明する\n"
    "- explanation は100文字以内、日本語2〜3文\n"
    "\n"
    "lessons（深掘り解説）ルール:\n"
    "- 記事中の技術用語、製品情報、法令、制度などから最も学習価値が高いものを1つだけ選ぶ\n"
    "- 技術的な本質を短く教える補助教材として書く。単なる用語の言い換えは禁止\n"
    "- 3〜5分で理解できる分量を目安にし、具体例を必ず含める\n"
    "- 同じ用語でも毎回切り口を固定せず、実装、処理フロー、運用上の注意、関連リスクなど記事文脈に最も合う軸を選ぶ\n"
    "- 適切な題材が見当たらない場合は lessons に 1 件だけ title=なし, body=なし を入れる\n"
    "\n"
    "分析観点の指針:\n"
    "- 前提条件: 攻撃成立に必要な構成・権限・露出条件\n"
    "- 攻撃者視点: ROIが高いポイント、踏み台・横展開の可能性\n"
    "- 被害の広がり方: 単点か、権限昇格・持続化・横移動につながるか\n"
    "- 検知の要点: 実務で確認可能なログ・挙動・設定差分\n"
    "- 封じ込めの要点: 初動で止める場所と、業務影響が出やすい注意点\n"
    "\n"
    "CVE番号が含まれる場合:\n"
    "- CVE番号とCVSSスコア（存在する場合）を明示\n"
    "- 数値評価と、実運用上の体感リスクの差があれば必ず言及\n"
    "\n"
    "出力制約:\n"
    "- 必ずJSONのみを返す\n"
    "- 前置き、説明文、コードフェンスは禁止\n"
    "- 指定スキーマ以外のキーは追加しない\n"
)


def _analysis_schema_hint() -> str:
    return (
        "出力JSONスキーマ:\n"
        "{\n"
        '  \"summary_html\": \"<div>...</div>\",\n'
        '  \"technical_terms\": [{\"term\":\"...\",\"explanation\":\"...\"}],\n'
        '  \"lessons\": [{\"title\":\"...\",\"body\":\"...\"}],\n'
        '  \"impact_level\": \"Critical|High|Medium|Low|Info\",\n'
        '  \"impact_reason\": \"...\",\n'
        '  \"threat_type\": \"Vulnerability|Exploit|Zero-day|Vulnerability Disclosure|Patch|Misconfiguration|Malware|Ransomware|Botnet|Cryptojacking|Phishing|Business Email Compromise|Scam/Fraud|Credential Theft|Intrusion|Data Breach|DDoS|Supply Chain|Advisory|Other|Unknown\"\n'
        "}\n"
        "\n"
        "summary_html 制約:\n"
        "- 使用可能タグ: <div><p><ul><ol><li><strong><h4><code><br>\n"
        "- 属性（class/id/style等）は付けない\n"
        "- 入力記事の title を除き、すべての出力項目は日本語で書く\n"
        "- summary_html・technical_terms.explanation・lessons.body はすべて常体で書き、です・ます調は使わない\n"
        "- summary_html・technical_terms.explanation・lessons.body は「〜だ」「〜である」で終えない\n"
        "- 以下の構成を厳守:\n"
        "  <h4>概要</h4><p>...</p>\n"
        "\n"
        "概要:\n"
        "- 4〜6文で記述し、記事理解のための文脈を厚めに書く\n"
        "- 最低でも以下を含める: 何が起きたか / どう悪用されるか / どこまで影響が広がるか / 未確認事項\n"
        "- 単なる出来事の言い換えではなく、判断に必要な背景を補う\n"
        "\n"
        "強調ルール:\n"
        "- 意思決定に影響する語句のみ <strong>…</strong> で強調\n"
        "- 最大8箇所、短いフレーズ単位\n"
        "\n"
        "文字量:\n"
        "- summary_html 全体で500〜800文字程度\n"
        "\n"
        "technical_terms 制約:\n"
        "- 最大4件\n"
        "- explanation は100文字以内、2〜3文\n"
        "- 対立概念・混同されやすい概念があれば必ず対比\n"
        "- 一般名詞・単なる固有名詞の列挙は禁止\n"
        "\n"
        "lessons 制約:\n"
        "- 0〜1件ではなく、常に1件入れる\n"
        "- 学習価値の高い題材がある場合は title/body を埋める\n"
        "- 題材が弱い場合は title=なし, body=なし とする\n"
        "- body は3〜5分で読み切れる長さで、少なくとも1つ具体例を含める\n"
        "- 記事内容の焼き直しではなく、背景理解や実務判断に効く説明を書く\n"
        "\n"
        "term 表記:\n"
        "- 英語が一般的な場合は英語→日本語併記\n"
        "\n"
        "impact_level:\n"
        "- 本文からベース評価→情報源種別で補正（根拠はimpact_reasonに含める）\n"
        "\n"
        "impact_reason:\n"
        "- impact_level の根拠を2〜3行で簡潔に\n"
        "- 技術的深刻度 + 運用影響 + 情報源補正の根拠\n"
        "- 固有名詞や製品名、CVE、URL を除き日本語で書き、英語の文を混ぜない\n"
        "\n"
        "threat_type:\n"
        "- 複数該当しそうな場合は、CSIRTが最初に意識すべき1種を選択\n"
    )


def has_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _supports_temperature(model: str) -> bool:
    normalized = (model or "").strip()
    if not normalized:
        return True
    return _TEMPERATURE_UNSUPPORTED_MODEL_RE.search(normalized) is None


def _build_analysis_payload(cfg: OpenAIConfig, user: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": cfg.model,
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
    if _supports_temperature(cfg.model):
        payload["temperature"] = cfg.temperature
    return payload


def load_openai_config(*, prefix: str = "OPENAI") -> OpenAIConfig | None:
    api_key = os.getenv(f"{prefix}_API_KEY")
    if not api_key:
        return None
    model = (os.getenv(f"{prefix}_MODEL") or "").strip() or "gpt-5.5"
    base_url = (os.getenv(f"{prefix}_BASE_URL") or "").strip() or "https://api.openai.com/v1"
    try:
        max_tokens = int(os.getenv(f"{prefix}_MAX_TOKENS", "900"))
    except Exception:
        max_tokens = 900
    try:
        temperature = float(os.getenv(f"{prefix}_TEMPERATURE", "0.4"))
    except Exception:
        temperature = 0.4
    return OpenAIConfig(
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_tokens=max(200, min(max_tokens, 5000)),
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


def _extract_response_model(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    model = data.get("model")
    if isinstance(model, str):
        return model.strip()
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
        return AnalysisResult(summary_html="", technical_terms=[], lessons=[], impact_level="Unknown", impact_reason="", threat_type="Unknown")

    def _normalize_threat_type(raw: str) -> str:
        v = (raw or "").strip()
        if not v or v == "-":
            return "Unknown"

        v_l = v.lower()

        # Prefer explicit English labels.
        allowed = {
            "vulnerability": "Vulnerability",
            "exploit": "Exploit",
            "zero-day": "Zero-day",
            "zeroday": "Zero-day",
            "0day": "Zero-day",
            "0-day": "Zero-day",
            "vulnerability disclosure": "Vulnerability Disclosure",
            "patch": "Patch",
            "misconfiguration": "Misconfiguration",
            "malware": "Malware",
            "ransomware": "Ransomware",
            "botnet": "Botnet",
            "cryptojacking": "Cryptojacking",
            "phishing": "Phishing",
            "business email compromise": "Business Email Compromise",
            "bec": "Business Email Compromise",
            "scam/fraud": "Scam/Fraud",
            "scam": "Scam/Fraud",
            "fraud": "Scam/Fraud",
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
        if v_l in {"vuln disclosure", "disclosure"}:
            return "Vulnerability Disclosure"
        if v_l in {"patching", "security patch", "update"}:
            return "Patch"
        if v_l in {"config", "configuration", "misconfig"}:
            return "Misconfiguration"
        if v_l in {"crypto-jacking", "crypto mining", "cryptomining", "coin mining"}:
            return "Cryptojacking"
        if v_l in {"email compromise", "business email"}:
            return "Business Email Compromise"
        if v_l in {"fraud/scam"}:
            return "Scam/Fraud"

        # Japanese -> English mapping (best-effort).
        if any(x in v for x in ("脆弱性", "ぜいじゃくせい", "vuln", "cve")):
            return "Vulnerability"
        if any(x in v for x in ("ゼロデイ", "0デイ", "0day", "ゼロ・デイ", "未公開", "未修正")):
            return "Zero-day"
        if any(x in v for x in ("開示", "公開", "disclosure")):
            return "Vulnerability Disclosure"
        if any(x in v for x in ("パッチ", "修正", "更新", "アップデート", "update", "patch")):
            return "Patch"
        if any(x in v for x in ("設定ミス", "誤設定", "構成ミス", "misconfig", "misconfiguration")):
            return "Misconfiguration"
        if any(x in v for x in ("エクスプロイト", "攻撃コード", "悪用", "exploit")):
            return "Exploit"
        if any(x in v for x in ("ボットネット", "botnet", "C2", "C&C")):
            return "Botnet"
        if any(x in v for x in ("クリプトジャッキング", "暗号資産マイニング", "不正マイニング", "cryptojacking", "coin mining")):
            return "Cryptojacking"
        if any(x in v for x in ("マルウェア", "ウイルス", "トロイ", "worm", "trojan")):
            return "Malware"
        if any(x in v for x in ("ランサム", "身代金", "ransom")):
            return "Ransomware"
        if any(x in v for x in ("フィッシング", "phish")):
            return "Phishing"
        if any(x in v for x in ("BEC", "ビジネスメール詐欺", "メール詐欺", "business email compromise")):
            return "Business Email Compromise"
        if any(x in v for x in ("詐欺", "騙し", "scam", "fraud")):
            return "Scam/Fraud"
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

    raw_lessons = parsed.get("lessons")
    if isinstance(raw_lessons, list):
        normalized_lessons: list[dict[str, str]] = []
        for entry in raw_lessons[:1]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            body = str(entry.get("body") or "").strip()
            if not title and not body:
                continue
            if not title:
                title = "なし" if body == "なし" else "補足"
            if not body:
                body = "なし" if title == "なし" else "説明なし"
            normalized_lessons.append({"title": title, "body": body})
        parsed["lessons"] = normalized_lessons
    elif isinstance(raw_lessons, dict):
        title = str(raw_lessons.get("title") or "").strip() or "補足"
        body = str(raw_lessons.get("body") or "").strip() or "説明なし"
        parsed["lessons"] = [{"title": title, "body": body}]
    else:
        parsed["lessons"] = []

    try:
        return AnalysisResult.model_validate(parsed)
    except Exception:
        # Partial fallback
        return AnalysisResult(
            summary_html=str(parsed.get("summary_html") or ""),
            technical_terms=[],
            lessons=[Lesson(title="なし", body="なし")],
            impact_level=str(parsed.get("impact_level") or "Unknown"),
            impact_reason=str(parsed.get("impact_reason") or ""),
            threat_type=_normalize_threat_type(str(parsed.get("threat_type") or "")),
            model_version=str(parsed.get("model_version") or ""),
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

    payload = _build_analysis_payload(cfg, user)

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
    model_version = _extract_response_model(data) or cfg.model

    try:
        parsed = _extract_json_object(content)
        result = _coerce_analysis_result(parsed)
        if not result.summary_html:
            result.summary_html = _fallback_summary_html(summary)
        result.model_version = model_version
        return result
    except Exception as e:
        if content.strip():
            logger.warning("OpenAI response parse failed: %s body=%s", e, content[:2000])
        else:
            logger.warning("OpenAI response parse failed: %s (empty body)", e)
        return AnalysisResult(
            summary_html=_fallback_summary_html(summary),
            technical_terms=[],
            lessons=[Lesson(title="なし", body="なし")],
            impact_level="Unknown",
            impact_reason="",
            threat_type="Unknown",
            model_version=model_version,
        )
