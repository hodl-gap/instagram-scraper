from instagram_scraper.client import get_client, session_path
from instagram_scraper.fetch import scrape_user_posts, scrape_hashtag, Post
from instagram_scraper.browser import discover_hashtag, discover_user, open_for_login

__all__ = [
    "get_client",
    "session_path",
    "scrape_user_posts",
    "scrape_hashtag",
    "Post",
    "discover_hashtag",
    "discover_user",
    "open_for_login",
]
