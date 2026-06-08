from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from notifyhub_digest.maintenance import cleanup_old_digests
from notifyhub_digest.timeutils import JST


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete digest directories older than the retention window.")
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument("--retain-days", type=int, default=90)
    parser.add_argument("--run-at", type=str, default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    run_at = datetime.now(JST) if not args.run_at else datetime.fromisoformat(args.run_at)
    result = cleanup_old_digests(args.site_dir, now=run_at, retain_days=args.retain_days)

    print(f"Removed {len(result.removed)} digest directories")
    for path in result.removed:
        print(path.as_posix())

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"removed_count={len(result.removed)}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
