#!/usr/bin/env python3
"""
Instagram scraper — central entrypoint (sibling to twitter_scraper/scrape.py).

Two modes:

    user     Scrape a user's recent posts (accepts a username)
    hashtag  Scrape recent / top posts for a hashtag (accepts a tag)

Examples:
    python scrape.py user natgeo --limit 20
    python scrape.py user natgeo --since 2026-06-01
    python scrape.py hashtag photography --limit 30
    python scrape.py hashtag photography --top --out output/photography.json
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from instagram_scraper.fetch import scrape_user_posts, scrape_hashtag


HASHTAG_HELP = """\
TAG is a single Instagram hashtag (the # is optional). Instagram has no
free-text or boolean search and no server-side date filter, so:

  (default)            most-recent posts, newest first
  --top                top / ranked posts (NOT chronological)
  --since YYYY-MM-DD   keep only posts on/after DATE, walking newest->oldest
                       and stopping early. Works WITHOUT --top only; ignored
                       with --top because ranked results aren't time-ordered.

Only ONE hashtag per call — multi-tag / AND / OR is not supported by the API.
"""


def _parse_since(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--since must be YYYY-MM-DD, got {value!r}")


def main():
    # Shared options, added to each subcommand so they work after it,
    # e.g. `scrape.py user natgeo --limit 20`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=None, help="Output JSON path (default: print to stdout)")
    common.add_argument("--limit", type=int, default=20, help="Max posts to fetch (default: 20)")
    common.add_argument("--session", default=None,
                        help="Session file (default: $IG_SESSION_PATH or ./ig_session.json)")
    common.add_argument("-v", "--verbose", action="store_true")

    parser = argparse.ArgumentParser(
        description="Scrape posts from an Instagram user or a hashtag.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_user = sub.add_parser("user", parents=[common], help="Scrape a user's recent posts")
    p_user.add_argument("username", help="Instagram username, e.g. natgeo or @natgeo")
    p_user.add_argument("--since", type=_parse_since, default=None,
                        help="Only posts on/after DATE (YYYY-MM-DD); newest->oldest early-stop")

    p_tag = sub.add_parser(
        "hashtag",
        parents=[common],
        help="Scrape recent/top posts for a hashtag",
        description=HASHTAG_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_tag.add_argument("tag", help="Hashtag (with or without #), e.g. photography")
    p_tag.add_argument("--top", action="store_true", help="Top/ranked posts instead of most-recent")
    p_tag.add_argument("--since", type=_parse_since, default=None,
                       help="Only posts on/after DATE (YYYY-MM-DD); recent only, ignored with --top")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.mode == "user":
        posts = scrape_user_posts(
            args.username, limit=args.limit, since=args.since, session_path=args.session
        )
        label = f"@{args.username.lstrip('@')}"
    else:
        posts = scrape_hashtag(
            args.tag, limit=args.limit, top=args.top, since=args.since, session_path=args.session
        )
        label = f"#{args.tag.lstrip('#')}" + (" (top)" if args.top else "")

    payload = json.dumps(posts, indent=2, ensure_ascii=False)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload)
        print(f"Wrote {len(posts)} posts for {label} to {out_path}")
    else:
        print(payload)
        print(f"\n# {len(posts)} posts for {label}", file=sys.stderr)


if __name__ == "__main__":
    main()
