from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

from notifyhub_digest.models import FeedItem


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcsEmailConfig:
    connection_string: str
    sender_address: str
    to_addresses: tuple[str, ...]


def _split_addresses(raw: str) -> tuple[str, ...]:
    # allow comma/semicolon/whitespace separated list
    parts: list[str] = []
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts.append(chunk)
    if not parts:
        return tuple()
    return tuple(parts)


def load_acs_email_config() -> AcsEmailConfig | None:
    """Load ACS Email config from environment.

    Required when enabled:
    - ACS_EMAIL_CONNECTION_STRING
    - ACS_EMAIL_SENDER
    - ACS_EMAIL_TO (comma separated)
    """

    conn = os.getenv("ACS_EMAIL_CONNECTION_STRING")
    sender = os.getenv("ACS_EMAIL_SENDER")
    to_raw = os.getenv("ACS_EMAIL_TO")

    if not conn or not sender or not to_raw:
        return None

    to_addrs = _split_addresses(to_raw)
    if not to_addrs:
        return None

    return AcsEmailConfig(connection_string=conn, sender_address=sender.strip(), to_addresses=to_addrs)


def _safe_url(url: str) -> str:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return "#"
        return url
    except Exception:
        return "#"


def build_digest_email_subject(*, day: str) -> str:
    prefix = os.getenv("ACS_EMAIL_SUBJECT_PREFIX", "NotifyHub CSIRT Daily Report")
    prefix = prefix.strip() or "NotifyHub CSIRT Daily Report"
    return f"{prefix} {day}"


def _load_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _render_template(template: str, mapping: dict[str, str]) -> str:
    out = template
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def _render_items_html(*, items: list[FeedItem], digest_root_url: str) -> str:
    if not items:
        return (
            "<div style=\"padding:12px 14px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;\">"
            "この期間の新着はありませんでした。"
            "</div>"
        )

    blocks: list[str] = []
    for it in items:
        title = html.escape(it.title)
        src = html.escape(it.source_name)
        sev = html.escape(it.rule_severity)
        impact = html.escape(it.analysis.impact_level)
        threat = html.escape(it.analysis.threat_type)
        reason = html.escape(it.rule_reason)

        original = _safe_url(it.original_url)
        original_attr = html.escape(original, quote=True)

        article_url = _safe_url(urljoin(_safe_url(digest_root_url), it.article_path))
        article_attr = html.escape(article_url, quote=True)

        # summary_html is already strictly sanitized (allowlist tags, no attrs)
        summary = it.analysis.summary_html or ""

        blocks.append(
            "\n".join(
                [
                    '<div style="padding:14px;border:1px solid #e5e7eb;border-radius:12px;margin-bottom:12px;">',
                    '  <div style="font-size:14px;line-height:1.4;">'
                    f"    <span style=\"display:inline-block;padding:2px 8px;border-radius:999px;background:#111827;color:#fff;font-size:12px;\">{sev}</span> "
                    f"    <a href=\"{original_attr}\" style=\"color:#111827;text-decoration:none;font-weight:700;\">{title}</a>"
                    f"    <span style=\"font-size:12px;margin-left:6px;\"><a href=\"{article_attr}\" style=\"color:#2563eb;\">(Web版)</a></span>"
                    f"    <span style=\"color:#6b7280;font-size:12px;\"> ({src})</span>"
                    "  </div>",
                    f'  <div style="margin-top:6px;font-size:12px;color:#374151;">Impact: <strong>{impact}</strong> / Threat: <strong>{threat}</strong></div>',
                    f'  <div style="margin-top:6px;font-size:12px;color:#6b7280;">Rule: {reason}</div>',
                    (f'  <div style="margin-top:10px;font-size:13px;color:#111827;line-height:1.6;">{summary}</div>' if summary else ""),
                    "</div>",
                ]
            )
        )

    return "\n".join(blocks)


def build_digest_email_html(
    *,
    day: str,
    digest_root_url: str,
    window_from_jst: str,
    window_to_jst: str,
    generated_at_jst: str,
    items: list[FeedItem],
) -> str:
    digest_url = _safe_url(digest_root_url)
    subject = build_digest_email_subject(day=day)

    template_path = Path(__file__).resolve().parent / "templates" / "email.html"
    template = _load_template(template_path)

    mapping = {
        "subject": html.escape(subject),
        "window_from_jst": html.escape(window_from_jst),
        "window_to_jst": html.escape(window_to_jst),
        "generated_at_jst": html.escape(generated_at_jst),
        "digest_url": html.escape(digest_url, quote=True),
        "count_total": html.escape(str(len(items))),
        "items_html": _render_items_html(items=items, digest_root_url=digest_root_url),
    }

    return _render_template(template, mapping)


def send_acs_email(*, cfg: AcsEmailConfig, subject: str, html_body: str, plain_text: str | None = None) -> bool:
    """Send an email via ACS Email.

    Returns True only when the service reports Succeeded.
    """

    try:
        from azure.communication.email import EmailClient
    except ModuleNotFoundError as e:
        raise RuntimeError("ACS Email requires extras: pip install '.[acs]'") from e

    message = {
        "senderAddress": cfg.sender_address,
        "recipients": {"to": [{"address": addr} for addr in cfg.to_addresses]},
        "content": {"subject": subject, "html": html_body},
    }
    if plain_text:
        message["content"]["plainText"] = plain_text

    client = EmailClient.from_connection_string(cfg.connection_string)
    poller = client.begin_send(message)
    result = poller.result()

    # azure.communication.email may return an object (EmailSendResult) or dict-like.
    raw_status = getattr(result, "status", None)
    if raw_status is None and isinstance(result, dict):
        raw_status = result.get("status")
    status = str(raw_status or "").strip()

    raw_id = getattr(result, "id", None)
    if raw_id is None and isinstance(result, dict):
        raw_id = result.get("id")
    op_id = str(raw_id or "").strip() or None

    logger.info("ACS Email send result: status=%s id=%s", status or "(empty)", op_id or "(none)")
    return status.lower() == "succeeded"


def require_acs_email_env() -> None:
    missing: list[str] = []
    for key in ("ACS_EMAIL_CONNECTION_STRING", "ACS_EMAIL_SENDER", "ACS_EMAIL_TO"):
        if not os.getenv(key):
            missing.append(key)
    if missing:
        raise RuntimeError(
            "ACS Email is enabled but required env vars are missing: " + ", ".join(missing)
        )


def normalize_digest_base_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return "https://notifyhub.site/digest"
    return raw[:-1] if raw.endswith("/") else raw


def should_send_email(flag: bool | None = None) -> bool:
    if flag is not None:
        return bool(flag)
    v = os.getenv("ACS_EMAIL_ENABLED", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def iter_unique_entry_ids(items: Iterable[FeedItem]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it.entry_id in seen:
            continue
        seen.add(it.entry_id)
        out.append(it.entry_id)
    return out
