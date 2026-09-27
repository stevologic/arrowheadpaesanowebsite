# Narrative comments (Workers Free)

Fans comment on every Chiefs Narrative edition without a GitHub (or any)
account. The site stays static on GitHub Pages. This folder is the $0
backend: a Cloudflare Worker plus SQLite Durable Objects, with a local
Python stand-in for previews and screenshots.

## Architecture

- One `CommentThread` Durable Object per **known** story slug. That object
  is the source of truth for the thread. There is no hot global key per
  comment.
- A single directory Durable Object (`__directory__`) holds an idempotent
  `INSERT OR IGNORE` list of slugs that actually have comments. `/moderate/`
  pages that directory (default 8 slugs) and fetches those threads in
  bounded parallel (4 at a time).
- One `RateBucket` Durable Object per IPv4 address or IPv6 `/64`. Post
  limits, public GET limits, and admin-token lockout share that bucket.
- Hugo emits `/comments-slugs.json` at build time from
  `data/narrative.json` plus `data/narrative_editions/`. Unknown slugs
  return 404 and never instantiate a thread DO.

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
   `wrangler.toml` for `arrowheadpaesano.com`. `COMMENTS_SLUGS_URL` should
   point at the live `/comments-slugs.json` after the first Pages deploy
   that includes this output format.

Free-tier notes: Workers Free + Turnstile free + GitHub Pages. Stay off
paid KV/D1/R2. Thread lists are capped (50) and paginated; admin fan-out
is capped per request.

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
- **Delete** hard-deletes the SQLite row (name, body, and id are gone).
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
