#!/usr/bin/env python3
"""
Instagram scraper — central entrypoint (sibling to twitter_scraper/scrape.py).

Discovery runs through a real logged-in Chrome (Playwright), because Instagram
blocks instagrapi on hashtag/user listing endpoints from a flagged IP. Log in
ONCE, then scrape:

    python scrape.py login                       # one-time: sign in to Chrome
    python scrape.py user natgeo --limit 20
    python scrape.py hashtag photography --limit 30
    python scrape.py hashtag photography --hydrate --out output/photography.json

Add --hydrate to fill caption/likes/comments/timestamp per post via instagrapi
(a targeted fetch that still works); without it you get shortcode + URL + thumb.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from instagram_scraper.browser import open_for_login
from instagram_scraper.fetch import scrape_user_posts, scrape_hashtag


HASHTAG_HELP = """\
TAG is a single Instagram hashtag (the # is optional). Discovery is browser-driven:
the tool navigates to the tag page in a logged-in Chrome and harvests the grid.

  (default)            posts from the tag grid (Instagram's ranked-ish order)
  --since YYYY-MM-DD   keep only posts on/after DATE; REQUIRES --hydrate
                       (the grid carries no timestamps on its own)
  --hydrate            fill caption/likes/comments/timestamp via instagrapi

Only ONE hashtag per call — multi-tag / AND / OR is not supported.
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
    common.add_argument("--hydrate", action="store_true",
                        help="Fill caption/likes/views/location/tags/music per post via instagrapi")
    common.add_argument("--comments", type=int, nargs="?", const=20, default=0, metavar="N",
                        help="Also fetch up to N comments per post (default 20 if no number); "
                             "+1 API call per post. Implies --hydrate.")
    common.add_argument("--author-info", dest="author_info", action="store_true",
                        help="Also fetch author follower/following/post counts + bio; "
                             "+1 API call per unique author. Implies --hydrate.")
    common.add_argument("--analyze", action="store_true",
                        help="Non-VLM video analysis per post: downloads the mp4 and runs "
                             "shots+brightness / speech transcript / people count / on-screen "
                             "text (OCR). Needs the 'analysis' extra. Implies --hydrate. SLOW on CPU.")
    common.add_argument("--whisper-model", default="small",
                        help="faster-whisper model size for --analyze audio (default: small)")
    common.add_argument("--yolo-model", default="yolo11n.pt",
                        help="YOLO weights for --analyze people detection (default: yolo11n.pt)")
    common.add_argument("--work-dir", default=".reel_work",
                        help="Where --analyze downloads mp4s (default: .reel_work)")
    common.add_argument("--session", default=None,
                        help="instagrapi session file for --hydrate "
                             "(default: $IG_SESSION_PATH or ~/.instagram-scraper/ig_session.json)")
    common.add_argument("--cdp-endpoint", default=None,
                        help="Attach to a running Chrome over CDP (e.g. http://localhost:9222) "
                             "instead of the saved session")
    common.add_argument("--headless", dest="headless", action="store_true", default=False,
                        help="Run the browser invisibly in the background (default: visible window)")
    common.add_argument("-v", "--verbose", action="store_true")

    parser = argparse.ArgumentParser(
        description="Scrape posts from an Instagram user or a hashtag (browser-driven).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_login = sub.add_parser("login", help="One-time: open Chrome to sign in (writes both sessions)")
    p_login.add_argument("-v", "--verbose", action="store_true")

    p_user = sub.add_parser("user", parents=[common], help="Scrape a user's recent posts")
    p_user.add_argument("username", help="Instagram username, e.g. natgeo or @natgeo")
    p_user.add_argument("--since", type=_parse_since, default=None,
                        help="Only posts on/after DATE (YYYY-MM-DD); requires --hydrate")

    p_tag = sub.add_parser(
        "hashtag",
        parents=[common],
        help="Scrape posts for a hashtag",
        description=HASHTAG_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_tag.add_argument("tag", help="Hashtag (with or without #), e.g. photography")
    p_tag.add_argument("--top", action="store_true", help="(No effect; tag page serves one grid)")
    p_tag.add_argument("--since", type=_parse_since, default=None,
                       help="Only posts on/after DATE (YYYY-MM-DD); requires --hydrate")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.mode == "login":
        open_for_login()
        return

    browser_opts = dict(
        cdp_endpoint=args.cdp_endpoint,
        headless=args.headless,
    )
    analysis_opts = dict(whisper_model=args.whisper_model, yolo_model=args.yolo_model)

    if args.mode == "user":
        posts = scrape_user_posts(
            args.username, limit=args.limit, since=args.since,
            hydrate=args.hydrate, comments=args.comments, author_info=args.author_info,
            analyze=args.analyze, analysis_opts=analysis_opts, work_dir=args.work_dir,
            session_path=args.session, **browser_opts,
        )
        label = f"@{args.username.lstrip('@')}"
    else:
        posts = scrape_hashtag(
            args.tag, limit=args.limit, top=args.top, since=args.since,
            hydrate=args.hydrate, comments=args.comments, author_info=args.author_info,
            analyze=args.analyze, analysis_opts=analysis_opts, work_dir=args.work_dir,
            session_path=args.session, **browser_opts,
        )
        label = f"#{args.tag.lstrip('#')}"

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
