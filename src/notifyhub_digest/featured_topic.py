from __future__ import annotations

import logging
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from notifyhub_digest.models import FeaturedTopic, InformationSource
from notifyhub_digest.openai_client import (
    _coerce_analysis_result,
    _extract_json_object,
    _extract_response_model,
    _extract_response_text,
)


logger = logging.getLogger(__name__)


GROK_BASE_URL = "https://api.x.ai/v1"


def _featured_topic_http_retries() -> int:
    raw = (
        os.getenv("FEATURED_TOPIC_HTTP_RETRIES")
        or os.getenv("GROK_HTTP_RETRIES")
        or os.getenv("NOTIFYHUB_HTTP_RETRIES")
        or "2"
    ).strip()
    try:
        return max(0, int(raw))
    except Exception:
        return 2


def _is_retryable_featured_topic_error(exc: Exception) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _is_retryable_featured_topic_status(status_code: int) -> bool:
    return 500 <= status_code < 600


@dataclass(frozen=True)
class GrokConfig:
    api_key: str
    model: str = "grok-4.3"
    max_tokens: int = 2200
    temperature: float | None = 0.3


@dataclass(frozen=True)
class FeaturedTopicsSettings:
    count: int
    categories: list[str]


@dataclass(frozen=True)
class CategoryPolicy:
    category: str
    normalized: str
    guidance: str = ""
    exclude_cybersecurity_incidents: bool = False


FEATURED_SYSTEM_PROMPT = (
    "あなたは日次ダイジェストの編集者です。\n"
    "必要に応じて x_search または web_search を使い、根拠が薄い場合は両方を使って過去一日の X 投稿とニュースサイトを横断して調査してください。\n"
    "検索をせずに既知知識だけで答えることは禁止です。\n"
    "生成する記事本文・深掘り解説は、日本語の常体で書く。です・ます調の敬体は使わない。「〜だ」「〜である」で終える文を避ける。\n"
    "用語解説は簡潔で中立的な説明文にし、「〜だ」「〜である」で終えない。\n"
    "article title に相当する title を除き、出力するすべての項目は固有名詞や製品名、CVE、URL を除いて日本語で書く。英語の文や英語だけの箇条書きは禁止する。\n"
    "\n"
    "選定原則:\n"
    "- 読者の実務判断や社会的理解に寄与するトピックを優先する\n"
    "- 速報性、影響度、話題性を評価軸に含め、今読む価値が高い話題を優先する\n"
    "- 単なる話題性だけに依存せず、噂、宣伝、一般論は避ける\n"
    "- セキュリティ、AI、法務・規制、政策、企業動向、研究など幅広い分野を対象にしてよい\n"
    "- 何が重要か、どこに影響するか、今なぜ注目すべきかを説明できるものを優先する\n"
    "- 速報性が高くても影響が限定的な話題より、意思決定や実務に波及する話題を優先する\n"
    "- X 投稿を選ぶ場合でも、根拠が薄い単発投稿は避け、裏づけ可能な情報を優先する\n"
    "- 同じ発信元、同じ企業・団体、同じ調査レポート、同じ年次予測、同じ抽象的なトレンド解説に偏らない\n"
    "- 数日続けて似たテーマが選ばれやすい候補は、新しい事実、追加発表、具体的な実務影響がない限り優先度を下げる\n"
    "- Gartner などの調査会社による IT トレンド予測や年次ランキングは、発表当日など明確な新規性がある場合を除き連日選ばない\n"
    "- 候補が複数ある場合は、発信元、地域、業界、技術領域、影響対象が異なる話題を選び、日次ダイジェスト全体の変化を作る\n"
    "- 過去一日より古い情報は原則採用しない\n"
    "- 生成する本文は全体として5分程度で目を通せる分量に収める\n"
    "\n"
    "出力制約:\n"
    "- JSONのみを返す\n"
    "- 前置き、説明文、コードフェンスは禁止\n"
    "- 指定スキーマ以外のキーは追加しない"
)


_CYBERSECURITY_THREAT_TYPES = {
    "Vulnerability",
    "Exploit",
    "Zero-day",
    "Vulnerability Disclosure",
    "Patch",
    "Misconfiguration",
    "Malware",
    "Ransomware",
    "Botnet",
    "Cryptojacking",
    "Phishing",
    "Business Email Compromise",
    "Scam/Fraud",
    "Credential Theft",
    "Intrusion",
    "Data Breach",
    "DDoS",
    "Supply Chain",
}

_CYBERSECURITY_CATEGORY_KEYWORDS = (
    "cybersecurity",
    "cyber security",
    "security",
    "soc",
    "threat",
    "incident",
    "breach",
    "vulnerability",
    "malware",
    "ransomware",
)

_AI_CATEGORY_KEYWORDS = (
    "ai",
    "genai",
    "llm",
    "machine learning",
    "artificial intelligence",
)

_TECH_TREND_CATEGORY_KEYWORDS = (
    "trend",
    "trends",
    "latest",
    "technology",
    "technologies",
    "tech",
    "it",
    "cloud",
    "platform",
    "infra",
    "infrastructure",
    "developer",
    "dev tools",
    "devtools",
    "devops",
    "data",
    "saas",
    "os",
    "device",
    "network",
    "open source",
    "enterprise it",
    "semiconductor",
)


def _normalize_category_label(category: str) -> str:
    return " ".join((category or "").strip().lower().replace("/", " ").replace("-", " ").split())


def _dedupe_categories(categories: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for category in categories:
        label = (category or "").strip()
        if not label:
            continue
        normalized = _normalize_category_label(label)
        key = normalized or label
        if key in seen:
            continue
        seen.add(key)
        deduped.append(label)
    return deduped


def _resolve_requested_category(requested_category: str, categories: list[str]) -> str:
    requested = (requested_category or "").strip()
    if not requested or not categories:
        return requested

    normalized_requested = _normalize_category_label(requested)
    if not normalized_requested:
        return requested

    for category in categories:
        if _normalize_category_label(category) == normalized_requested:
            return category
    return requested


def _contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    normalized_text = _normalize_category_label(text)
    tokens = set(normalized_text.split())
    for keyword in keywords:
        normalized_keyword = _normalize_category_label(keyword)
        if not normalized_keyword:
            continue
        if " " in normalized_keyword:
            if normalized_keyword in normalized_text:
                return True
            continue
        if normalized_keyword in tokens:
            return True
    return False


def _infer_category_policy(category: str) -> CategoryPolicy:
    normalized = _normalize_category_label(category)
    if not normalized:
        return CategoryPolicy(category=category, normalized=normalized)

    generic_guidance = (
        f'- カテゴリ「{category}」では、その分野そのものの動き、出来事、制度、研究、議論、製品、実務への影響を優先し、'
        "カテゴリ名が示す主題と直接関係しない話題は避けること"
    )

    is_security = _contains_any_keyword(normalized, _CYBERSECURITY_CATEGORY_KEYWORDS)
    is_ai = _contains_any_keyword(normalized, _AI_CATEGORY_KEYWORDS)
    is_tech_trend = _contains_any_keyword(normalized, _TECH_TREND_CATEGORY_KEYWORDS)

    if is_security:
        return CategoryPolicy(
            category=category,
            normalized=normalized,
            guidance=f"{generic_guidance}。特に脆弱性悪用、侵害、漏えい、マルウェア、規制対応などセキュリティ実務に直結する話題を優先すること",
        )

    if is_ai:
        return CategoryPolicy(
            category=category,
            normalized=normalized,
            guidance=f"{generic_guidance}。特にAIモデル、AI製品、AI研究、AI規制、AI導入事例などAIそのものの進展を扱い、単なるセキュリティ事故の要約は避けること",
        )

    if is_tech_trend:
        return CategoryPolicy(
            category=category,
            normalized=normalized,
            guidance=f"{generic_guidance}。特にクラウド、開発者ツール、半導体、AI基盤、データ基盤、SaaS、OS、デバイス、ネットワーク、オープンソース、エンタープライズITなどの技術トレンドを優先し、サイバー攻撃・脆弱性・情報漏えい・インシデント対応そのものは原則として選ばないこと",
            exclude_cybersecurity_incidents=True,
        )

    return CategoryPolicy(category=category, normalized=normalized, guidance=generic_guidance)


def _category_guidance(categories: list[str]) -> list[str]:
    guidance: list[str] = []
    for category in categories:
        policy = _infer_category_policy(category)
        if policy.guidance:
            guidance.append(policy.guidance)

    return guidance


def _looks_mismatched_for_category(*, requested_category: str, threat_type: str, title: str, summary_html: str) -> bool:
    policy = _infer_category_policy(requested_category)
    if not policy.exclude_cybersecurity_incidents:
        return False

    if threat_type in _CYBERSECURITY_THREAT_TYPES:
        return True

    text = f"{title} {_extract_plain_text(summary_html)}".lower()
    cybersecurity_markers = (
        "cyber attack",
        "cyberattack",
        "data breach",
        "ransomware",
        "zero-day",
        "zero day",
        "vulnerability",
        "exploit",
        "malware",
        "漏えい",
        "侵害",
        "脆弱性",
        "攻撃",
        "ランサムウェア",
    )
    return any(marker in text for marker in cybersecurity_markers)


def _extract_plain_text(summary_html: str) -> str:
    return " ".join(part.strip() for part in summary_html.replace("<", " ").replace(">", " ").split())


def _response_debug_excerpt(text: str, *, limit: int = 2000) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return "<empty>"
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}... [truncated {len(normalized) - limit} chars]"


def _parse_temperature_env(var_name: str, default: float | None) -> float | None:
    raw = os.getenv(var_name)
    if raw is None:
        return default

    normalized = raw.strip()
    if not normalized:
        return None

    try:
        value = float(normalized)
    except Exception:
        return default
    return max(0.0, min(value, 1.0))


def load_grok_config() -> GrokConfig | None:
    api_key = (os.getenv("GROK_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (os.getenv("GROK_MODEL") or "").strip() or "grok-4.3"
    try:
        max_tokens = int(os.getenv("GROK_MAX_TOKENS", "2200"))
    except Exception:
        max_tokens = 2200
    temperature = _parse_temperature_env("GROK_TEMPERATURE", 0.3)
    return GrokConfig(
        api_key=api_key,
        model=model,
        max_tokens=max(800, min(max_tokens, 4000)),
        temperature=temperature,
    )


def load_featured_topics_settings() -> FeaturedTopicsSettings:
    try:
        count = int(os.getenv("FEATURED_TOPIC_COUNT", "1"))
    except Exception:
        count = 1
    count = max(0, min(count, 10))
    raw_categories = (os.getenv("FEATURED_TOPIC_CATEGORIES") or "").strip()
    categories = _dedupe_categories([part.strip() for part in raw_categories.split(",") if part.strip()])
    return FeaturedTopicsSettings(count=count, categories=categories)


def _schema_hint(count: int) -> str:
    return (
        "出力JSONスキーマ:\n"
        "{\n"
        '  "topics": [\n'
        "    {\n"
        '      "topic_id": "featured-topic-1",\n'
        '      "requested_category": "...",\n'
        '      "title": "...",\n'
        '      "source_name": "...",\n'
        '      "published_at": "2026-05-09T01:23:45Z",\n'
        '      "original_url": "https://...",\n'
        '      "selection_reason": "...",\n'
        '      "information_sources": [{"title":"...","url":"https://...","source_type":"web|x|official|news|law|research"}],\n'
        '      "summary_html": "<h4>概要</h4><p>...</p>",\n'
        '      "technical_terms": [{"term":"...","explanation":"..."}],\n'
        '      "lessons": [{"title":"...","body":"..."}],\n'
        '      "impact_level": "Critical|High|Medium|Low|Info",\n'
        '      "impact_reason": "...",\n'
        '      "threat_type": "Vulnerability|Exploit|Zero-day|Vulnerability Disclosure|Patch|Misconfiguration|Malware|Ransomware|Botnet|Cryptojacking|Phishing|Business Email Compromise|Scam/Fraud|Credential Theft|Intrusion|Data Breach|DDoS|Supply Chain|Advisory|Other|Unknown"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        f"- topics は原則 {count} 件返す。categories がある場合はまず各カテゴリから最大1件ずつ選び、不足分は関連性の高い追加トピックで埋める\n"
        "- requested_category は categories に指定された文字列をそのまま使う。categories が未指定の場合のみ自由記述可\n"
        "- 各 topic は requested_category が指す分野やテーマに直接対応させ、周辺話題や比喩的な一致で埋めない\n"
        "- information_sources には実際に参照した情報ソースを 2〜3 件入れる\n"
        "- x_search で有用な X 投稿を参照した場合は、information_sources に source_type=x を少なくとも1件含める\n"
        "概要:\n"
        "- summary_html は許可タグのみ、属性禁止\n"
        "- article title に相当する title を除き、すべての出力項目は日本語で書く\n"
        "- summary_html・lessons.body は常体で書き、です・ます調は使わない\n"
        "- summary_html・technical_terms.explanation・lessons.body は「〜だ」「〜である」で終えない\n"
        "- 3〜4文で書く\n"
        "強調ルール:\n"
        "- 意思決定に影響する語句のみ <strong>…</strong> で強調\n"
        "- 最大6箇所、短いフレーズ単位\n"
        "文字量:\n"
        "- summary_html 全体で350〜550文字程度\n"
        "technical_terms（用語解説）ルール:\n"
        "- technical_terms は最大 2 件で、固有名詞または技術用語のみを選ぶ。一般語や抽象語は避ける\n"
        "- 同じ用語が過去にも使われていることを前提とし、毎回同じ説明を繰り返さない\n"
        "- 今回の記事文脈で「なぜ重要か」に焦点を当てる\n"
        "- 可能な限り対立概念・混同されやすい概念と対比して説明する\n"
        "- explanation は100文字以内、日本語2〜3文で簡潔に説明し、「〜だ」「〜である」で終えない\n"
        "lessons（深掘り解説）ルール:\n"
        "- 記事中の用語、製品情報、法令、制度などから最も学習価値が高いものを1つだけ選ぶ\n"
        "- 本質を短く教える補助教材として書く。単なる用語の言い換えは禁止\n"
        "- 3〜5分で理解できる分量を目安にし、具体例を必ず含める\n"
        "- 同じ用語でも毎回切り口を固定せず、記事文脈に最も合う軸を選ぶ\n"
        "- 適切な題材が見当たらない場合は lessons に 1 件だけ title=なし, body=なし を入れる\n"
        "- published_at は ISO8601\n"
    )


_FEATURED_TOPICS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string"},
                    "requested_category": {"type": "string"},
                    "title": {"type": "string"},
                    "source_name": {"type": "string"},
                    "published_at": {"type": "string"},
                    "original_url": {"type": "string"},
                    "selection_reason": {"type": "string"},
                    "information_sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "source_type": {"type": "string"},
                            },
                            "required": ["title", "url", "source_type"],
                            "additionalProperties": False,
                        },
                    },
                    "summary_html": {"type": "string"},
                    "technical_terms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "term": {"type": "string"},
                                "explanation": {"type": "string"},
                            },
                            "required": ["term", "explanation"],
                            "additionalProperties": False,
                        },
                    },
                    "lessons": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["title", "body"],
                            "additionalProperties": False,
                        },
                    },
                    "impact_level": {"type": "string"},
                    "impact_reason": {"type": "string"},
                    "threat_type": {"type": "string"},
                },
                "required": [
                    "topic_id",
                    "requested_category",
                    "title",
                    "source_name",
                    "published_at",
                    "original_url",
                    "selection_reason",
                    "information_sources",
                    "summary_html",
                    "technical_terms",
                    "lessons",
                    "impact_level",
                    "impact_reason",
                    "threat_type",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}


def _featured_topics_response_schema(count: int) -> dict[str, Any]:
    schema = deepcopy(_FEATURED_TOPICS_RESPONSE_SCHEMA)
    topics_schema = schema["properties"]["topics"]
    topics_schema["minItems"] = 1
    topics_schema["maxItems"] = count
    return schema


def _build_user_prompt(*, window_start_utc: datetime, window_end_utc: datetime, settings: FeaturedTopicsSettings) -> str:
    category_text = ", ".join(settings.categories) if settings.categories else "指定なし"
    return "\n".join(
        [
            _schema_hint(settings.count),
            "調査条件:",
            f"- 対象期間: {window_start_utc.isoformat()} 以上 {window_end_utc.isoformat()} 未満",
            f"- 抽出件数: 最大 {settings.count} 件",
            f"- 優先カテゴリ: {category_text}",
            "- x_search または web_search を使って根拠を集めること。片方で十分な根拠が得られる場合は両方使わなくてよい",
            "- X 投稿を含めるが、ニュース/公式情報でも裏づけること",
            "- X 投稿が実際に有用な根拠になった場合は、その投稿も情報ソースに含めること",
            "- 出力では各トピックについて参照した情報ソースを 2〜3 件明記すること",
            "- categories は固定候補ではなく自由なテーマ名として解釈し、その語が指す分野に直接属する話題を選ぶこと",
            "- categories が指定されている場合、requested_category には指定されたカテゴリ名をそのまま使うこと",
            "- トピック候補を選ぶ前に、少なくとも異なる発信元・業界・技術領域から複数候補を比較し、同種の調査会社レポートや年次予測だけに寄せないこと",
            "- Gartner の IT トレンド予測のような汎用的な予測・ランキング・調査レポートは、対象期間内に新しい発表や具体的な波及が確認できる場合だけ選ぶこと",
            "- 似たテーマの候補がある場合は、より具体的な製品発表、規制変更、研究成果、導入事例、市場・企業行動、開発者向け変更を優先すること",
            "- selection_reason には、そのトピックが他の候補より新規性・具体性・分野の多様性で優れる理由を含めること",
            "- article title に相当する title を除き、summary_html・selection_reason・impact_reason・用語解説・深掘り解説を含む全出力を日本語で書き、英語の文を混ぜないこと",
            "- キーワード解説は固有名詞や技術的なワードの解説だけに絞り、今回の記事文脈でなぜ重要かが伝わる説明にすること",
            "- キーワード解説は可能な限り類似概念と対比しつつ、日本語2〜3文・100文字以内で、「〜だ」「〜である」で終えないこと",
            "- 深掘り解説は、記事に関連する別の論点や制度、技術、背景トピックから学習価値が最も高い題材を1つだけ選び、具体例を含めて説明すること",
            "- 深掘り解説に適切な題材がない場合は、lessons に title=なし, body=なし の1件だけを入れること",
            "- 概要は3〜4文、350〜550文字程度で、意思決定に影響する短い語句だけを <strong>…</strong> で強調すること",
            "- 全体として5分程度で読める量と密度にすること",
            "- 各 topic_id は featured-topic-1, featured-topic-2 のように連番にすること",
            "- categories が指定されている場合は、まず各カテゴリに対応する topic を優先して返すこと",
            "- 指定件数に満たない場合は、指定カテゴリに近い話題や同日に重要度の高い周辺トピックで埋めて、原則として指定件数を返すこと",
            *_category_guidance(settings.categories),
        ]
    )


def build_featured_topics(
    client: httpx.Client,
    *,
    cfg: GrokConfig,
    window_start_utc: datetime,
    window_end_utc: datetime,
    settings: FeaturedTopicsSettings,
    request_timeout: httpx.Timeout | float | None = None,
) -> list[FeaturedTopic]:
    if settings.count <= 0:
        return []

    payload = {
        "model": cfg.model,
        "max_output_tokens": cfg.max_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "featured_topics",
                "schema": _featured_topics_response_schema(settings.count),
                "strict": True,
            }
        },
        "tools": [
            {"type": "web_search"},
            {"type": "x_search"},
        ],
        "tool_choice": "auto",
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": FEATURED_SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": _build_user_prompt(window_start_utc=window_start_utc, window_end_utc=window_end_utc, settings=settings)}],
            },
        ],
    }
    if cfg.temperature is not None:
        payload["temperature"] = cfg.temperature

    retries = _featured_topic_http_retries()
    last_exc: Exception | None = None
    parsed: dict[str, Any] = {}
    model_version = cfg.model
    for attempt in range(retries + 1):
        try:
            res = client.post(
                f"{GROK_BASE_URL}/responses",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json=payload,
                timeout=request_timeout,
            )
            status_code = getattr(res, "status_code", 200)
            if _is_retryable_featured_topic_status(status_code) and attempt < retries:
                logger.warning(
                    "Retrying Grok featured topic request after server error: attempt=%s/%s model=%s status=%s body_excerpt=%r",
                    attempt + 2,
                    retries + 1,
                    cfg.model,
                    status_code,
                    _response_debug_excerpt(getattr(res, "text", "")),
                )
                close = getattr(res, "close", None)
                if callable(close):
                    close()
                continue

            try:
                res.raise_for_status()
            except httpx.HTTPStatusError:
                logger.warning(
                    "Grok featured topic request failed: model=%s status=%s body_excerpt=%r",
                    cfg.model,
                    getattr(res, "status_code", "unknown"),
                    _response_debug_excerpt(getattr(res, "text", "")),
                )
                raise

            data = res.json()
            content = _extract_response_text(data)
            model_version = _extract_response_model(data) or cfg.model
            try:
                parsed = _extract_json_object(content)
            except Exception:
                if attempt >= retries:
                    logger.exception(
                        "Failed to parse Grok featured topic response as JSON: model=%s output_excerpt=%r",
                        model_version,
                        _response_debug_excerpt(content),
                    )
                    raise
                logger.warning(
                    "Retrying Grok featured topic request after invalid JSON object response: attempt=%s/%s model=%s output_excerpt=%r",
                    attempt + 2,
                    retries + 1,
                    model_version,
                    _response_debug_excerpt(content),
                )
                close = getattr(res, "close", None)
                if callable(close):
                    close()
                continue
            break
        except Exception as exc:
            if not _is_retryable_featured_topic_error(exc) or attempt >= retries:
                raise
            last_exc = exc
            logger.warning(
                "Retrying Grok featured topic request after transient error: attempt=%s/%s model=%s error=%s",
                attempt + 2,
                retries + 1,
                cfg.model,
                exc,
            )
    else:
        raise last_exc or RuntimeError("featured topic request failed without exception")

    raw_topics = parsed.get("topics")
    if not isinstance(raw_topics, list):
        logger.warning(
            "Grok featured topic response has no topics array: model=%s response_type=%s",
            model_version,
            type(raw_topics).__name__,
        )
        return []

    topics: list[FeaturedTopic] = []
    rejection_counts = {
        "not_object": 0,
        "missing_required_fields": 0,
        "category_mismatch": 0,
        "invalid_published_at": 0,
    }
    candidates = raw_topics[: settings.count]
    for index, raw_topic in enumerate(candidates, start=1):
        if not isinstance(raw_topic, dict):
            rejection_counts["not_object"] += 1
            continue
        analysis = _coerce_analysis_result(raw_topic)
        analysis.model_version = model_version
        topic_id = str(raw_topic.get("topic_id") or f"featured-topic-{index}").strip() or f"featured-topic-{index}"
        title = str(raw_topic.get("title") or "").strip()
        source_name = str(raw_topic.get("source_name") or "Grok web/x search").strip() or "Grok web/x search"
        original_url = str(raw_topic.get("original_url") or "").strip()
        published_at_raw = str(raw_topic.get("published_at") or "").strip()
        selection_reason = str(raw_topic.get("selection_reason") or "").strip()
        requested_category = _resolve_requested_category(str(raw_topic.get("requested_category") or "").strip(), settings.categories)
        raw_sources = raw_topic.get("information_sources")
        if not title or not original_url or not published_at_raw:
            rejection_counts["missing_required_fields"] += 1
            continue
        if _looks_mismatched_for_category(
            requested_category=requested_category,
            threat_type=analysis.threat_type,
            title=title,
            summary_html=analysis.summary_html,
        ):
            rejection_counts["category_mismatch"] += 1
            continue
        try:
            published_at = datetime.fromisoformat(published_at_raw.replace("Z", "+00:00"))
        except Exception:
            rejection_counts["invalid_published_at"] += 1
            continue

        information_sources: list[InformationSource] = []
        if isinstance(raw_sources, list):
            for raw_source in raw_sources[:5]:
                if not isinstance(raw_source, dict):
                    continue
                source_title = str(raw_source.get("title") or raw_source.get("url") or "").strip()
                source_url = str(raw_source.get("url") or "").strip()
                source_type = str(raw_source.get("source_type") or "web").strip() or "web"
                if not source_title or not source_url:
                    continue
                information_sources.append(
                    InformationSource(title=source_title, url=source_url, source_type=source_type)
                )

        topics.append(
            FeaturedTopic(
                topic_id=topic_id,
                title=title,
                source_name=source_name,
                published_at=published_at,
                original_url=original_url,
                analysis=analysis,
                selection_reason=selection_reason,
                requested_category=requested_category,
                information_sources=information_sources,
            )
        )
    if not topics:
        logger.warning(
            "Grok featured topic response yielded no accepted topics: model=%s requested=%s candidates=%s rejected=%s",
            model_version,
            settings.count,
            len(candidates),
            rejection_counts,
        )
    elif any(rejection_counts.values()):
        logger.info(
            "Some Grok featured topic candidates were rejected: model=%s requested=%s candidates=%s accepted=%s rejected=%s",
            model_version,
            settings.count,
            len(candidates),
            len(topics),
            rejection_counts,
        )
    return topics
