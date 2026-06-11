# Maintenance — Instagram scraper

Discovery is **browser-driven** (Playwright over a logged-in Chrome); instagrapi
is used only for optional `--hydrate`. So breakage falls into two buckets:
browser/DOM issues (discovery) and instagrapi issues (hydration).

## When discovery breaks (empty results / errors)

1. **`BrowserNotLoggedIn`** → the profile isn't signed in (cookies cleared or
   expired, or someone tapped "Log out"). Re-run `scrape.py login` and sign in.
2. **Empty list but you're logged in** → Instagram changed the page markup or
   route. Run with `-v (and drop --headless to watch)` and watch: does the grid render? Are the
   post links still `/p/<code>/` / `/reel/<code>/`? If the selectors in
   `browser.py` (`_harvest`, `_POST_HREF`) no longer match, update them.
3. **Hashtag page looks different** → Instagram redirects `/explore/tags/<tag>/`
   to `/explore/search/keyword/?q=#<tag>`; if that route changes again, update
   `discover_hashtag` in `browser.py`.
4. **`playwright` not installed / no browser** → `pip install playwright` then
   `playwright install chromium`.
5. **Too few posts** → raise `--limit`; discovery scrolls until it has `limit`
   posts or the feed stops producing new ones (3 quiet scrolls).

## When hydration breaks (`--hydrate`)

1. **`pip install -U instagrapi` first.** Instagram rotates its internals and
   instagrapi ships fixes on its own cadence.
2. **`login_required` on hydration** → the `ig_session.json` expired; re-run
   `scrape.py login` (it rewrites both session files). Note hydration is
   best-effort — a post that won't hydrate is returned in its sparse form.

## When extras break (`--comments` / `--author-info`)

These are **browser-driven** (instagrapi's `media_comments`/`user_info` are
blocked from a flagged IP). They read off the rendered page, so they break the
same way discovery does:

1. **Empty comments/author** → DOM changed. The comment extractor walks each
   `<time>` up to the row containing "N likes"/"Reply" (`_COMMENTS_JS` in
   `browser.py`); the author stats parse the profile `meta[name=description]` +
   header `[title]` (`_AUTHOR_JS`). Run with `-v (and drop --headless to watch)` and update those
   if Instagram reshaped the markup.
2. **Only ~12-16 comments returned** → long threads need more scrolls or a
   "load more" click; `harvest_comments` scrolls the tallest container best-effort.
3. **`BrowserNotLoggedIn`** → re-run `scrape.py login`.

## Why discovery is NOT instagrapi (investigation, 2026-06-11)

The original build listed posts via instagrapi's private API and it failed with
`login_required` (hashtag) and `429` (user lookup) even with a valid session. We
proved with one known-good session that it's the **endpoint category + IP
reputation**, not the session or code:

| Operation | Kind | Result |
|---|---|---|
| Reel download by URL | targeted single-object fetch | ✅ works |
| `hashtag_medias_recent` | discovery / listing (private API) | ❌ `login_required` |
| `web_profile_info` (user) | discovery / listing (public API) | ❌ `429` |

A real logged-in browser navigated to the same hashtag and rendered 24 posts
fine. Bulk discovery/listing is what Instagram polices hardest; a trusted browser
is served what an impersonating client is refused. Hence: **discovery → browser,
hydration → instagrapi (targeted, still works).** Full writeup in the README
("Investigation").

> Do **not** "fix" discovery by retrying instagrapi hashtag/user in a loop — each
> attempt deepens the IP flag. Drive the browser instead.

## Auth notes

- **One login powers both.** `scrape.py login` opens Chrome, you sign in once, and
  `auth.py` writes BOTH `~/.instagram-scraper/state.json` (Playwright cookies, for
  discovery/comments/author) and `~/.instagram-scraper/ig_session.json` (instagrapi,
  for `--hydrate`) from the same `sessionid`. No second login, no password in code.
- When either side throws `login_required` / `BrowserNotLoggedIn`, the session
  expired — re-run `scrape.py login` to refresh both.

## Capability limits (by design, not bugs)

- **No free-text post search** — `hashtag` is the only keyword-ish surface.
- **No server-side date filter** — `--since` is a client-side filter and needs
  `--hydrate` (the grid has no timestamps); there is no `--until`.
