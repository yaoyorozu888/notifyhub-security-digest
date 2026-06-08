from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import tarfile

from notifyhub_digest.timeutils import JST


@dataclass(frozen=True)
class CleanupResult:
    removed: tuple[Path, ...]
    kept: tuple[Path, ...]


@dataclass(frozen=True)
class BackupArtifact:
    archive_path: Path
    manifest_path: Path
    blob_prefix: str
    members: tuple[Path, ...]


def _iter_digest_day_dirs(digest_root: Path) -> list[tuple[datetime, Path]]:
    day_dirs: list[tuple[datetime, Path]] = []
    for year_dir in sorted(digest_root.iterdir() if digest_root.exists() else []):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir() or not day_dir.name.isdigit():
                    continue
                try:
                    day = datetime(
                        int(year_dir.name),
                        int(month_dir.name),
                        int(day_dir.name),
                        tzinfo=JST,
                    )
                except ValueError:
                    continue
                day_dirs.append((day, day_dir))
    return day_dirs


def expired_digest_dirs(site_dir: Path, *, now: datetime, retain_days: int = 90) -> tuple[Path, ...]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if retain_days < 1:
        raise ValueError("retain_days must be >= 1")

    site_dir = site_dir.resolve()
    digest_root = (site_dir / "digest").resolve()
    cutoff_day = (now.astimezone(JST) - timedelta(days=retain_days)).date()
    expired: list[Path] = []

    for day, day_dir in _iter_digest_day_dirs(digest_root):
        if day.date() < cutoff_day:
            expired.append(day_dir.relative_to(site_dir))

    return tuple(expired)


def cleanup_old_digests(site_dir: Path, *, now: datetime, retain_days: int = 90) -> CleanupResult:
    site_dir = site_dir.resolve()
    removed: list[Path] = []
    kept: list[Path] = []
    expired = set(expired_digest_dirs(site_dir, now=now, retain_days=retain_days))

    for _, day_dir in _iter_digest_day_dirs((site_dir / "digest").resolve()):
        rel_path = day_dir.relative_to(site_dir)
        if rel_path in expired:
            shutil.rmtree(day_dir)
            removed.append(rel_path)
        else:
            kept.append(rel_path)

    return CleanupResult(removed=tuple(removed), kept=tuple(kept))


def _backup_members(
    site_dir: Path,
    *,
    mode: str,
    day: str | None,
) -> tuple[str, list[Path]]:
    site_dir = site_dir.resolve()
    digest_root = site_dir / "digest"
    if not digest_root.exists():
        raise FileNotFoundError(f"Missing digest root: {digest_root}")

    root_candidates = [
        site_dir / "index.html",
        digest_root / "index.html",
        digest_root / "latest" / "index.html",
    ]
    members: list[Path] = [path for path in root_candidates if path.exists()]

    if mode == "full":
        blob_prefix = "full"
        members.extend(path for _, path in _iter_digest_day_dirs(digest_root))
        return blob_prefix, sorted(set(members), key=lambda p: p.as_posix())

    if mode == "daily":
        if day is None:
            raise ValueError("day is required for daily backup")
        day_parts = day.split("-")
        if len(day_parts) != 3:
            raise ValueError("day must be in YYYY-MM-DD format")
        day_dir = digest_root / day_parts[0] / day_parts[1] / day_parts[2]
        if not day_dir.exists():
            raise FileNotFoundError(f"Missing digest day directory: {day_dir}")
        members.append(day_dir)
        return f"daily/{day}", sorted(set(members), key=lambda p: p.as_posix())

    raise ValueError("mode must be 'full' or 'daily'")


def create_backup_artifact(
    site_dir: Path,
    *,
    output_dir: Path,
    mode: str,
    run_at: datetime,
    day: str | None = None,
    commit_sha: str | None = None,
) -> BackupArtifact:
    if run_at.tzinfo is None:
        raise ValueError("run_at must be timezone-aware")

    site_dir = site_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    blob_prefix, members = _backup_members(
        site_dir,
        mode=mode,
        day=day,
    )

    stamp = run_at.astimezone(JST).strftime("%Y%m%dT%H%M%S%z")
    archive_path = output_dir / f"digest-backup-{mode}-{stamp}.tar.gz"
    manifest_path = output_dir / f"digest-backup-{mode}-{stamp}.json"

    with tarfile.open(archive_path, "w:gz") as tar:
        for member in members:
            tar.add(member, arcname=member.relative_to(site_dir))

    manifest = {
        "mode": mode,
        "day": day,
        "blob_prefix": blob_prefix,
        "run_at_jst": run_at.astimezone(JST).isoformat(),
        "commit_sha": commit_sha,
        "members": [str(member.relative_to(site_dir)).replace("\\", "/") for member in members],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return BackupArtifact(
        archive_path=archive_path,
        manifest_path=manifest_path,
        blob_prefix=blob_prefix,
        members=tuple(member.relative_to(site_dir) for member in members),
    )
