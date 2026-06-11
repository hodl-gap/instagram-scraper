#!/usr/bin/env python3
"""Bootstrap an Instagram session file from a browser `sessionid` cookie.

One-off. Log into instagram.com in a real browser, copy the `sessionid` cookie
(DevTools -> Application -> Cookies -> https://www.instagram.com), then:

    python scripts/ig_session_bootstrap.py --sessionid 'PASTE_HERE' --test

This writes ig_session.json (gitignored): the cookie plus a persisted device
identity. NEVER tap "Log out" in that browser afterwards — it invalidates the
sessionid server-side and kills the session file (you'd have to re-bootstrap).
"""

import argparse
import sys
from pathlib import Path

from instagrapi import Client

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "ig_session.json"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sessionid", help="sessionid cookie value (or pipe it via stdin)")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"Session file to write (default: {DEFAULT_OUT})")
    ap.add_argument("--test", action="store_true", help="Verify by fetching account info")
    args = ap.parse_args()

    sessionid = (args.sessionid or sys.stdin.readline()).strip()
    if not sessionid:
        ap.error("no sessionid provided (pass --sessionid or pipe via stdin)")

    cl = Client()
    cl.login_by_sessionid(sessionid)
    cl.dump_settings(args.out)
    print(f"Wrote session to {args.out}")

    if args.test:
        info = cl.account_info()
        print(f"Authenticated as @{info.username} ({info.full_name})")


if __name__ == "__main__":
    main()
