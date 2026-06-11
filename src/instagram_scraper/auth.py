"""Single source of truth for the scraper's Instagram session.

ONE human login produces a `sessionid` cookie. From that one session we derive
BOTH artifacts the scraper needs — there is no second login:

  * state.json      -> Playwright storage_state  (discovery / comments / author)
  * ig_session.json -> instagrapi session        (hydrate: caption/likes/views/music/video)

`scrape.py login` opens a browser, you sign in once, and both files are written
from the resulting cookies. When Instagram expires the session, re-run `login`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_HOME = Path(os.environ.get("IG_SCRAPER_HOME", str(Path.home() / ".instagram-scraper")))
STATE_FILE = _HOME / "state.json"          # Playwright storage_state
SESSION_FILE = _HOME / "ig_session.json"   # instagrapi settings

# Cookies worth persisting (sessionid is the one that authenticates).
ESSENTIAL_COOKIES = ("sessionid", "ds_user_id", "csrftoken", "datr", "ig_did", "mid", "rur")


def _cookie_entry(name: str, value: str) -> dict:
    return {
        "name": name, "value": value, "domain": ".instagram.com", "path": "/",
        "expires": 1_900_000_000, "httpOnly": name == "sessionid",
        "secure": True, "sameSite": "Lax",
    }


def write_state_from_cookies(cookies: dict, path: Path = STATE_FILE) -> Path:
    """Write a Playwright storage_state from a {name: value} cookie dict."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [_cookie_entry(n, v) for n, v in cookies.items() if v]
    path.write_text(json.dumps({"cookies": entries, "origins": []}))
    return path


def save_state_from_context(context, path: Path = STATE_FILE) -> Path:
    """Persist a live Playwright context's cookies to the state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    return path


def sessionid_from_state(path: Path = STATE_FILE) -> Optional[str]:
    if not path.exists():
        return None
    for c in json.loads(path.read_text()).get("cookies", []):
        if c.get("name") == "sessionid":
            return c.get("value")
    return None


def derive_instagrapi_session(sessionid: str, path: Path = SESSION_FILE):
    """Build an instagrapi session file from the same sessionid (no separate login)."""
    from instagrapi import Client

    cl = Client()
    cl.login_by_sessionid(sessionid)
    path.parent.mkdir(parents=True, exist_ok=True)
    cl.dump_settings(str(path))
    return cl
