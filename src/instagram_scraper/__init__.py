from instagram_scraper.client import get_client, session_path
from instagram_scraper.fetch import scrape_user_posts, scrape_hashtag, Post

__all__ = [
    "get_client",
    "session_path",
    "scrape_user_posts",
    "scrape_hashtag",
    "Post",
]
