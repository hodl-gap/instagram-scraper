# instagram_scraper

Standalone Instagram scraper — a sibling to `twitter_scraper`, same shape
(`scrape.py` CLI, `src/` layout, JSON output with identical media fields).

**Discovery is browser-driven.** Listing posts for a hashtag or a user is done by
driving a real, logged-in Chrome (via [Playwright](https://playwright.dev/python/))
and harvesting the rendered grid — because Instagram blocks the private-API
approach (instagrapi) on those endpoints from a flagged IP. See
[Investigation](#investigation-why-discovery-is-browser-driven) for the evidence.
instagrapi is kept only for optional per-post *hydration*.

## What it can (and can't) do

Instagram has **no free-text post search** and **no server-side date filter**,
so this is deliberately narrower than the Twitter scraper:

| Mode | Accepts | Returns |
|---|---|---|
| `scrape.py user <username>` | a username | that user's recent posts |
| `scrape.py hashtag <tag>` | one hashtag | posts from that tag's grid |

- **No keyword/boolean search** (`AND`/`OR`), no `from:` / `min_faves:` operators.
- **No real `since:`/`until:`.** `--since` is a client-side filter and needs
  `--hydrate` (the grid has no timestamps on its own); there is no `--until`.
- By default discovery returns **shortcode + URL + thumbnail** per post. Add
  `--hydrate` to fill caption / likes / comments / timestamp.

## Layout

```
instagram_scraper/
├── scrape.py                          # CLI entry point (login / user / hashtag)
├── docs/
│   └── MAINTENANCE.md                 # what to do when scraping breaks
└── src/instagram_scraper/
    ├── auth.py                        # one login -> state.json + ig_session.json
    ├── browser.py                     # Playwright: discovery + comments + author
    ├── fetch.py                       # everything -> flat post dicts
    ├── client.py                      # instagrapi wrapper (hydration only)
    └── analysis.py                    # non-VLM video analysis (--analyze)
```

## Setup

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium      # one-time: fetch the browser binary
```

### 2. Log in (once)

```bash
.venv/bin/python scrape.py login
```

A real Chrome window opens on instagram.com — **sign in by hand, once.** From that
single sign-in the scraper writes **both** session files it needs (see
[Authentication](#authentication-one-login)):

- `~/.instagram-scraper/state.json` — browser cookies (discovery / comments / author)
- `~/.instagram-scraper/ig_session.json` — instagrapi session (hydrate)

There is **no second login** and no password stored anywhere. When Instagram
eventually expires the session, just run `scrape.py login` again.

> **Don't "Log out"** of that account in the meantime — it kills the session.

**Signing in is the one step a human must do** — typing the password can't (and
shouldn't) be automated. The command handles the handoff for you: it opens the
window, **polls until it detects you're signed in, then writes both session files
and exits on its own** (no "press enter", no copying cookies). So an automated
setup — e.g. asking Claude Code to "set this up" — looks like:

1. agent runs the install commands,
2. agent runs `scrape.py login`; a Chrome window opens,
3. **you** sign in by hand (you have up to 5 minutes),
4. the command detects it, saves both sessions, and exits — the agent takes over
   from there and runs `user`/`hashtag` commands with no further logins.

> The login window is a **real visible browser**, so it needs a desktop/display.
> On a plain headless server there's nothing to show it on; on WSL, WSLg provides
> the display. (Only `login` must be visible — actual scraping can run `--headless`.)

**Alternative — attach to a Chrome you're already in.** If you have a logged-in
Chrome running with remote debugging (`chrome --remote-debugging-port=9222`),
skip `login` and pass `--cdp-endpoint http://localhost:9222` to any command.

### 3. Verify

```bash
.venv/bin/python scrape.py user instagram --limit 3
```

A JSON list of posts means it works.

## Usage

```bash
# one-time sign-in (writes both session files)
.venv/bin/python scrape.py login

# a user's recent posts (shortcode + url + thumbnail)
.venv/bin/python scrape.py user natgeo --limit 20

# a hashtag with full metadata + comments + author stats
.venv/bin/python scrape.py hashtag photography --limit 30 --hydrate --comments --author-info --out output/photography.json

# run the browser invisibly in the background
.venv/bin/python scrape.py hashtag photography --headless
```

**Flags** (place after the subcommand):

| Flag | Default | Meaning |
|---|---|---|
| `--out PATH` | *(stdout)* | write JSON to PATH instead of printing |
| `--limit N` | `20` | max posts to fetch |
| `--hydrate` | off | fill caption/likes/views/location/tags/music per post via instagrapi |
| `--comments [N]` | off | also fetch up to N comments/post (default 20); browser; implies `--hydrate` |
| `--author-info` | off | also fetch author follower/post counts + bio; browser; implies `--hydrate` |
| `--analyze` | off | non-VLM video analysis (shots/transcript/people/OCR); needs `analysis` extra; implies `--hydrate`; **slow on CPU** |
| `--whisper-model` | `small` | faster-whisper size for `--analyze` audio (`tiny`/`base`/`small`/…) |
| `--yolo-model` | `yolo11n.pt` | YOLO weights for `--analyze` people detection |
| `--work-dir` | `.reel_work` | where `--analyze` downloads mp4s |
| `--since YYYY-MM-DD` | off | keep posts on/after DATE; **requires `--hydrate`** |
| `--session PATH` | `~/.instagram-scraper/ig_session.json` | instagrapi session file for `--hydrate` |
| `--cdp-endpoint URL` | off | attach to a running Chrome over CDP instead of the saved session |
| `--headless` | off (visible) | run the browser invisibly in the background |
| `-v`, `--verbose` | off | debug logging |

## Authentication: one login

You log in **once** (`scrape.py login`). That single sign-in produces a `sessionid`
cookie, and the scraper derives **both** sessions it needs from it — there is no
separate Playwright login and no separate instagrapi login:

```
scrape.py login  (you sign in once, by hand, in a real Chrome window)
        │  sessionid cookie
        ├──────────────► state.json        →  Playwright (discovery, comments, author)
        └──────────────► ig_session.json   →  instagrapi (hydrate: caption/likes/views/music/video)
```

After that, every `user`/`hashtag` command runs **fully standalone** — Playwright
launches its own Chromium and reuses `state.json`; instagrapi reuses
`ig_session.json`. No browser needs to be open, and nothing external drives it.
When the session expires, re-run `scrape.py login`.

## Non-VLM video analysis (`--analyze`)

Optionally decompose each reel's **video content** — with **no vision-language
model** involved. It downloads the mp4 (from the hydrated `video_urls`) and runs
four best-effort stages:

| Block | Tool | What you get |
|---|---|---|
| `shots` | PySceneDetect | shot cuts, shot count, mean brightness |
| `audio` | faster-whisper | spoken-word **transcript** (timestamped) + language |
| `people` | Ultralytics YOLO | **person count** + per-track screen time |
| `on_screen_text` | RapidOCR (classical OCR) | burned-in text spans |

These are **heavy** deps in the optional `analysis` extra:

```bash
pip install -e ".[analysis]"          # or: pip install -r requirements-analysis.txt
python scrape.py hashtag photography --limit 5 --analyze --out out.json
```

Each stage degrades independently — a missing dep or a failed stage leaves an
`{"error": ...}` note rather than crashing the run.

**Honest limits (it's non-VLM, so it's structural, not semantic):**
- It tells you *what is said, how many people are on screen, how many shots, how
  bright* — **not** what people are *doing*, wearing, or the mood. That needs a VLM.
- **Slow on CPU** — ~6 min for one 37 s reel with `whisper small` + `yolo11n`.
  Use `--whisper-model tiny` to speed up; a GPU helps a lot.
- **People count is inflated** — it's the raw YOLO track count with no cross-frame
  identity dedup, so the same person re-entering counts again.
- **OCR is language-limited** — RapidOCR's default model is Chinese/English and
  mis-reads other scripts (e.g. Korean). Swap in a matching OCR model for accuracy.

## Output — where results go

The scrape result is a **JSON list of post dicts**.

- **Default: nothing is saved.** JSON is printed to **stdout**, a one-line count
  to stderr. Redirect it yourself: `... > my_posts.json`.
- **With `--out PATH`:** JSON is written to that file (parent dirs created).
  The `output/` dir is gitignored — e.g. `--out output/natgeo.json`.

Each post:

- **Discovery (free):** `id`, `url`, `handle`, `media_types`, `media_urls` (thumb).
- **Hydrate (`--hydrate`, 1 call/post):** `text` (caption), `likes`, `comments`
  (count), `timestamp`, `video_urls` (playable mp4), audio (`track_name`,
  `artist_name`, `is_original_audio`), `play_count`, `view_count`, `location`,
  `tagged_users`, `sponsor_tags`, `hashtags`, `mentions`, `author_full_name`,
  `author_is_verified`, `author_is_private`.
- **`--comments [N]` (extra call/post):** `comments_list` — `[{user, text, likes, created_at}]`.
- **`--author-info` (extra call/author):** `author_followers`, `author_following`,
  `author_post_count`, `author_bio`.
- **`--analyze` (downloads + processes mp4):** `analysis` — `{shots, audio
  (transcript+language), people (count+tracks), on_screen_text}`. See
  [Non-VLM video analysis](#non-vlm-video-analysis---analyze).

> **Without `--hydrate`,** `text`/`likes`/`comments`/`timestamp`, the audio
> fields, and the playable `video_urls` are empty/zero, and `media_urls` holds
> only the grid thumbnail — discovery reads the grid, which doesn't carry those.
> `--hydrate` fetches each post individually to fill them.
>
> **Music note:** `track_name`/`artist_name` come from the post's
> `clips_metadata` (licensed library track) or its original-audio info. Many
> reels expose no audio metadata at all via this endpoint — there the fields stay
> empty. The playable `.mp4` in `video_urls` is always present once hydrated.
>
> **`--comments` / `--author-info` are browser-driven.** instagrapi's versions of
> these (`media_comments`, `user_info`) are listing/profile-lookup endpoints that
> Instagram blocks from a flagged IP (observed: `LoginRequired` on comments,
> `TooManyRedirects` on author info, in the same run where `media_info` worked).
> So like discovery, they read off the rendered page instead: `--comments`
> navigates to each post and harvests the comment list (user / text / likes /
> timestamp); `--author-info` reads follower/following/post counts + bio from the
> profile header. They reuse one browser session and are best-effort (a failure
> leaves that field empty, logged, never crashes). Note comment harvesting is
> bounded by what the page renders on scroll — very large threads may not load in full.

### Programmatic use

```python
from instagram_scraper import scrape_user_posts, scrape_hashtag

posts = scrape_user_posts("natgeo", limit=20)
posts = scrape_hashtag("photography", limit=30, hydrate=True)
```

## How it works

1. **Discovery (browser)** — Playwright drives a logged-in Chrome to the
   hashtag/profile page, scrolls to paginate, and harvests `/p/<code>/` links
   from the DOM. We key on DOM links, not Instagram's GraphQL `doc_id`s, because
   the `doc_id`s rotate constantly while the link shape is stable.
2. **Normalize** — each discovered tile becomes a flat post dict matching the
   `twitter_scraper` output shape.
3. **Hydrate (optional, instagrapi)** — `--hydrate` calls `media_info` per
   shortcode to fill caption/engagement/timestamp/music/counts. This is a
   *targeted single-object* fetch, which Instagram still serves even where it
   blocks listing endpoints.
4. **Extras (optional, browser)** — `--comments` / `--author-info` reuse a browser
   session to read comments off each post page and author stats off each profile
   header, because instagrapi's comment/profile endpoints are blocked from a
   flagged IP (see Investigation).

## Investigation: why discovery is browser-driven

The original build listed posts through instagrapi's private API
(`hashtag_medias_recent`, `user_medias`). On 2026-06-11 that path failed
repeatedly with `login_required` (hashtag) and `429` (user lookup) — **even with
a freshly minted, valid session.** We isolated the cause with one known-good
session (the same one that successfully downloaded a reel minutes earlier):

| Operation | Kind | Result |
|---|---|---|
| Reel download by URL (`media_info` → CDN) | targeted single-object fetch | ✅ works |
| Hashtag recent (`hashtag_medias_recent`) | discovery / listing (private API) | ❌ `login_required` |
| User lookup (`web_profile_info`) | discovery / listing (public API) | ❌ `429` |

**Conclusion: it's the *endpoint category* + IP reputation, not the session,
login, or code.** Instagram polices bulk discovery/listing hardest (that's what
scrapers abuse) and refuses a non-browser client from a flagged IP, while it
still serves targeted single-object fetches.

We then confirmed the fix: navigating a **real logged-in Chrome** to the same
hashtag (`/explore/tags/<tag>/`, which Instagram redirects to
`/explore/search/keyword/?q=#<tag>`) rendered 24 posts with no block. A trusted
browser is served what an impersonating client is refused — so discovery now
runs through the browser, and instagrapi is demoted to optional per-post
hydration.

## Caveats

- **Browser-driven discovery is slower and needs a logged-in profile.** It scrolls
  a real page; expect seconds, not milliseconds, and keep the profile signed in.
- **DOM/redirect shapes drift.** Instagram changes its markup and routes; if
  discovery returns nothing, see `docs/MAINTENANCE.md`.
- **Private accounts** you don't follow return nothing.
- **`--hydrate` uses instagrapi** and so depends on a session file and on
  per-post fetches staying unblocked; it's best-effort (a post that won't
  hydrate is returned in its sparse form).
