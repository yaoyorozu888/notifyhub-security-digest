from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    sources_path: Path
    out_dir: Path
    state_dir: Path
    digest_base_url: str
    user_agent: str
    http_timeout_seconds: float


def default_config(repo_root: Path) -> AppConfig:
    return AppConfig(
        sources_path=repo_root / "sources.json",
        out_dir=repo_root / "out",
        state_dir=repo_root / "state",
        digest_base_url="https://notifyhub.site/digest",
        user_agent="notifyhub-security-digest/0.1 (+https://notifyhub.site)",
        http_timeout_seconds=20.0,
    )
