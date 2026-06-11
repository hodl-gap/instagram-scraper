"""Fetch + normalize Instagram posts via instagrapi into flat dicts.

Output shape deliberately mirrors twitter_scraper's RawTweet so both scrapers
feel identical downstream: id / url / handle / text / likes / comments /
timestamp / media_urls / media_types / video_urls.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, TypedDict

from instagram_scraper.client import get_client

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


def _u(value) -> str:
    """instagrapi URL fields are pydantic HttpUrl; coerce to plain str."""
    return str(value) if value else ""


def _normalize(media) -> Post:
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

    taken = getattr(media, "taken_at", None)
    return Post(
        id=str(media.pk),
        url=f"https://www.instagram.com/p/{code}/" if code else "",
        handle=f"@{username}" if username else "",
        text=media.caption_text or "",
        likes=media.like_count or 0,
        comments=media.comment_count or 0,
        timestamp=taken.isoformat() if taken else "",
        media_urls=media_urls,
        media_types=media_types,
        video_urls=video_urls,
    )


def _paginate_user_until(cl, user_id: str, limit: int, since: datetime) -> list:
    """Page a user's media newest->oldest, stopping once we cross `since` or hit `limit`."""
    out: list = []
    cursor = ""
    while len(out) < limit:
        page, cursor = cl.user_medias_paginated(user_id, 0, cursor)  # 0 = one page (~12)
        if not page:
            break
        out.extend(page)
        oldest = getattr(page[-1], "taken_at", None)
        if oldest and oldest < since:  # crossed the cutoff — stop fetching
            break
        if not cursor:  # no more pages
            break
    return out[:limit]


def scrape_user_posts(
    username: str,
    limit: int = 20,
    since: Optional[datetime] = None,
    session_path: Optional[str] = None,
) -> list[Post]:
    """Scrape a user's recent posts. With `since`, walk newest->oldest and early-stop."""
    cl = get_client(session_path)
    user_id = cl.user_id_from_username(username.lstrip("@"))
    if since is None:
        medias = cl.user_medias(user_id, amount=limit)
    else:
        medias = _paginate_user_until(cl, user_id, limit, since)
        medias = [m for m in medias if getattr(m, "taken_at", None) and m.taken_at >= since]
    return [_normalize(m) for m in medias]


def scrape_hashtag(
    tag: str,
    limit: int = 20,
    top: bool = False,
    since: Optional[datetime] = None,
    session_path: Optional[str] = None,
) -> list[Post]:
    """Scrape posts for a hashtag. `top` = ranked; otherwise most-recent.

    `since` applies to recent (chronological) results only; it is ignored for
    `top` because ranked results are not time-ordered.
    """
    cl = get_client(session_path)
    tag = tag.lstrip("#")
    if top:
        if since is not None:
            logger.warning("--since ignored for --top (ranked results are not chronological)")
        medias = cl.hashtag_medias_top(tag, amount=limit)
    else:
        medias = cl.hashtag_medias_recent(tag, amount=limit)
        if since is not None:
            medias = [m for m in medias if getattr(m, "taken_at", None) and m.taken_at >= since]
    return [_normalize(m) for m in medias]
