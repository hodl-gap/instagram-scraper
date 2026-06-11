# instagram_scraper

Standalone Instagram scraper — a sibling to `twitter_scraper`, same shape
(`scrape.py` CLI, `src/` layout, JSON output with identical media fields).
Wraps [instagrapi](https://github.com/subzeroid/instagrapi) for the private-API
work. Auth is a browser-born session — **no username/password**.

## What it can (and can't) do

Instagram has **no free-text post search** and **no server-side date filter**,
so this is deliberately narrower than the Twitter scraper:

| Mode | Accepts | Returns |
|---|---|---|
| `scrape.py user <username>` | a username | that user's recent posts |
| `scrape.py hashtag <tag>` | one hashtag | recent (or `--top`) posts for that tag |

- **No keyword/boolean search** (`AND`/`OR`), no `from:` / `min_faves:` operators.
- **No real `since:`/`until:`.** `--since` exists only as a newest-first
  *early-stop* on chronological feeds (see [About `--since`](#about---since-read-this));
  historical windows aren't practical, so there's no `--until`.
- `user` mode is the most reliable surface; **hashtag scraping is rate-limited
  harder** by Instagram.

## Layout

```
instagram_scraper/
├── scrape.py                          # CLI entry point (user / hashtag modes)
├── ig_session.json                    # session secret (gitignored) — created at setup
├── scripts/
│   └── ig_session_bootstrap.py        # one-time session bootstrap from a sessionid cookie
├── docs/
│   └── MAINTENANCE.md                 # what to do when scraping breaks
└── src/instagram_scraper/
    ├── client.py                      # thin instagrapi wrapper (load session, no login)
    └── fetch.py                       # instagrapi Media -> flat post dicts
```

## Setup

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Authenticate (get a session)

There is **no username/password field anywhere** — auth is a `sessionid` cookie
lifted once from a browser already logged into Instagram. (Logging in with
credentials programmatically just trips Instagram's `challenge_required` wall;
the session route avoids it.)

1. Log into <https://www.instagram.com> in a normal browser, with the account
   you want to scrape as.
2. DevTools → **Application → Cookies → `https://www.instagram.com`** → copy the
   **`sessionid`** value.
3. Bootstrap the session file:

```bash
.venv/bin/python scripts/ig_session_bootstrap.py --sessionid 'PASTE_SESSIONID_HERE' --test
```

This writes `ig_session.json` (gitignored — the `sessionid` cookie plus a
persisted device identity) and prints the logged-in handle.

> **Do NOT tap "Log out"** in that browser afterwards — it invalidates the
> `sessionid` server-side and the session file dies (re-bootstrap to recover).

### 3. Verify

```bash
.venv/bin/python scrape.py user instagram --limit 3
```

A JSON list of posts means auth works.

> **Security:** `ig_session.json` is a live session secret (cookie + device
> identity) — anyone holding it can act as that account. It's gitignored; never
> commit it or move it between machines unencrypted.

## Usage

`scrape.py` is the central entrypoint with two subcommands:

| Command | Accepts | Scrapes |
|---|---|---|
| `scrape.py user <username>` | a username | that user's recent posts |
| `scrape.py hashtag <tag>` | one hashtag | recent (or `--top`) posts |

```bash
# user's recent posts
.venv/bin/python scrape.py user natgeo --limit 20

# user's posts since a date (newest->oldest, stops early at the cutoff)
.venv/bin/python scrape.py user natgeo --since 2026-06-01

# hashtag — most recent, then top/ranked
.venv/bin/python scrape.py hashtag photography --limit 30
.venv/bin/python scrape.py hashtag photography --top --out output/photography.json
```

**Flags** (place after the subcommand):

| Flag | Default | Meaning |
|---|---|---|
| `--out PATH` | *(stdout)* | write JSON to PATH instead of printing |
| `--limit N` | `20` | max posts to fetch (also the scan cap when `--since` is set) |
| `--since YYYY-MM-DD` | off | `user` + `hashtag` (recent) only; newest→oldest early-stop |
| `--top` | off | `hashtag`: ranked instead of most-recent (ignores `--since`) |
| `--session PATH` | `$IG_SESSION_PATH` or `./ig_session.json` | session file to use |
| `-v`, `--verbose` | off | debug logging |

Run `scrape.py user --help` or `scrape.py hashtag --help` for the full list.

### About `--since` (read this)

Instagram can only be read **newest-first, from "now" backward** — there is no
server-side date query. So `--since DATE`:

- walks posts newest→oldest and **stops** once it crosses DATE (an early-stop,
  not a "fetch everything then filter"),
- works on `user` and `hashtag` **without** `--top` only (ranked results aren't
  time-ordered),
- is bounded by `--limit` — raise it for a longer window,
- **cannot** cheaply fetch an arbitrary *old* window (that would mean paging
  through everything newer first), which is why there is **no `--until`**.

## Output — where results go

The scrape result is a **JSON list of post dicts**.

- **Default: nothing is saved.** JSON is printed to **stdout** and a one-line
  count goes to stderr. Redirect it yourself: `... > my_posts.json`.
- **With `--out PATH`:** JSON is written to that file (parent dirs created as
  needed). The `output/` directory is provided for this and is gitignored —
  e.g. `--out output/natgeo.json`.

Each post: `id`, `url`, `handle`, `text` (caption), `likes`, `comments`,
`timestamp` (ISO 8601), plus media:

- `media_urls` + `media_types` — index-aligned. Photo = the image URL; video =
  the **poster thumbnail** with `media_types[i] = "video"`. Albums expand into
  one entry per carousel item.
- `video_urls` — the **playable** `.mp4` URL for each video item (empty for
  photo-only posts).

### Programmatic use

```python
from instagram_scraper import scrape_user_posts, scrape_hashtag

posts = scrape_user_posts("natgeo", limit=20)
posts = scrape_hashtag("photography", limit=30, top=True)
```

## How it works

1. **Auth** — load a saved session (cookie + persisted device) via instagrapi.
   No `login()` call exists, so the credential-login challenge wall can't fire.
2. **`user` mode** — `user_id_from_username` → `user_medias` (paginated
   early-stop when `--since` is set).
3. **`hashtag` mode** — `hashtag_medias_recent` (or `hashtag_medias_top`).
4. **Normalize** — instagrapi `Media` objects → flat dicts matching the
   `twitter_scraper` output shape.

## Caveats

- **instagrapi tracks Instagram's private API.** When scraping breaks, run
  `pip install -U instagrapi` first. See `docs/MAINTENANCE.md`.
- **Private accounts** you don't follow return nothing.
- **Don't log out** of the bootstrap browser session (kills the `sessionid`).
