"""Thin instagrapi wrapper: build an authenticated Client from the saved session.

instagrapi is NOT used for discovery/comments/author (those endpoints are blocked
from a flagged IP — see README "Investigation"); it serves only per-post
**hydration** (`media_info` by shortcode), a targeted fetch Instagram still honors.

The session file (`ig_session.json`) is derived from the SAME login as the
browser — `scrape.py login` writes both from one sign-in (see `auth.py`). We never
call `login()` here, so the challenge-wall double-fire cannot happen.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from instagrapi import Client

from instagram_scraper import auth

logger = logging.getLogger("instagram_scraper")


def session_path(explicit: Optional[str] = None) -> Path:
    """Resolve the session file: explicit arg > $IG_SESSION_PATH > shared auth.SESSION_FILE."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("IG_SESSION_PATH")
    return Path(env) if env else auth.SESSION_FILE


def get_client(explicit_path: Optional[str] = None, delay_range=(1, 3)) -> Client:
    """Return an instagrapi Client with the saved session loaded (no login)."""
    path = session_path(explicit_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No Instagram session at {path}. Run `scrape.py login` once (it writes "
            "both the browser and instagrapi sessions from one sign-in)."
        )
    cl = Client()
    cl.delay_range = list(delay_range)
    cl.load_settings(path)
    logger.info("Loaded IG session from %s", path)
    return cl
