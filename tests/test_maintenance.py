from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tarfile

from notifyhub_digest.maintenance import cleanup_old_digests, create_backup_artifact, expired_digest_dirs
from notifyhub_digest.timeutils import JST


def _make_digest_day(site_dir: Path, day_path: str) -> Path:
    day_dir = site_dir / "digest" / Path(day_path)
    day_dir.mkdir(parents=True)
    (day_dir / "index.html").write_text("ok", encoding="utf-8")
    (day_dir / "articles").mkdir()
    return day_dir


def test_cleanup_old_digests_removes_only_older_than_90_days(tmp_path: Path):
    site_dir = tmp_path / "site"
    old_dir = _make_digest_day(site_dir, "2026/03/09")
    boundary_dir = _make_digest_day(site_dir, "2026/03/10")
    fresh_dir = _make_digest_day(site_dir, "2026/06/08")

    result = cleanup_old_digests(
        site_dir,
        now=datetime(2026, 6, 8, 6, 0, 0, tzinfo=JST),
        retain_days=90,
    )

    assert not old_dir.exists()
    assert boundary_dir.exists()
    assert fresh_dir.exists()
    assert result.removed == (Path("digest/2026/03/09"),)
    assert Path("digest/2026/03/10") in result.kept
    assert Path("digest/2026/06/08") in result.kept


def test_cleanup_old_digests_ignores_latest_and_non_date_dirs(tmp_path: Path):
    site_dir = tmp_path / "site"
    _make_digest_day(site_dir, "2026/03/01")
    latest_dir = site_dir / "digest" / "latest"
    latest_dir.mkdir(parents=True)
    (latest_dir / "index.html").write_text("latest", encoding="utf-8")
    misc_dir = site_dir / "digest" / "drafts"
    misc_dir.mkdir(parents=True)

    cleanup_old_digests(
        site_dir,
        now=datetime(2026, 6, 8, 6, 0, 0, tzinfo=JST),
        retain_days=90,
    )

    assert latest_dir.exists()
    assert misc_dir.exists()


def test_cleanup_old_digests_requires_timezone_aware_now(tmp_path: Path):
    site_dir = tmp_path / "site"
    _make_digest_day(site_dir, "2026/03/01")

    try:
        cleanup_old_digests(site_dir, now=datetime(2026, 6, 8, 6, 0, 0), retain_days=90)
    except ValueError as exc:
        assert str(exc) == "now must be timezone-aware"
    else:
        raise AssertionError("cleanup_old_digests should require timezone-aware datetimes")


def test_expired_digest_dirs_returns_only_prune_candidates(tmp_path: Path):
    site_dir = tmp_path / "site"
    _make_digest_day(site_dir, "2026/03/09")
    _make_digest_day(site_dir, "2026/03/10")
    _make_digest_day(site_dir, "2026/06/08")

    result = expired_digest_dirs(
        site_dir,
        now=datetime(2026, 6, 8, 6, 0, 0, tzinfo=JST),
        retain_days=90,
    )

    assert result == (Path("digest/2026/03/09"),)


def test_create_backup_artifact_full_contains_all_digest_days_only(tmp_path: Path):
    site_dir = tmp_path / "site"
    _make_digest_day(site_dir, "2026/06/07")
    _make_digest_day(site_dir, "2026/06/08")
    (site_dir / "index.html").write_text("root", encoding="utf-8")
    (site_dir / "digest" / "index.html").write_text("digest-root", encoding="utf-8")
    latest_dir = site_dir / "digest" / "latest"
    latest_dir.mkdir(parents=True)
    (latest_dir / "index.html").write_text("latest", encoding="utf-8")
    (site_dir / "pagefind").mkdir(parents=True)
    (site_dir / "pagefind" / "pagefind-entry.json").write_text("{}", encoding="utf-8")
    (site_dir / "calendar").mkdir(parents=True)
    (site_dir / "calendar" / "index.html").write_text("calendar", encoding="utf-8")

    artifact = create_backup_artifact(
        site_dir,
        output_dir=tmp_path / "backup",
        mode="full",
        run_at=datetime(2026, 6, 8, 6, 0, 0, tzinfo=JST),
        commit_sha="abc123",
    )

    assert artifact.blob_prefix == "full"
    assert artifact.archive_path.exists()
    assert artifact.manifest_path.exists()
    assert Path("pagefind") not in artifact.members
    assert Path("calendar/index.html") not in artifact.members

    with tarfile.open(artifact.archive_path, "r:gz") as tar:
        names = set(tar.getnames())

    assert "index.html" in names
    assert "digest/index.html" in names
    assert "digest/latest/index.html" in names
    assert "digest/2026/06/07/index.html" in names
    assert "digest/2026/06/08/index.html" in names
    assert all(not name.startswith("pagefind/") for name in names)
    assert all(not name.startswith("calendar/") for name in names)

    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    assert manifest["commit_sha"] == "abc123"
    assert manifest["blob_prefix"] == "full"


def test_create_backup_artifact_daily_contains_requested_day_and_redirects(tmp_path: Path):
    site_dir = tmp_path / "site"
    _make_digest_day(site_dir, "2026/06/07")
    target_dir = _make_digest_day(site_dir, "2026/06/08")
    (site_dir / "index.html").write_text("root", encoding="utf-8")
    (site_dir / "digest" / "index.html").write_text("digest-root", encoding="utf-8")
    latest_dir = site_dir / "digest" / "latest"
    latest_dir.mkdir(parents=True)
    (latest_dir / "index.html").write_text("latest", encoding="utf-8")

    artifact = create_backup_artifact(
        site_dir,
        output_dir=tmp_path / "backup",
        mode="daily",
        run_at=datetime(2026, 6, 8, 6, 0, 0, tzinfo=JST),
        day="2026-06-08",
    )

    assert artifact.blob_prefix == "daily/2026-06-08"
    assert Path("digest/2026/06/08") in artifact.members
    assert Path("digest/2026/06/07") not in artifact.members
    assert target_dir.exists()

    with tarfile.open(artifact.archive_path, "r:gz") as tar:
        names = set(tar.getnames())

    assert "index.html" in names
    assert "digest/index.html" in names
    assert "digest/latest/index.html" in names
    assert "digest/2026/06/08/index.html" in names
    assert "digest/2026/06/07/index.html" not in names


