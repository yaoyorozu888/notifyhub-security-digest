from __future__ import annotations

from notifyhub_digest.rules import evaluate_rule


def test_rule_high_keyword():
    r = evaluate_rule("Microsoft zero-day exploited")
    assert r.severity == "HIGH"


def test_rule_medium_cve():
    r = evaluate_rule("Patch for CVE-2024-12345 released")
    assert r.severity == "MEDIUM"


def test_rule_low_other():
    r = evaluate_rule("Weekly security roundup")
    assert r.severity == "LOW"
