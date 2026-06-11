"""Thin instagrapi wrapper: build an authenticated Client from a saved session.

There is **no username/password path here by design**. Auth is a browser-born
session file only (see scripts/ig_session_bootstrap.py), and we never call
`login()` — so the `load_settings()` + `login()` double-fire that re-triggers
Instagram's challenge wall cannot happen.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from instagrapi import Client

logger = logging.getLogger("instagram_scraper")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SESSION = _PROJECT_ROOT / "ig_session.json"


def session_path(explicit: Optional[str] = None) -> Path:
    """Resolve the session file: explicit arg > $IG_SESSION_PATH > ./ig_session.json."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("IG_SESSION_PATH")
    return Path(env) if env else _DEFAULT_SESSION


def get_client(explicit_path: Optional[str] = None, delay_range=(1, 3)) -> Client:
    """Return an instagrapi Client with the saved session loaded (no login)."""
    path = session_path(explicit_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No Instagram session at {path}. Bootstrap one with "
            "scripts/ig_session_bootstrap.py (see README -> Setup)."
        )
    cl = Client()
    cl.delay_range = list(delay_range)
    cl.load_settings(path)
    logger.info("Loaded IG session from %s", path)
    return cl
