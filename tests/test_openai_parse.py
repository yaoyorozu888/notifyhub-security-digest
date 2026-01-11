from __future__ import annotations

import pytest

from notifyhub_digest.openai_client import _extract_json_object


def test_extract_json_plain() -> None:
    obj = _extract_json_object('{"a": 1, "b": "x"}')
    assert obj["a"] == 1
    assert obj["b"] == "x"


def test_extract_json_code_fence() -> None:
    obj = _extract_json_object('```json\n{"a": 1}\n```')
    assert obj == {"a": 1}


def test_extract_json_with_preface() -> None:
    obj = _extract_json_object('OK. Here you go: {"a": 1, "b": 2} Thanks')
    assert obj == {"a": 1, "b": 2}


def test_extract_json_empty_raises() -> None:
    with pytest.raises(ValueError):
        _extract_json_object("")
