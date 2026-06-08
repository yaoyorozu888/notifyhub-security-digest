from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from notifyhub_digest.maintenance import create_backup_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a digest backup archive and manifest.")
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["full", "daily"], required=True)
    parser.add_argument("--run-at", type=str, required=True)
    parser.add_argument("--day", type=str, default="")
    parser.add_argument("--commit-sha", type=str, default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    artifact = create_backup_artifact(
        args.site_dir,
        output_dir=args.output_dir,
        mode=args.mode,
        run_at=datetime.fromisoformat(args.run_at),
        day=args.day or None,
        commit_sha=args.commit_sha or None,
    )

    print(f"Archive: {artifact.archive_path}")
    print(f"Manifest: {artifact.manifest_path}")
    print(f"Blob prefix: {artifact.blob_prefix}")

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"archive_path={artifact.archive_path}\n")
            fh.write(f"manifest_path={artifact.manifest_path}\n")
            fh.write(f"blob_prefix={artifact.blob_prefix}\n")
            fh.write(f"member_count={len(artifact.members)}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
