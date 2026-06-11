"""Browser-driven discovery: drive a real, logged-in Chrome to list posts.

Why this exists
---------------
Instagram's *discovery / listing* endpoints (hashtag feeds, profile-feed
enumeration, username lookup) reject instagrapi from a flagged IP — `login_required`
on the private API, `429` on the public web API — even with a perfectly valid
session. See README -> "Investigation: why discovery moved to the browser".

A real logged-in browser is a *trusted* client, so navigating to the same
hashtag/profile page renders the posts fine. This module drives that browser
(via Playwright) and harvests post shortcodes from the rendered DOM — the most
durable signal, since Instagram's GraphQL `doc_id`s rotate constantly but
`/p/<code>/` links do not.

Auth model
----------
No username/password, no sessionid file. You log in **once** in a persistent
Chrome profile (`scrape.py login`), and the cookies live in that profile dir for
reuse. Alternatively, attach to an already-running Chrome over CDP
(`--cdp-endpoint http://localhost:9222`) to reuse a browser you're already
logged into.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, TypedDict

from instagram_scraper import auth

logger = logging.getLogger("instagram_scraper")

# /p/<code>/ and /reel/<code>/ are the stable post-link shapes in the grid DOM.
_POST_HREF = re.compile(r"/(p|reel|tv)/([^/?#]+)")


class DiscoveredPost(TypedDict):
    shortcode: str
    url: str
    media_type: str        # "video" for /reel/, else "unknown" (grid doesn't always say)
    thumbnail_url: str      # poster image from the grid tile (best-effort)
    alt: str                # img alt text (best-effort; sometimes holds author/caption hints)


class BrowserNotLoggedIn(RuntimeError):
    """Raised when the driven browser isn't authenticated to Instagram."""


class MissingPlaywright(RuntimeError):
    """Raised when Playwright isn't installed."""


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as e:
        raise MissingPlaywright(
            "Browser discovery needs Playwright:\n"
            "    pip install playwright\n"
            "    playwright install chromium"
        ) from e
    return sync_playwright


@contextmanager
def browser_page(
    cdp_endpoint: Optional[str] = None,
    state_file: Optional[Path] = None,
    headless: bool = False,
) -> Iterator["object"]:
    """Yield a Playwright Page backed by the logged-in Instagram session.

    Two modes:
      * cdp_endpoint set  -> attach to an already-running Chrome (reuse its session).
      * otherwise         -> launch Chromium and load cookies from the shared
                             storage_state (`state.json`), written once by
                             `scrape.py login`. No separate browser login.
    """
    sync_playwright = _import_playwright()
    cdp_endpoint = cdp_endpoint or os.environ.get("IG_CDP_ENDPOINT")
    state = Path(state_file or auth.STATE_FILE)

    with sync_playwright() as pw:
        if cdp_endpoint:
            logger.info("Attaching to Chrome over CDP at %s", cdp_endpoint)
            browser = pw.chromium.connect_over_cdp(cdp_endpoint)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            try:
                yield page
            finally:
                browser.close()
        else:
            if not state.exists():
                raise BrowserNotLoggedIn(
                    f"No saved session at {state}. Run `scrape.py login` once and sign in."
                )
            logger.info("Launching Chromium with session %s (headless=%s)", state, headless)
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(
                storage_state=str(state), viewport={"width": 1280, "height": 900}
            )
            page = context.new_page()
            try:
                yield page
            finally:
                browser.close()


def _assert_logged_in(page) -> None:
    url = page.url or ""
    if "/accounts/login" in url or "/accounts/onetap" in url:
        raise BrowserNotLoggedIn(
            "Browser is not logged in to Instagram. Run `scrape.py login` once "
            "(opens a headed Chrome) and sign in, then retry."
        )
    # The logged-out home is the account picker / login wall.
    body = page.inner_text("body")[:400] if page.query_selector("body") else ""
    if "Create new account" in body and ("Log in" in body or "Continue" in body):
        raise BrowserNotLoggedIn(
            "Browser is not logged in to Instagram. Run `scrape.py login` once "
            "(opens a headed Chrome) and sign in, then retry."
        )


def _harvest(page) -> list[dict]:
    """Pull every post anchor currently in the DOM (shortcode + best-effort thumb)."""
    return page.evaluate(
        """() => Array.from(
              document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]')
           ).map(a => {
              const img = a.querySelector('img');
              return {
                href: a.getAttribute('href') || '',
                thumb: img ? (img.getAttribute('src') || '') : '',
                alt: img ? (img.getAttribute('alt') || '') : '',
              };
           })"""
    )


def _to_post(raw: dict) -> Optional[DiscoveredPost]:
    m = _POST_HREF.search(raw.get("href", ""))
    if not m:
        return None
    kind, code = m.group(1), m.group(2)
    return DiscoveredPost(
        shortcode=code,
        url=f"https://www.instagram.com/p/{code}/",
        media_type="video" if kind in ("reel", "tv") else "unknown",
        thumbnail_url=raw.get("thumb", "") or "",
        alt=raw.get("alt", "") or "",
    )


def discover_post_urls(
    page,
    target_url: str,
    limit: int,
    max_scrolls: int = 40,
    settle_ms: int = 1200,
) -> list[DiscoveredPost]:
    """Navigate to `target_url` and scroll, collecting unique post links up to `limit`."""
    page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(settle_ms)
    _assert_logged_in(page)

    found: "dict[str, DiscoveredPost]" = {}
    stale_rounds = 0
    for _ in range(max_scrolls):
        for raw in _harvest(page):
            post = _to_post(raw)
            if post and post["shortcode"] not in found:
                found[post["shortcode"]] = post
        if len(found) >= limit:
            break
        before = len(found)
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(settle_ms)
        # Re-harvest after the scroll settles so the stale check sees new tiles.
        for raw in _harvest(page):
            post = _to_post(raw)
            if post and post["shortcode"] not in found:
                found[post["shortcode"]] = post
        stale_rounds = stale_rounds + 1 if len(found) == before else 0
        if stale_rounds >= 3:  # three quiet scrolls in a row -> end of feed
            logger.info("No new posts after %d scrolls; stopping (%d found)", stale_rounds, len(found))
            break

    return list(found.values())[:limit]


def discover_hashtag(tag: str, limit: int = 20, **browser_opts) -> list[DiscoveredPost]:
    """Discover recent posts for a hashtag by driving the browser to its tag page."""
    tag = tag.lstrip("#")
    url = f"https://www.instagram.com/explore/tags/{tag}/"
    with browser_page(**browser_opts) as page:
        return discover_post_urls(page, url, limit)


def discover_user(username: str, limit: int = 20, **browser_opts) -> list[DiscoveredPost]:
    """Discover a user's recent posts by driving the browser to their profile."""
    username = username.lstrip("@")
    url = f"https://www.instagram.com/{username}/"
    with browser_page(**browser_opts) as page:
        return discover_post_urls(page, url, limit)


# ---------------------------------------------------------------------------
# Browser-driven Bucket 2: comments + author info (instagrapi's versions of these
# hit listing/profile endpoints that Instagram blocks from a flagged IP, so we
# read them off the rendered page instead, same as discovery).
# ---------------------------------------------------------------------------

# Walk each comment's <time> up to the row that also holds "N likes"/"Reply",
# then pull username + datetime + like count + text. Validated against the live
# post DOM (Korean comments, like counts, timestamps all extracted correctly).
_COMMENTS_JS = r"""() => {
  const isProfile = h => /^\/[A-Za-z0-9._]+\/$/.test(h || '');
  const rows = [];
  for (const t of document.querySelectorAll('time')) {
    let n = t, row = null;
    for (let i = 0; i < 7 && n.parentElement; i++) {
      n = n.parentElement;
      const txt = n.innerText || '';
      if (/\blikes?\b/.test(txt) || /\bReply\b/.test(txt)) { row = n; break; }
    }
    if (!row) continue;
    const a = [...row.querySelectorAll('a[href^="/"]')].find(x => isProfile(x.getAttribute('href')));
    if (!a) continue;
    const user = a.getAttribute('href').replace(/\//g, '');
    const rel = (t.innerText || '').trim();
    const lines = (row.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const likesLine = lines.find(l => /^[\d,.]+\s+likes?$/i.test(l));
    const likes = likesLine ? parseInt(likesLine.replace(/[^\d]/g, ''), 10) : 0;
    const text = lines.filter(l =>
      l !== user && l !== rel && l !== likesLine &&
      !/^reply$/i.test(l) && !/^view\b/i.test(l) && !/^\d+\s*repl/i.test(l)
    ).join(' ');
    rows.push({ user, created_at: t.getAttribute('datetime') || '', likes, text });
  }
  const seen = new Set(), uniq = [];
  for (const r of rows) { const k = r.user + '|' + r.created_at; if (!seen.has(k)) { seen.add(k); uniq.push(r); } }
  return uniq;
}"""

# Profile follower/following/post counts come from the meta description (stable);
# exact follower count from the header [title] attribute when present.
_AUTHOR_JS = r"""() => {
  const meta = (document.querySelector('meta[property="og:description"]')?.content)
            || (document.querySelector('meta[name="description"]')?.content) || '';
  const m = meta.match(/([\d.,KMB]+)\s+Followers,\s+([\d.,KMB]+)\s+Following,\s+([\d.,KMB]+)\s+Posts/i);
  let exact = null;
  const titled = [...document.querySelectorAll('header [title]')]
      .map(e => e.getAttribute('title')).find(t => /^[\d,]+$/.test(t || ''));
  if (titled) exact = parseInt(titled.replace(/,/g, ''), 10);
  let bio = '';
  const header = document.querySelector('header');
  if (header) {
    const lines = (header.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const idx = lines.findIndex(l => /following$/i.test(l));
    if (idx >= 0) bio = lines.slice(idx + 1)
        .filter(l => !/^(Follow|Message|Following)$/i.test(l)).slice(0, 4).join(' ');
  }
  return { followers: m ? m[1] : '', following: m ? m[2] : '', posts: m ? m[3] : '', exact, bio };
}"""


def _num(s: str) -> int:
    """Parse Instagram's abbreviated counts ('839K', '1.2M', '1,234') to int."""
    s = (s or "").replace(",", "").strip().upper()
    mult = 1
    if s.endswith("K"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("B"):
        mult, s = 1_000_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _scroll_comments(page) -> None:
    """Scroll the tallest scrollable container (the comment list) toward its end."""
    page.evaluate(
        """() => {
            const els = [...document.querySelectorAll('*')]
                .filter(e => e.scrollHeight > e.clientHeight + 200);
            const c = els.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
            if (c) c.scrollTop = c.scrollHeight;
        }"""
    )


def harvest_comments(page, post_url: str, amount: int, max_scrolls: int = 12,
                     settle_ms: int = 1000) -> list[dict]:
    """Navigate to a post and read its comments from the DOM (up to `amount`)."""
    page.goto(post_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(settle_ms)
    _assert_logged_in(page)
    prev = -1
    rows: list[dict] = []
    for _ in range(max_scrolls):
        rows = page.evaluate(_COMMENTS_JS)
        if len(rows) >= amount or len(rows) == prev:
            break
        prev = len(rows)
        _scroll_comments(page)
        page.wait_for_timeout(settle_ms)
    return rows[:amount]


def harvest_author(page, username: str, settle_ms: int = 800) -> dict:
    """Navigate to a profile and read follower/following/post counts + bio."""
    username = username.lstrip("@")
    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(settle_ms)
    _assert_logged_in(page)
    raw = page.evaluate(_AUTHOR_JS)
    return {
        "author_followers": raw.get("exact") or _num(raw.get("followers", "")),
        "author_following": _num(raw.get("following", "")),
        "author_post_count": _num(raw.get("posts", "")),
        "author_bio": raw.get("bio", "") or "",
    }


def open_for_login(state_file: Optional[Path] = None, poll_timeout_sec: int = 300) -> None:
    """Open a headed Chromium on instagram.com so the user logs in once.

    Polls until the `sessionid` cookie appears (i.e. login succeeded), then writes
    BOTH session artifacts from that one login: the Playwright storage_state
    (`state.json`) and the instagrapi session (`ig_session.json`). No second login.
    """
    sync_playwright = _import_playwright()
    state = Path(state_file or auth.STATE_FILE)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        print("\nLog in to Instagram in the opened window. Waiting for sign-in...\n")

        sessionid = None
        waited = 0
        while waited < poll_timeout_sec:
            for c in context.cookies():
                if c.get("name") == "sessionid" and c.get("value"):
                    sessionid = c["value"]
                    break
            if sessionid:
                break
            page.wait_for_timeout(2000)
            waited += 2

        if not sessionid:
            print("Timed out waiting for login. Nothing saved.")
            browser.close()
            return

        page.wait_for_timeout(1500)  # let any post-login cookies settle
        auth.save_state_from_context(context, state)
        print(f"Saved browser session -> {state}")
        try:
            auth.derive_instagrapi_session(sessionid)
            print(f"Saved instagrapi session -> {auth.SESSION_FILE}")
        except Exception as e:  # noqa: BLE001 - browser session still works without it
            logger.warning("Could not derive instagrapi session (%s); --hydrate may not work", type(e).__name__)
        browser.close()
        print("Login complete. You can now run `scrape.py user`/`hashtag`.")
