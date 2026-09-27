# Narrative comments (Workers Free)

Fans comment on every Chiefs Narrative edition without a GitHub (or any)
account. The site stays static on GitHub Pages. This folder is the $0
backend: a Cloudflare Worker plus SQLite Durable Objects, with a local
Python stand-in for previews and screenshots.

Node **>= 22** is required (`node:sqlite` in the Worker test suite). CI
uses Node 22.

## Deploy target

`arrowheadpaesano.com` DNS is on **Namecheap**
(`dns1`/`dns2.registrar-servers.com`). The apex points at GitHub Pages.
It is **not** a Cloudflare zone.

The Worker therefore runs on `*.workers.dev`. On `workers.dev` the
Workers **Cache API is a no-op** (`caches.default` never stores). Cost
math must not assume cache hits. The versioned cache code stays in the
Worker only so it is harmless and correct if the zone later moves; it
is not the cost control.

## Architecture

- One `CommentThread` Durable Object per **known** story slug. That object
  is the source of truth for the thread. There is no hot global key per
  comment.
- A directory Durable Object (`__directory__`) is an idempotent slug index
  for `/moderate/` only. It is **never** on the public read path. After a
  successful insert the Worker returns 201 even if directory registration
  fails; `ctx.waitUntil` retries `INSERT OR IGNORE`.
- Posts send a client `requestId` (UUID). The thread stores it `UNIQUE`.
  The Worker looks that id up in the thread DO **before Turnstile and
  before** the 20s post rate limit. If the row is already there, the
  Worker returns it (replay) without calling siteverify. That covers the
  “saved, then 5xx on the way back” case, where the Turnstile token is
  already spent. A retry of an **unsaved** post still needs a fresh
  token: the browser resets the widget and waits before retrying once.
  Replay does not make a brand-new post impossible to duplicate if the
  client throws away the `requestId` and submits again.
- Public `GET /comments?slug=` ignores any caller `limit` and always
  returns the newest **50**. Browsers get `Cache-Control: no-store` so a
  hide or delete is visible on the next page load. Optional edge cache
  (only if the Worker later sits on a Cloudflare zone) is keyed by
  **slug + cursor + version**. The version pointer compare-then-put is
  best-effort and **only matters on a custom domain**; on `workers.dev`
  the Cache API is a no-op, so `x-comments-cache` is always `miss`.
- One `RateBucket` Durable Object per IPv4 or IPv6 `/64` for **posts**
  and the 5-minute admin-token lockout. Public reads do **not** touch
  `RATES`. If `RATES` is missing, post and admin routes fail closed
  (503); reads still work.
- Hugo emits `/comments-slugs.json` at build time. The Worker caches that
  list in isolate memory for **60 seconds** and will refetch at most
  **once per 60s** on an unknown-slug miss. Unknown slugs return 404 and
  never instantiate a thread DO.

## Per-request costs (workers.dev, no Cache API)

| Path | Durable Object requests | SQLite rows (typical) |
| --- | --- | --- |
| Public `GET /comments?slug=` | **1** thread `/list` | ~50 comment rows + 1 `meta.version` |
| Public `GET` unknown slug | **0** (slug list from memory, or 1 origin fetch if the 60s TTL expired) | 0 |
| `POST /comments` (new) | **3**: thread `/by-request-id` + `RATES /hit` + thread `/insert` (+ directory write-behind, not on the response path) | 1 comment lookup + 1 rate write + 1 comment insert + 1 version bump |
| `POST /comments` (same `requestId`) | **1** thread `/by-request-id` | 1 comment lookup |
| Hide / unhide / delete | 1 `RATES /auth` + 1 thread write | 1 comment + 1 version bump |
| Admin `GET ?all=1` | 1 `RATES /auth` + 1 directory + **≤4** thread `/list` (4 slugs × 1 page). The client follows `nextSlug` / `nextBySlug`. | 1 auth + 4 × 50 hidden rows |

## Pagination

The public thread shows the **newest 50** comments. If more exist, the
API returns `next` (`createdAt|id`, older than that cursor, id as
tiebreak). The page has a **Load older comments** button that follows
`next`. A failed older-page fetch shows **Couldn't load older comments.**
plus **Retry** and does **not** disable the form.

`/moderate/` pages the directory (`nextSlug`, 4 slugs per request, one
hidden page each) and follows `/comments?slug=&hidden=1` `next` so every
comment in a thread can be hidden or deleted. If the admin client hits
its page cap, it shows a notice.

## Slug allowlist

- Default: fetch `COMMENTS_SLUGS_URL` (or
  `https://arrowheadpaesano.com/comments-slugs.json`) and cache it in
  Worker isolate memory for **60 seconds**.
- An unknown slug may trigger **at most one origin refetch per 60s**.
  A brand-new edition can stay 404 until that TTL elapses or the isolate
  refetches.
- `COMMENTS_KNOWN_SLUGS` is an optional comma-separated pin (tests or an
  emergency override). When it is set, the Worker does not fetch the
  JSON list.

## Schema

`migrateThreadSchema` runs inside `blockConcurrencyWhile` on every
thread DO:

- `CREATE TABLE IF NOT EXISTS` for `comments`, `slugs`, and `meta`
- `schema_version` in `meta` (currently `1`)
- `ALTER TABLE comments ADD COLUMN requestId` when the column is missing
- `CREATE UNIQUE INDEX IF NOT EXISTS comments_request_id`

Nothing has been deployed yet; the migration is still written so an
old-shape table upgrades in place.

## Free-tier math

Workers Free (the real ceiling on `workers.dev`):

- **100,000 Worker requests / day** — this is the scarce quota. Every
  page view that hits the comments Worker counts, cache or not.
- 10 ms CPU / request
- SQLite Durable Objects: **100,000 DO requests / day**, **5 million
  row reads / day**, **100,000 row writes / day**, 5 GB storage,
  13,000 GB-s duration / day

Without the Cache API, every public list is 1 Worker request + 1 DO
request + a `LIMIT 51` scan plus a `meta.version` read (**52 row
reads** if the thread is full):

- The Worker and DO **request** caps are both 100k/day. A 100k-list
  day would be 100k × 52 = **5.2 million row reads**, which is over
  the 5M row-read cap.
- The binding quota on a full-thread day is therefore **row reads**:
  5,000,000 / 52 ≈ **96,000 public lists/day** (~67/minute if spread
  evenly). Empty or short threads read fewer rows.
- Writes (post / hide / delete) stay tiny versus the 100k write cap.
- Directory traffic is admin-only.

Stay off paid KV/D1/R2.

## Quota abuse on workers.dev

Public reads are **unlimited** from the application's point of view:
there is no per-IP GET limiter (a RateBucket on every read would
double DO cost). A script at about **100 requests/second** burns the
**100k Worker requests/day** in about **17 minutes**. Cloudflare then
returns **error 1027** for that account until the free-tier daily
reset at **00:00 UTC (5 PM Pacific)**. That 1027 blocks **everything**
on the Worker, including posting and `/moderate/`.

The only real fix is to move DNS to Cloudflare (free) and attach
`comments.arrowheadpaesano.com`, then add a **free WAF rate-limiting
rule** on that hostname. The Cache API also starts working on that
custom domain. On today's `workers.dev` deploy there is no WAF and
no Cache API.

## Optional: move DNS to Cloudflare (free)

Namecheap can keep the registration. Point the nameservers at
Cloudflare (free zone). Then attach a **custom domain** such as
`comments.arrowheadpaesano.com` to this Worker.

On a Cloudflare zone the Cache API works. Versioned list entries can
then serve a hit at **0 Durable Object requests**. Browsers still
receive `Cache-Control: no-store`, so a hide or delete appears on the
next load. Residual delay if you later enable an edge cache: an
in-flight miss that listed before a hide finished could serve one stale
response; the version pointer is never moved backwards, and that
in-flight body expires with the 45s edge TTL. On today's `workers.dev`
deploy there is no edge cache, so the next GET always sees the new
version.

## One-time owner setup (all free tiers)

1. Create a Cloudflare account and a Worker named
   `arrowhead-paesano-comments` on the **Workers Free** plan. SQLite-backed
   Durable Objects are included; do not attach KV, D1, or a paid database.
2. From this directory:

   ```bash
   npx wrangler deploy
   wrangler secret put COMMENTS_ADMIN_TOKEN
   wrangler secret put TURNSTILE_SECRET
   ```

3. Create a Cloudflare Turnstile site (free, CAPTCHA-less). Use the
   always-pass test sitekey `1x00000000000000000000AA` only on localhost.
4. Set the Worker URL and the production Turnstile sitekey in `hugo.yaml`:

   ```yaml
   params:
     commentsApiUrl: "https://arrowhead-paesano-comments.<account>.workers.dev"
     commentsTurnstileSiteKey: "<production sitekey>"
   ```

   If either value is unset or whitespace, Hugo renders **nothing** — no
   heading, empty state, or form.
5. `COMMENTS_CORS_ORIGINS` and `COMMENTS_SLUGS_URL` are already set in
   `wrangler.toml` for `arrowheadpaesano.com`.

## Local preview

```bash
COMMENTS_ADMIN_TOKEN=dev-admin COMMENTS_DEV=1 python tools/comments/server.py
# in another shell
HUGO_COMMENTS_API_URL=http://127.0.0.1:8787 \
HUGO_TURNSTILE_SITE_KEY=1x00000000000000000000AA \
npm run dev
```

The local Python server accepts the offline Turnstile token `test-pass`.
The production Worker always calls Turnstile siteverify and never honors
`test-pass`.

## Moderation

Stephen opens `/moderate/` (the page is `noindex, nofollow`). Paste
`COMMENTS_ADMIN_TOKEN` — it stays in memory for that visit only and is
never written to `localStorage` or `sessionStorage`.

- **Hide** takes a comment off the public story. **Unhide** puts it back.
- **Delete** hard-deletes the SQLite row (name, body, and id are gone
  from the live database). Cloudflare still keeps **point-in-time
  recovery for SQLite Durable Objects for 30 days**, so a deleted row
  can theoretically be restored from a PIT snapshot during that window.
- There is **no admin-token bypass** for the lockout. Eight bad guesses
  from one IPv4 (or IPv6 `/64`) lock that address for **5 minutes**. A
  later correct token from the same address still gets 429 until the
  window expires. Other addresses are unaffected.
- If the `RATES` binding is missing, **post and admin** routes fail
  closed (503). Public reads do not use `RATES`.

## Tests

```bash
python -m unittest tools.tests.test_comments -v
node --test tools/comments/worker.test.mjs
```

The Node suite drives the real `CommentThread` / `RateBucket` classes
through `THREADS` / `RATES` with a `node:sqlite` shim of the same SQL.
