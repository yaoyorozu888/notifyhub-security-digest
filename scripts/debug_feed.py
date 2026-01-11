from __future__ import annotations

import argparse
from pathlib import Path

import feedparser
import httpx


def _summarize_entry(e) -> dict:
    d = dict(e)
    keys = sorted(d.keys())

    def g(*names):
        for n in names:
            v = getattr(e, n, None)
            if v:
                return v
            if n in d and d[n]:
                return d[n]
        return None

    return {
        "keys": keys,
        "title": g("title"),
        "link": g("link"),
        "id": g("id", "guid"),
        "published": g("published"),
        "updated": g("updated"),
        "created": g("created"),
        "published_parsed": bool(g("published_parsed")),
        "updated_parsed": bool(g("updated_parsed")),
        "created_parsed": bool(g("created_parsed")),
        "links": d.get("links"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    transport = httpx.HTTPTransport(retries=2)
    with httpx.Client(timeout=args.timeout, follow_redirects=True, transport=transport) as client:
        res = client.get(
            args.url,
            headers={
                "User-Agent": "notifyhub-security-digest/0.1",
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Encoding": "identity",
            },
        )
        print("status", res.status_code)
        print("content-type", res.headers.get("content-type"))
        head = res.text[:200].replace("\n", " ")
        print("head", head)

        parsed = feedparser.parse(res.content)
        print("feed bozo", getattr(parsed, "bozo", None))
        if getattr(parsed, "bozo", 0):
            print("bozo_exception", getattr(parsed, "bozo_exception", None))
        entries = list(parsed.entries or [])
        print("entries", len(entries))

        out = [_summarize_entry(e) for e in entries[: args.limit]]
        for i, e in enumerate(out, start=1):
            print("--- entry", i)
            for k in ("title", "link", "id", "published", "updated", "created", "published_parsed", "updated_parsed", "created_parsed"):
                print(f"{k}: {e.get(k)}")
            if e.get("links"):
                print("links:", e.get("links"))
            print("keys:", e.get("keys"))

        if args.out:
            import json

            args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print("wrote", args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
