# Maintenance — Instagram scraper

Unlike `twitter_scraper` (which owns its HTTP/GraphQL layer), this project
delegates all private-API work to **instagrapi**. So most breakage is fixed
*upstream*, not here.

## When scraping breaks

1. **`pip install -U instagrapi` first.** Instagram rotates its internals and
   instagrapi ships fixes on its own cadence — an upgrade resolves most breakage.
2. **`login_required` / empty results** → the session expired or was
   invalidated (someone tapped "Log out", or Instagram killed it). Re-bootstrap
   with `scripts/ig_session_bootstrap.py`.
3. **`challenge_required` / `429`** → too aggressive, or the IP/account is
   flagged. Slow down (raise `delay_range` in `client.py`), lower `--limit`,
   prefer `user` over `hashtag` (hashtag is rate-limited harder).

## Auth notes

- Auth is a **session file only**. There is **no username/password path** in
  this code by design — that avoids the `load_settings()` + `login()`
  double-fire that re-triggers Instagram's challenge wall.
- The session file = `sessionid` cookie **plus** a persisted fake-device
  identity (uuids, device_id, android_id, UA). Both must stay stable;
  regenerating the device each run looks suspicious.

## Capability limits (by design, not bugs)

- **No free-text post search** — Instagram's `*search*` endpoints return
  entities (users/hashtags/places), not a post feed. Hence `hashtag` is the
  only keyword-ish surface, and it's hashtag-only.
- **No server-side date filter** — `--since` is a client-side newest->oldest
  early-stop (chronological feeds only). Arbitrary *old* windows aren't
  practical (you'd page through everything newer first), which is why there is
  no `--until`.
