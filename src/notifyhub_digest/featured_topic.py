from __future__ import annotations

import os
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


GROK_BASE_URL = "https://api.x.ai/v1"


@dataclass(frozen=True)
class GrokConfig:
    api_key: str
    model: str = "grok-4.3"
    max_tokens: int = 2200
    temperature: float = 0.3


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
    "必ず x_search と web_search の両方を使い、過去一日の X 投稿とニュースサイトを横断して調査してください。\n"
    "検索をせずに既知知識だけで答えることは禁止です。\n"
    "\n"
    "選定原則:\n"
    "- 読者の実務判断や社会的理解に寄与するトピックを優先する\n"
    "- 単なる話題性、噂、宣伝、一般論は避ける\n"
    "- セキュリティ、AI、法務・規制、政策、企業動向、研究など幅広い分野を対象にしてよい\n"
    "- 何が重要か、どこに影響するか、今なぜ注目すべきかを説明できるものを優先する\n"
    "- X 投稿を選ぶ場合でも、根拠が薄い単発投稿は避け、裏づけ可能な情報を優先する\n"
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

    is_security = _contains_any_keyword(normalized, _CYBERSECURITY_CATEGORY_KEYWORDS)
    is_ai = _contains_any_keyword(normalized, _AI_CATEGORY_KEYWORDS)
    is_tech_trend = _contains_any_keyword(normalized, _TECH_TREND_CATEGORY_KEYWORDS)

    if is_security:
        return CategoryPolicy(
            category=category,
            normalized=normalized,
            guidance=f'- カテゴリ「{category}」では、脆弱性悪用、侵害、漏えい、マルウェア、規制対応などセキュリティ実務に直結する話題を優先すること',
        )

    if is_ai:
        return CategoryPolicy(
            category=category,
            normalized=normalized,
            guidance=f'- カテゴリ「{category}」では、AIモデル、AI製品、AI研究、AI規制、AI導入事例などAIそのものの進展を扱い、単なるセキュリティ事故の要約は避けること',
        )

    if is_tech_trend:
        return CategoryPolicy(
            category=category,
            normalized=normalized,
            guidance=f'- カテゴリ「{category}」では、クラウド、開発者ツール、半導体、AI基盤、データ基盤、SaaS、OS、デバイス、ネットワーク、オープンソース、エンタープライズITなどの技術トレンドを優先し、サイバー攻撃・脆弱性・情報漏えい・インシデント対応そのものは原則として選ばないこと',
            exclude_cybersecurity_incidents=True,
        )

    return CategoryPolicy(category=category, normalized=normalized)


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


def load_grok_config() -> GrokConfig | None:
    api_key = (os.getenv("GROK_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (os.getenv("GROK_MODEL") or "").strip() or "grok-4.3"
    try:
        max_tokens = int(os.getenv("GROK_MAX_TOKENS", "2200"))
    except Exception:
        max_tokens = 2200
    try:
        temperature = float(os.getenv("GROK_TEMPERATURE", "0.3"))
    except Exception:
        temperature = 0.3
    return GrokConfig(
        api_key=api_key,
        model=model,
        max_tokens=max(800, min(max_tokens, 4000)),
        temperature=max(0.0, min(temperature, 1.0)),
    )


def load_featured_topics_settings() -> FeaturedTopicsSettings:
    try:
        count = int(os.getenv("FEATURED_TOPIC_COUNT", "1"))
    except Exception:
        count = 1
    count = max(0, min(count, 10))
    raw_categories = (os.getenv("FEATURED_TOPIC_CATEGORIES") or "").strip()
    categories = [part.strip() for part in raw_categories.split(",") if part.strip()]
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
        "- information_sources には実際に参照した情報ソースを 2〜5 件入れる\n"
        "- x_search で有用な X 投稿を参照した場合は、information_sources に source_type=x を少なくとも1件含める\n"
        "- summary_html は許可タグのみ、属性禁止\n"
        "- summary_html は 450〜700 文字程度で、概要を短時間で把握できる密度にする\n"
        "- technical_terms は最大 3 件で、固有名詞または技術用語のみを選ぶ。一般語や抽象語は避ける\n"
        "- technical_terms の explanation は、その語が何かを日本語1〜2文で簡潔に説明する\n"
        "- lessons は必ず 1 件入れ、記事そのものの言い換えではなく関連トピックの深掘り解説にする\n"
        "- lessons.body は 250〜500 文字程度で、5分以内に読める要点整理にする\n"
        "- published_at は ISO8601\n"
    )


def _build_user_prompt(*, window_start_utc: datetime, window_end_utc: datetime, settings: FeaturedTopicsSettings) -> str:
    category_text = ", ".join(settings.categories) if settings.categories else "指定なし"
    return "\n".join(
        [
            _schema_hint(settings.count),
            "調査条件:",
            f"- 対象期間: {window_start_utc.isoformat()} 以上 {window_end_utc.isoformat()} 未満",
            f"- 抽出件数: 最大 {settings.count} 件",
            f"- 優先カテゴリ: {category_text}",
            "- 必ず x_search と web_search を両方使うこと",
            "- X 投稿を含めるが、ニュース/公式情報でも裏づけること",
            "- X 投稿が実際に有用な根拠になった場合は、その投稿も情報ソースに含めること",
            "- 出力では各トピックについて参照した情報ソースを 2〜5 件明記すること",
            "- キーワード解説は固有名詞や技術的なワードの解説だけに絞ること",
            "- 深掘り解説は、記事に関連する別の論点や制度、技術、背景トピックを1つ選んで説明すること",
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
) -> list[FeaturedTopic]:
    if settings.count <= 0:
        return []

    payload = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_output_tokens": cfg.max_tokens,
        "text": {"format": {"type": "json_object"}},
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

    res = client.post(
        f"{GROK_BASE_URL}/responses",
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        json=payload,
    )
    res.raise_for_status()

    data = res.json()
    content = _extract_response_text(data)
    model_version = _extract_response_model(data) or cfg.model
    parsed: dict[str, Any] = _extract_json_object(content)
    raw_topics = parsed.get("topics")
    if not isinstance(raw_topics, list):
        return []

    topics: list[FeaturedTopic] = []
    for index, raw_topic in enumerate(raw_topics[: settings.count], start=1):
        if not isinstance(raw_topic, dict):
            continue
        analysis = _coerce_analysis_result(raw_topic)
        analysis.model_version = model_version
        topic_id = str(raw_topic.get("topic_id") or f"featured-topic-{index}").strip() or f"featured-topic-{index}"
        title = str(raw_topic.get("title") or "").strip()
        source_name = str(raw_topic.get("source_name") or "Grok web/x search").strip() or "Grok web/x search"
        original_url = str(raw_topic.get("original_url") or "").strip()
        published_at_raw = str(raw_topic.get("published_at") or "").strip()
        selection_reason = str(raw_topic.get("selection_reason") or "").strip()
        requested_category = str(raw_topic.get("requested_category") or "").strip()
        raw_sources = raw_topic.get("information_sources")
        if not title or not original_url or not published_at_raw:
            continue
        if _looks_mismatched_for_category(
            requested_category=requested_category,
            threat_type=analysis.threat_type,
            title=title,
            summary_html=analysis.summary_html,
        ):
            continue
        try:
            published_at = datetime.fromisoformat(published_at_raw.replace("Z", "+00:00"))
        except Exception:
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
    return topics