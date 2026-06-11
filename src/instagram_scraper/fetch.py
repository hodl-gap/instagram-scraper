"""Fetch + normalize Instagram posts into flat dicts.

Discovery (listing posts for a hashtag or user) runs through a **real logged-in
browser** (`browser.py`) because Instagram blocks instagrapi on those endpoints
from a flagged IP — see README -> "Investigation". instagrapi is kept ONLY for
optional per-post *hydration* (`media_info` by shortcode), a targeted single-object
fetch that Instagram still serves.

Field tiers:
  * discovery (free)  : id / url / handle / media_types / media_urls(thumb)
  * hydrate (instagrapi media_info, 1 call/post): caption / likes / comments-count /
                           timestamp / video mp4 / music / play+view counts /
                           location / tags / author flags
  * --comments / --author-info (browser): comment list + author follower/post counts.
                           These use the BROWSER (not instagrapi) because the
                           comment/profile endpoints are blocked from a flagged IP.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional, TypedDict

from instagram_scraper import browser

logger = logging.getLogger("instagram_scraper")

# instagrapi media_type codes
_TYPE_NAME = {1: "photo", 2: "video", 8: "album"}


class Post(TypedDict):
    id: str
    url: str
    handle: str
    text: str
    likes: int
    comments: int
    timestamp: str
    media_urls: list[str]   # index-aligned with media_types; poster thumbnail for video
    media_types: list[str]  # "photo" | "video"
    video_urls: list[str]   # playable mp4 for each video item (empty for photo-only)
    is_original_audio: bool  # creator's own audio vs a licensed library track
    track_name: str          # song/audio title (empty if none/unknown)
    artist_name: str         # song artist / original-audio creator (empty if none)
    # --- Bucket 1: free from media_info (filled on --hydrate) ---
    play_count: int
    view_count: int
    location: str            # tagged location name ("" if none)
    tagged_users: list[str]  # usernames tagged in the media
    sponsor_tags: list[str]  # paid-partnership usernames
    hashtags: list[str]      # parsed from caption
    mentions: list[str]      # parsed from caption
    author_full_name: str
    author_is_verified: bool
    author_is_private: bool
    # --- Bucket 2: extra calls, only with --comments / --author-info ---
    comments_list: list[dict]  # [{user, text, likes, created_at}], only with --comments
    author_followers: int      # only with --author-info
    author_following: int
    author_post_count: int
    author_bio: str
    # --- non-VLM video analysis, only with --analyze (downloads + processes mp4) ---
    analysis: dict             # {shots, audio(transcript/language), people, on_screen_text}


def _u(value) -> str:
    """instagrapi URL fields are pydantic HttpUrl; coerce to plain str."""
    return str(value) if value else ""


def _attr(obj, key):
    """Read `key` from a pydantic model OR a dict (instagrapi mixes both)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _iso(dt) -> str:
    return dt.isoformat() if dt else ""


def _usernames(tags) -> list[str]:
    """Collect usernames from a list of usertag/sponsor entries (defensive on shape)."""
    out: list[str] = []
    for t in tags or []:
        user = _attr(t, "user") or t  # usertag wraps .user; sponsor may BE the user
        name = _attr(user, "username")
        if name:
            out.append(name)
    return out


def _extract_music(media) -> dict:
    """Pull music/audio fields from an instagrapi Media's clips_metadata.

    Mirrors reel-decomposer's L1 `_extract_audio_meta`: prefer a licensed library
    track (`music_asset_info`); otherwise fall back to creator/original audio.
    Defensive against clips_metadata being a dict or a pydantic model.
    """
    clips = _attr(media, "clips_metadata")
    asset = _attr(_attr(clips, "music_info"), "music_asset_info")
    if asset:
        return {
            "is_original_audio": False,
            "track_name": _attr(asset, "title") or "",
            "artist_name": _attr(asset, "display_artist") or "",
        }
    original = _attr(clips, "original_sound_info")
    if original:
        artist = _attr(original, "ig_artist")
        return {
            "is_original_audio": True,
            "track_name": _attr(original, "original_audio_title") or "",
            "artist_name": _attr(artist, "username") or "",
        }
    return {"is_original_audio": False, "track_name": "", "artist_name": ""}


# Default values for fields not available at the discovery (pre-hydrate) tier.
_HYDRATE_DEFAULTS = dict(
    play_count=0, view_count=0, location="", tagged_users=[], sponsor_tags=[],
    hashtags=[], mentions=[], author_full_name="", author_is_verified=False,
    author_is_private=False, comments_list=[], author_followers=0,
    author_following=0, author_post_count=0, author_bio="", analysis={},
)


def _post_from_discovery(d: browser.DiscoveredPost, handle: str = "") -> Post:
    """Build a Post from a browser-discovered tile (no API call; sparse metadata).

    The grid gives us a stable shortcode/URL + a poster thumbnail, but not
    caption/likes/comments/timestamp — those require `--hydrate`.
    """
    mt = d["media_type"] if d["media_type"] != "unknown" else "photo"
    return Post(
        id=d["shortcode"],
        url=d["url"],
        handle=handle,
        text="",
        likes=0,
        comments=0,
        timestamp="",
        media_urls=[d["thumbnail_url"]] if d["thumbnail_url"] else [],
        media_types=[mt],
        video_urls=[],
        is_original_audio=False,
        track_name="",
        artist_name="",
        **{k: (list(v) if isinstance(v, list) else v) for k, v in _HYDRATE_DEFAULTS.items()},
    )


def _normalize(media) -> Post:
    """Map an instagrapi Media (from hydration) to a fully-populated Post (Bucket 1)."""
    code = media.code or ""
    user = getattr(media, "user", None)
    username = getattr(user, "username", "") if user else ""

    media_urls: list[str] = []
    media_types: list[str] = []
    video_urls: list[str] = []

    resources = getattr(media, "resources", None) or []
    if resources:  # album (media_type 8) — walk the carousel items
        for r in resources:
            media_urls.append(_u(r.thumbnail_url))
            media_types.append(_TYPE_NAME.get(r.media_type, "photo"))
            if r.media_type == 2 and r.video_url:
                video_urls.append(_u(r.video_url))
    else:  # single photo (1) or single video/reel (2)
        media_urls.append(_u(media.thumbnail_url))
        media_types.append(_TYPE_NAME.get(media.media_type, "photo"))
        if media.media_type == 2 and media.video_url:
            video_urls.append(_u(media.video_url))

    caption = media.caption_text or ""
    music = _extract_music(media)
    return Post(
        id=str(media.pk),
        url=f"https://www.instagram.com/p/{code}/" if code else "",
        handle=f"@{username}" if username else "",
        text=caption,
        likes=media.like_count or 0,
        comments=media.comment_count or 0,
        timestamp=_iso(getattr(media, "taken_at", None)),
        media_urls=media_urls,
        media_types=media_types,
        video_urls=video_urls,
        is_original_audio=music["is_original_audio"],
        track_name=music["track_name"],
        artist_name=music["artist_name"],
        # Bucket 1 — all already present in the media_info object, no extra calls.
        play_count=getattr(media, "play_count", None) or 0,
        view_count=getattr(media, "view_count", None) or 0,
        location=_attr(getattr(media, "location", None), "name") or "",
        tagged_users=_usernames(getattr(media, "usertags", None)),
        sponsor_tags=_usernames(getattr(media, "sponsor_tags", None)),
        hashtags=re.findall(r"#(\w+)", caption, re.UNICODE),
        mentions=re.findall(r"@([\w.]+)", caption),
        author_full_name=_attr(user, "full_name") or "",
        author_is_verified=bool(_attr(user, "is_verified")),
        author_is_private=bool(_attr(user, "is_private")),
        # Bucket 2 defaults — overwritten only if the flags are set.
        comments_list=[],
        author_followers=0,
        author_following=0,
        author_post_count=0,
        author_bio="",
        analysis={},
    )


def _hydrate(posts: list[Post], session_path: Optional[str]) -> list[Post]:
    """Fill Bucket-1 metadata via instagrapi per-post `media_info` (targeted).

    `media_info` is the one instagrapi call that still works from a flagged IP
    because it fetches a single known object rather than enumerating a feed.
    Best-effort: a post that fails to hydrate is returned in its sparse form.
    """
    from instagram_scraper.client import get_client

    cl = get_client(session_path)
    out: list[Post] = []
    for p in posts:
        try:
            media = cl.media_info(cl.media_pk_from_code(p["id"]))
            out.append(_normalize(media))
        except Exception as e:  # noqa: BLE001 - keep going, keep the sparse row
            logger.warning("Hydration failed for %s (%s); keeping sparse row", p["url"], type(e).__name__)
            out.append(p)
    return out


def _enrich_via_browser(posts, comments_amount, author_info, browser_opts) -> list[Post]:
    """Bucket 2 over the browser: comments per post, author stats per unique author.

    instagrapi's `media_comments`/`user_info` are listing/profile endpoints that
    Instagram blocks from a flagged IP, so we read them off the rendered page —
    the same trusted-browser approach as discovery. One browser session is reused
    across every post and author. Best-effort: a failure leaves that field empty.
    """
    from instagram_scraper import browser as b

    author_cache: dict = {}
    with b.browser_page(**browser_opts) as page:
        for p in posts:
            if comments_amount:
                try:
                    p["comments_list"] = b.harvest_comments(page, p["url"], comments_amount)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Browser comments failed for %s (%s)", p["url"], type(e).__name__)
            if author_info:
                handle = (p["handle"] or "").lstrip("@")
                if not handle:
                    continue
                if handle not in author_cache:
                    try:
                        author_cache[handle] = b.harvest_author(page, handle)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Browser author info failed for @%s (%s)", handle, type(e).__name__)
                        author_cache[handle] = {}
                p.update(author_cache[handle])
    return posts


def _run_analysis(posts, work_dir, analysis_opts):
    """Non-VLM video analysis per post (downloads mp4 + runs shots/audio/people/OCR)."""
    from instagram_scraper import analysis as az
    from pathlib import Path

    wd = Path(work_dir)
    for p in posts:
        try:
            p["analysis"] = az.analyze_post(p, wd, **(analysis_opts or {}))
        except Exception as e:  # noqa: BLE001
            logger.warning("Analysis failed for %s (%s)", p["url"], type(e).__name__)
            p["analysis"] = {"error": f"{type(e).__name__}: {e}"}
    return posts


def _maybe_hydrate(posts, hydrate, comments, author_info, analyze, session_path,
                   since, browser_opts, analysis_opts=None, work_dir=".reel_work"):
    """Shared tail: hydrate (Bucket 1) + browser extras (Bucket 2) + non-VLM analysis + `since`."""
    need = hydrate or comments or author_info or analyze
    if need:
        posts = _hydrate(posts, session_path)
        if comments or author_info:
            posts = _enrich_via_browser(posts, comments, author_info, browser_opts)
        if since is not None:
            posts = [p for p in posts if p["timestamp"] and datetime.fromisoformat(p["timestamp"]) >= since]
        if analyze:
            posts = _run_analysis(posts, work_dir, analysis_opts)
    elif since is not None:
        logger.warning("--since ignored without --hydrate (the grid carries no timestamps)")
    return posts


def scrape_user_posts(
    username: str,
    limit: int = 20,
    since: Optional[datetime] = None,
    hydrate: bool = False,
    comments: int = 0,
    author_info: bool = False,
    analyze: bool = False,
    analysis_opts: Optional[dict] = None,
    work_dir: str = ".reel_work",
    session_path: Optional[str] = None,
    **browser_opts,
) -> list[Post]:
    """Scrape a user's recent posts via the browser.

    `comments`/`author_info`/`analyze` imply hydration (they need the video URL /
    `media_info`). `since` filters by post date and requires hydration.
    """
    handle = f"@{username.lstrip('@')}"
    discovered = browser.discover_user(username, limit=limit, **browser_opts)
    posts = [_post_from_discovery(d, handle=handle) for d in discovered]
    return _maybe_hydrate(posts, hydrate, comments, author_info, analyze, session_path,
                          since, browser_opts, analysis_opts, work_dir)


def scrape_hashtag(
    tag: str,
    limit: int = 20,
    top: bool = False,
    since: Optional[datetime] = None,
    hydrate: bool = False,
    comments: int = 0,
    author_info: bool = False,
    analyze: bool = False,
    analysis_opts: Optional[dict] = None,
    work_dir: str = ".reel_work",
    session_path: Optional[str] = None,
    **browser_opts,
) -> list[Post]:
    """Scrape posts for a hashtag via the browser.

    `top` is accepted for CLI compatibility but the browser tag page already
    serves Instagram's ranked-ish grid; there is no separate recent/top toggle.
    `comments`/`author_info`/`analyze` imply hydration; `since` requires it.
    """
    if top:
        logger.warning("--top has no effect in browser discovery (tag page serves one grid)")
    discovered = browser.discover_hashtag(tag, limit=limit, **browser_opts)
    posts = [_post_from_discovery(d) for d in discovered]
    return _maybe_hydrate(posts, hydrate, comments, author_info, analyze, session_path,
                          since, browser_opts, analysis_opts, work_dir)
