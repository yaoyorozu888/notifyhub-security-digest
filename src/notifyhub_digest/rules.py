from __future__ import annotations

import re
from dataclasses import dataclass

from notifyhub_digest.models import RuleSeverity

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

_HIGH_KEYWORDS = [
    "zero-day",
    "0day",
    "0-day",
    "actively exploited",
    "active exploitation",
    "in-the-wild",
    "ransomware",
    "wiper",
    "mass exploitation",
    "exploited in the wild",
]


@dataclass(frozen=True)
class RuleResult:
    severity: RuleSeverity
    reason: str


def evaluate_rule(title: str, text_hint: str | None = None) -> RuleResult:
    """MVPルール

    - HIGH: zero-day / actively exploited / ransomware 等
    - MEDIUM: CVE番号を含む
    - LOW: その他

    判定根拠は人が見て理解できる短文で返す。
    """

    hay = (title or "").strip()
    extra = (text_hint or "").strip()
    combined = (hay + "\n" + extra).lower()

    hits = [kw for kw in _HIGH_KEYWORDS if kw in combined]
    if hits:
        return RuleResult(
            severity="HIGH",
            reason="HIGH: キーワード一致: " + ", ".join(sorted(set(hits))) + "\n（MVPルール）",
        )

    cve = _CVE_RE.search(hay) or _CVE_RE.search(extra)
    if cve:
        return RuleResult(severity="MEDIUM", reason=f"MEDIUM: CVE検出: {cve.group(0)}\n（MVPルール）")

    return RuleResult(severity="LOW", reason="LOW: 該当なし（MVPルール）")
