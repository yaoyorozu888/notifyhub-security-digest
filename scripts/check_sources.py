from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from notifyhub_digest.rss import fetch_feed_entries
from notifyhub_digest.sources import load_sources


@dataclass(frozen=True)
class CheckResult:
    name: str
    category: str
    feed_url: str
    enabled: bool
    ok: bool
    status: str
    entry_count: int
    elapsed_ms: int


def _now_ms() -> int:
    return int(time.time() * 1000)


def check_sources(*, sources_path: Path, timeout_seconds: float, user_agent: str) -> list[CheckResult]:
    sources = load_sources(sources_path)
    results: list[CheckResult] = []

    transport = httpx.HTTPTransport(retries=2)
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True, transport=transport) as client:
        for s in sources:
            if not s.enabled:
                results.append(
                    CheckResult(
                        name=s.name,
                        category=s.category,
                        feed_url=s.feed_url,
                        enabled=False,
                        ok=True,
                        status="SKIP(disabled)",
                        entry_count=0,
                        elapsed_ms=0,
                    )
                )
                continue

            start = _now_ms()
            try:
                entries = fetch_feed_entries(client, s, user_agent=user_agent)
                elapsed = _now_ms() - start

                if not entries:
                    results.append(
                        CheckResult(
                            name=s.name,
                            category=s.category,
                            feed_url=s.feed_url,
                            enabled=True,
                            ok=False,
                            status="NO_ENTRIES",
                            entry_count=0,
                            elapsed_ms=elapsed,
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            name=s.name,
                            category=s.category,
                            feed_url=s.feed_url,
                            enabled=True,
                            ok=True,
                            status="OK",
                            entry_count=len(entries),
                            elapsed_ms=elapsed,
                        )
                    )

            except httpx.HTTPStatusError as e:
                elapsed = _now_ms() - start
                code = e.response.status_code if e.response is not None else "?"
                results.append(
                    CheckResult(
                        name=s.name,
                        category=s.category,
                        feed_url=s.feed_url,
                        enabled=True,
                        ok=False,
                        status=f"HTTP_{code}",
                        entry_count=0,
                        elapsed_ms=elapsed,
                    )
                )
            except httpx.TimeoutException:
                elapsed = _now_ms() - start
                results.append(
                    CheckResult(
                        name=s.name,
                        category=s.category,
                        feed_url=s.feed_url,
                        enabled=True,
                        ok=False,
                        status="TIMEOUT",
                        entry_count=0,
                        elapsed_ms=elapsed,
                    )
                )
            except Exception as e:
                elapsed = _now_ms() - start
                results.append(
                    CheckResult(
                        name=s.name,
                        category=s.category,
                        feed_url=s.feed_url,
                        enabled=True,
                        ok=False,
                        status=f"ERROR({type(e).__name__})",
                        entry_count=0,
                        elapsed_ms=elapsed,
                    )
                )

    return results


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Check RSS sources.json availability")
    p.add_argument("--sources", type=Path, default=Path("sources.json"), help="Path to sources.json")
    p.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    p.add_argument("--user-agent", default="notifyhub-security-digest/0.1", help="User-Agent")
    p.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional: write results as JSON to this path",
    )

    args = p.parse_args(argv)

    results = check_sources(
        sources_path=args.sources,
        timeout_seconds=args.timeout,
        user_agent=args.user_agent,
    )

    ok = sum(1 for r in results if r.ok)
    bad = sum(1 for r in results if (r.enabled and not r.ok))
    skipped = sum(1 for r in results if (not r.enabled))

    print(f"Total: {len(results)}  OK: {ok}  NG: {bad}  SKIP: {skipped}")
    for r in results:
        flag = "OK" if r.ok else "NG"
        print(
            f"[{flag}] {r.status:14} {r.elapsed_ms:5}ms  entries={r.entry_count:3}  {r.name}  {r.feed_url}"
        )

    if args.json_out:
        payload = [
            {
                "name": r.name,
                "category": r.category,
                "feed_url": r.feed_url,
                "enabled": r.enabled,
                "ok": r.ok,
                "status": r.status,
                "entry_count": r.entry_count,
                "elapsed_ms": r.elapsed_ms,
            }
            for r in results
        ]
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote: {args.json_out}")

    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
