# Narrative comments (Workers Free)

Fans comment on every Chiefs Narrative edition without a GitHub (or any)
account. The site stays static on GitHub Pages. This folder is the $0
backend: a Cloudflare Worker plus SQLite Durable Objects, with a local
Python stand-in for previews and screenshots.

Node **>= 22** is required (`node:sqlite` in the Worker test suite). CI
uses Node 22.

## Architecture

- One `CommentThread` Durable Object per **known** story slug. That object
  is the source of truth for the thread. There is no hot global key per
  comment.
- A directory Durable Object (`__directory__`) is an idempotent slug index
  for `/moderate/` only. It is **never** on the public read path. After a
  successful insert the Worker returns 201 even if directory registration
  fails; `ctx.waitUntil` retries `INSERT OR IGNORE`.
- Posts send a client `requestId` (UUID). The thread stores it `UNIQUE`,
  so a retry after a 500 cannot double-post.
- Public `GET /comments?slug=` is served from the Workers Cache API
  (`caches.default`, 45s TTL) keyed by **slug + cursor + version**. A
  cache hit costs **0 Durable Object requests**. A miss costs **exactly
  one** thread `/list`. Writes bump a per-slug version in the thread DO
  and in cache so the next read misses.
- One `RateBucket` Durable Object per IPv4 or IPv6 `/64` for **posts**
  and the 5-minute admin-token lockout. Public GET rate limiting (600/min
  per `/64`) runs **only on cache misses**.
- Hugo emits `/comments-slugs.json` at build time. Unknown slugs return
  404 and never instantiate a thread DO.

## Pagination

The public thread shows the **newest 50** comments. If more exist, the
API returns `next` (`createdAt|id`, older than that cursor, id as
tiebreak). The page has a **Load older comments** button that follows
`next`.

`/moderate/` pages the directory (`nextSlug`, 8 slugs per request) and
follows `/comments?slug=&hidden=1` `next` so every comment in a thread
can be hidden or deleted. If the admin client hits its page cap, it
shows a notice.

## Slug allowlist

- Default: fetch `COMMENTS_SLUGS_URL` (or
  `https://arrowheadpaesano.com/comments-slugs.json`) and cache it in
  Worker memory for **60 seconds**.
- A 404 for an unknown slug **refetches once** so a brand-new edition
  does not stay 404 until the TTL ends.
- `COMMENTS_KNOWN_SLUGS` is an optional comma-separated pin (tests or an
  emergency override). When it is set, the Worker does not fetch the
  JSON list.

## Free-tier math

Workers Free: 100,000 Worker requests/day, 10 ms CPU; SQLite DOs:
5 million row reads/day, 100,000 row writes/day, 5 GB.

Cache hits still count as Worker requests (the 100k cap is unchanged)
but they do **not** touch Durable Objects. The scarce quota is DO row
reads/writes:

- One live edition, 45s first-page TTL: `86400 / 45 ≈ 1,920` list
  fills/day × ~50 rows ≈ **96k row reads/day** — well under 5M.
- Before this cache, every page view was 1 thread DO + 1 rate DO.
- Writes (post / hide / delete) stay tiny versus the 100k write cap.
- Directory traffic is admin-only.

Stay off paid KV/D1/R2.

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
- If the `RATES` binding is missing, admin routes fail closed (503).

## Tests

```bash
python -m unittest tools.tests.test_comments -v
node --test tools/comments/worker.test.mjs
```

The Node suite drives the real `CommentThread` / `RateBucket` classes
through `THREADS` / `RATES` with a `node:sqlite` shim of the same SQL.
