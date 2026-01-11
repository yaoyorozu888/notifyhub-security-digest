from __future__ import annotations

import json
from pathlib import Path

from notifyhub_digest.models import Source


def load_sources(path: Path) -> list[Source]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Source.model_validate(x) for x in data]
