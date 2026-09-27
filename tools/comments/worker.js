/**
 * Cloudflare Worker + SQLite Durable Objects for narrative comments.
 *
 * $0 on Workers Free (SQLite-backed DOs only). One CommentThread DO per
 * known story slug so post/hide/delete are atomic; RateBucket DO per IPv4
 * or IPv6 /64 for post limits and bad-admin-token lockout.
 *
 * Deploy target: arrowheadpaesano.com DNS is on Namecheap, apex → GitHub
 * Pages. The Worker runs on *.workers.dev, where the Cache API is a
 * no-op. Public GET /comments?slug= therefore costs exactly one thread
 * DO /list. Do not put a RateBucket on that path. caches.default is
 * kept only so a later custom-domain move can reuse versioned keys;
 * browsers always get Cache-Control: no-store. Never put an older
 * version over a newer one.
 *
 * Secrets (never commit):
 *   wrangler secret put COMMENTS_ADMIN_TOKEN
 *   wrangler secret put TURNSTILE_SECRET
 *
 * Vars:
 *   COMMENTS_CORS_ORIGINS  comma-separated production origins
 *   COMMENTS_SLUGS_URL     Hugo-emitted allowlist (comments-slugs.json)
 *   COMMENTS_KNOWN_SLUGS   optional comma list (tests / emergency pin)
 *   COMMENTS_DEV           "1" to allow localhost CORS
 */
export const HONEYPOT_FIELD = "nrt_hp_x7";
export const SLUG_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
export const REQUEST_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_NAME = 40;
const MIN_NAME = 2;
const MAX_BODY = 1000;
const MIN_BODY = 2;
export const RATE_WINDOW_SEC = 10 * 60;
export const RATE_MAX = 5;
export const RATE_MIN_INTERVAL_SEC = 20;
export const AUTH_FAIL_MAX = 8;
export const AUTH_FAIL_WINDOW_SEC = 5 * 60;
export const THREAD_LIST_LIMIT = 50;
export const ADMIN_SLUG_PAGE = 4;
export const ADMIN_PARALLEL = 4;
export const LIST_CACHE_TTL_SEC = 45;
export const SLUGS_TTL_MS = 60 * 1000;
export const SCHEMA_VERSION = 1;
const DIR_NAME = "__directory__";
const DEFAULT_SLUGS_URL = "https://arrowheadpaesano.com/comments-slugs.json";

let slugCache = { at: 0, slugs: null, lastFetchAt: 0 };

export function resetSlugCache() {
  slugCache = { at: 0, slugs: null, lastFetchAt: 0 };
}

function clean(value, limit) {
  return String(value || "")
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "")
    .replace(/[<>]/g, "")
    .trim()
    .slice(0, limit);
}

function publicComment(row) {
  return {
    id: row.id,
    slug: row.slug,
    name: row.name,
    body: row.body,
    createdAt: row.createdAt,
    hidden: Boolean(row.hidden),
  };
}

function parseThreadId(id) {
  const text = String(id || "");
  const cut = text.lastIndexOf("~");
  if (cut <= 0) return { slug: "", id: text };
  return { slug: text.slice(0, cut), id: text };
}

function clientIp(request) {
  return (
    request.headers.get("CF-Connecting-IP") ||
    (request.headers.get("X-Forwarded-For") || "").split(",")[0].trim() ||
    "unknown"
  );
}

function expandIPv6(ip) {
  const bare = String(ip || "").split("%")[0].toLowerCase();
  const [head, tail] = bare.split("::");
  const headParts = head ? head.split(":").filter(Boolean) : [];
  const tailParts = tail ? tail.split(":").filter(Boolean) : [];
  const missing = Math.max(8 - headParts.length - tailParts.length, 0);
  return [...headParts, ...Array(missing).fill("0"), ...tailParts]
    .slice(0, 8)
    .map((part) => part.replace(/^0+(?=\w)/, "") || "0");
}

export function rateKey(ip) {
  const raw = String(ip || "unknown").trim() || "unknown";
  if (raw === "unknown") return raw;
  const bare = raw.split("%")[0];
  if (bare.includes(".") && bare.includes(":")) {
    return bare.slice(bare.lastIndexOf(":") + 1);
  }
  if (bare.includes(":")) {
    return expandIPv6(bare).slice(0, 4).join(":") + "::/64";
  }
  return bare;
}

export async function timingSafeEqual(a, b) {
  const encoder = new TextEncoder();
  const left = await crypto.subtle.digest("SHA-256", encoder.encode(String(a || "")));
  const right = await crypto.subtle.digest("SHA-256", encoder.encode(String(b || "")));
  const leftBytes = new Uint8Array(left);
  const rightBytes = new Uint8Array(right);
  let out = 0;
  for (let i = 0; i < leftBytes.length; i += 1) out |= leftBytes[i] ^ rightBytes[i];
  return out === 0;
}

function allowedOrigins(env) {
  return String(env.COMMENTS_CORS_ORIGINS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function isDev(env) {
  const flag = String(env.COMMENTS_DEV || "").toLowerCase();
  return flag === "1" || flag === "true" || flag === "yes";
}

export function corsOrigin(request, env) {
  const origin = request.headers.get("Origin") || "";
  if (!origin) return "";
  if (allowedOrigins(env).includes(origin)) return origin;
  if (isDev(env) && /^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(origin)) return origin;
  return "";
}

function json(request, env, status, payload, extra) {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type, X-Turnstile-Token",
  };
  const origin = corsOrigin(request, env);
  if (origin) {
    headers["access-control-allow-origin"] = origin;
    headers.vary = "Origin";
  }
  if (extra) Object.assign(headers, extra);
  return new Response(payload === null ? null : JSON.stringify(payload), { status, headers });
}

function adminHeaders() {
  return { "X-Robots-Tag": "noindex, nofollow" };
}

function clampLimit(value, max) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) return max;
  return Math.min(parsed, max);
}

function unknownStory() {
  const err = new Error("Unknown story.");
  err.status = 404;
  return err;
}

function parseKnownSlugs(data) {
  const list = Array.isArray(data) ? data : data && Array.isArray(data.slugs) ? data.slugs : [];
  return new Set(list.map((item) => String(item || "").trim()).filter(Boolean));
}

function bundledSlugs(env) {
  if (env.__knownSlugs instanceof Set) return env.__knownSlugs;
  if (Array.isArray(env.__knownSlugs)) return new Set(env.__knownSlugs);
  const bundled = String(env.COMMENTS_KNOWN_SLUGS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return bundled.length ? new Set(bundled) : null;
}

export async function knownSlugs(env, { force = false } = {}) {
  const pinned = bundledSlugs(env);
  if (pinned) return pinned;
  const now = Date.now();
  const haveList = slugCache.slugs instanceof Set;
  const fresh = haveList && now - slugCache.at < SLUGS_TTL_MS;
  if (!force && fresh) return slugCache.slugs;
  const fetchedRecently = slugCache.lastFetchAt > 0 && now - slugCache.lastFetchAt < SLUGS_TTL_MS;
  if (fetchedRecently) {
    if (haveList) return slugCache.slugs;
    const err = new Error("Story list is unavailable.");
    err.status = 503;
    throw err;
  }
  slugCache.lastFetchAt = now;
  const url = String(env.COMMENTS_SLUGS_URL || "").trim() || DEFAULT_SLUGS_URL;
  const res = await fetch(url);
  if (!res.ok) {
    const err = new Error("Story list is unavailable.");
    err.status = 503;
    throw err;
  }
  const slugs = parseKnownSlugs(await res.json().catch(() => []));
  slugCache = { at: Date.now(), slugs, lastFetchAt: slugCache.lastFetchAt };
  return slugs;
}

async function assertKnownSlug(env, slug) {
  if (!SLUG_RE.test(slug) || slug === DIR_NAME) throw unknownStory();
  const known = await knownSlugs(env);
  if (known.has(slug)) return;
  if (bundledSlugs(env)) throw unknownStory();
  const again = await knownSlugs(env, { force: true });
  if (!again.has(slug)) throw unknownStory();
}

function tableColumns(sql, table) {
  return sql
    .exec(`PRAGMA table_info(${table})`)
    .toArray()
    .map((row) => row.name);
}

export function migrateThreadSchema(sql) {
  sql.exec(`CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    body TEXT NOT NULL,
    createdAt TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0
  )`);
  sql.exec(`CREATE TABLE IF NOT EXISTS slugs (
    slug TEXT PRIMARY KEY
  )`);
  sql.exec(`CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
  )`);
  sql.exec(`INSERT OR IGNORE INTO meta (key, value) VALUES ('version', 0)`);
  sql.exec(`INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', 0)`);
  const columns = new Set(tableColumns(sql, "comments"));
  if (!columns.has("requestId")) {
    sql.exec(`ALTER TABLE comments ADD COLUMN requestId TEXT`);
  }
  sql.exec(`CREATE UNIQUE INDEX IF NOT EXISTS comments_request_id ON comments (requestId)`);
  sql.exec(`CREATE INDEX IF NOT EXISTS comments_slug_created ON comments (slug, createdAt, id)`);
  sql.exec(`INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ${SCHEMA_VERSION})`);
}

export function initThreadSchema(sql) {
  migrateThreadSchema(sql);
}

export function initRateSchema(sql) {
  sql.exec(`CREATE TABLE IF NOT EXISTS hits (
    ip TEXT NOT NULL,
    stamp REAL NOT NULL
  )`);
  sql.exec(`CREATE TABLE IF NOT EXISTS authfails (
    ip TEXT NOT NULL,
    stamp REAL NOT NULL
  )`);
  sql.exec(`CREATE INDEX IF NOT EXISTS hits_ip_stamp ON hits (ip, stamp)`);
  sql.exec(`CREATE INDEX IF NOT EXISTS authfails_ip_stamp ON authfails (ip, stamp)`);
}

function runInit(ctx, fn) {
  if (ctx && typeof ctx.blockConcurrencyWhile === "function") {
    return ctx.blockConcurrencyWhile(async () => {
      fn();
    });
  }
  fn();
  return Promise.resolve();
}

function parseCursor(after) {
  if (!after) return { created: "", id: "" };
  const cut = String(after).indexOf("|");
  if (cut <= 0) return { created: "", id: "" };
  return { created: after.slice(0, cut), id: after.slice(cut + 1) };
}

export class SqliteThreadStore {
  constructor(sql) {
    this.sql = sql;
  }

  version() {
    const rows = this.sql.exec(`SELECT value FROM meta WHERE key = 'version'`).toArray();
    return rows.length ? Number(rows[0].value) || 0 : 0;
  }

  bumpVersion() {
    this.sql.exec(`UPDATE meta SET value = value + 1 WHERE key = 'version'`);
    return this.version();
  }

  list(slug, { includeHidden = false, limit = THREAD_LIST_LIMIT, after = "" } = {}) {
    const cap = clampLimit(limit, THREAD_LIST_LIMIT);
    const cursor = parseCursor(after);
    const rows = this.sql
      .exec(
        `SELECT id, slug, name, body, createdAt, hidden FROM comments
         WHERE (? = '' OR slug = ?)
           AND (? = 1 OR hidden = 0)
           AND (? = '' OR createdAt < ? OR (createdAt = ? AND id < ?))
         ORDER BY createdAt DESC, id DESC
         LIMIT ?`,
        slug || "",
        slug || "",
        includeHidden ? 1 : 0,
        cursor.created,
        cursor.created,
        cursor.created,
        cursor.id,
        cap + 1
      )
      .toArray();
    const hasMore = rows.length > cap;
    const page = rows.slice(0, cap);
    const last = page[page.length - 1];
    return {
      comments: page.map((row) => publicComment({ ...row, hidden: Boolean(row.hidden) })),
      next: hasMore && last ? `${last.createdAt}|${last.id}` : null,
      version: this.version(),
    };
  }

  findByRequestId(requestId) {
    if (!requestId) return null;
    const rows = this.sql
      .exec(
        `SELECT id, slug, name, body, createdAt, hidden, requestId FROM comments WHERE requestId = ?`,
        requestId
      )
      .toArray();
    return rows.length ? publicComment({ ...rows[0], hidden: Boolean(rows[0].hidden) }) : null;
  }

  insert(row) {
    const replayed = this.findByRequestId(row.requestId);
    if (replayed) return { comment: replayed, replayed: true, version: this.version() };
    this.sql.exec(
      `INSERT INTO comments (id, slug, name, body, createdAt, hidden, requestId)
       VALUES (?, ?, ?, ?, ?, 0, ?)`,
      row.id,
      row.slug,
      row.name,
      row.body,
      row.createdAt,
      row.requestId || null
    );
    const saved = this.sql.exec(`SELECT id FROM comments WHERE id = ?`, row.id).toArray();
    if (!saved.length) return { comment: null, replayed: false, version: this.version() };
    return { comment: publicComment(row), replayed: false, version: this.bumpVersion() };
  }

  hide(id, hidden) {
    const rows = this.sql
      .exec(`SELECT id, slug, name, body, createdAt, hidden FROM comments WHERE id = ?`, id)
      .toArray();
    if (!rows.length) return { comment: null, version: this.version() };
    this.sql.exec(`UPDATE comments SET hidden = ? WHERE id = ?`, hidden ? 1 : 0, id);
    return {
      comment: publicComment({ ...rows[0], hidden: Boolean(hidden) }),
      version: this.bumpVersion(),
    };
  }

  delete(id) {
    const rows = this.sql.exec(`SELECT id FROM comments WHERE id = ?`, id).toArray();
    if (!rows.length) return { ok: false, version: this.version() };
    this.sql.exec(`DELETE FROM comments WHERE id = ?`, id);
    const leftover = this.sql.exec(`SELECT id FROM comments WHERE id = ?`, id).toArray();
    return { ok: leftover.length === 0, version: leftover.length === 0 ? this.bumpVersion() : this.version() };
  }

  rememberSlug(slug) {
    this.sql.exec(`INSERT OR IGNORE INTO slugs (slug) VALUES (?)`, slug);
    return this.sql.exec(`SELECT slug FROM slugs WHERE slug = ?`, slug).toArray().length > 0;
  }

  pageSlugs(after = "", limit = ADMIN_SLUG_PAGE) {
    const cap = clampLimit(limit, ADMIN_SLUG_PAGE);
    const rows = this.sql
      .exec(`SELECT slug FROM slugs WHERE slug > ? ORDER BY slug ASC LIMIT ?`, after || "", cap + 1)
      .toArray();
    const hasMore = rows.length > cap;
    const slugs = rows.slice(0, cap).map((row) => row.slug);
    return { slugs, nextSlug: hasMore ? slugs[slugs.length - 1] : null };
  }
}

export class SqliteRateStore {
  constructor(sql) {
    this.sql = sql;
  }

  _prune(table, windowSec, now) {
    if (table === "hits") this.sql.exec(`DELETE FROM hits WHERE stamp < ?`, now - windowSec);
    else if (table === "authfails") this.sql.exec(`DELETE FROM authfails WHERE stamp < ?`, now - windowSec);
  }

  hit(ip, now = Date.now() / 1000) {
    const key = ip || "unknown";
    this._prune("hits", RATE_WINDOW_SEC, now);
    const stamps = this.sql
      .exec(`SELECT stamp FROM hits WHERE ip = ? ORDER BY stamp ASC`, key)
      .toArray()
      .map((row) => row.stamp);
    if (stamps.length && now - stamps[stamps.length - 1] < RATE_MIN_INTERVAL_SEC) {
      return { ok: false, status: 429, message: "Please wait a moment before commenting again." };
    }
    if (stamps.length >= RATE_MAX) {
      return { ok: false, status: 429, message: "Too many comments. Try again later." };
    }
    this.sql.exec(`INSERT INTO hits (ip, stamp) VALUES (?, ?)`, key, now);
    return { ok: true };
  }

  authAttempt(ip, valid, now = Date.now() / 1000) {
    const key = ip || "unknown";
    this._prune("authfails", AUTH_FAIL_WINDOW_SEC, now);
    const stamps = this.sql.exec(`SELECT stamp FROM authfails WHERE ip = ?`, key).toArray();
    if (stamps.length >= AUTH_FAIL_MAX) {
      return { ok: false, blocked: true, status: 429 };
    }
    if (!valid) {
      this.sql.exec(`INSERT INTO authfails (ip, stamp) VALUES (?, ?)`, key, now);
      return { ok: false, blocked: false, status: 401 };
    }
    return { ok: true, blocked: false };
  }
}

async function verifyTurnstile(token, ip, env) {
  const secret = String(env.TURNSTILE_SECRET || "").trim();
  const value = String(token || "").trim();
  if (!secret || !value) {
    const err = new Error("Spam check failed.");
    err.status = 400;
    throw err;
  }
  const body = new URLSearchParams();
  body.set("secret", secret);
  body.set("response", value);
  if (ip && ip !== "unknown") body.set("remoteip", ip);
  const res = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (!data.success) {
    const err = new Error("Spam check failed.");
    err.status = 400;
    throw err;
  }
}

function requireRates(env) {
  if (env.RATES) return;
  const err = new Error("Rate limiter unavailable.");
  err.status = 503;
  throw err;
}

async function rateCall(env, path, ip, extra = {}) {
  requireRates(env);
  const key = rateKey(ip);
  const url = new URL("https://do" + path);
  url.searchParams.set("ip", key);
  for (const [name, value] of Object.entries(extra)) url.searchParams.set(name, String(value));
  const res = await env.RATES.get(env.RATES.idFromName(key)).fetch(new Request(url.toString(), { method: "POST" }));
  return res.json();
}

async function requireAdmin(request, env, ip) {
  const extra = adminHeaders();
  const expected = String(env.COMMENTS_ADMIN_TOKEN || "").trim();
  if (!expected) {
    const err = new Error("Moderation is not configured.");
    err.status = 503;
    err.extra = extra;
    throw err;
  }
  try {
    requireRates(env);
  } catch (err) {
    err.extra = extra;
    throw err;
  }
  const provided = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
  const valid = Boolean(provided) && (await timingSafeEqual(provided, expected));
  const gate = await rateCall(env, "/auth", ip, { valid: valid ? "1" : "0" });
  if (gate.blocked) {
    const err = new Error("Too many failed sign-in attempts. Try again later.");
    err.status = 429;
    err.extra = extra;
    throw err;
  }
  if (!valid) {
    const err = new Error("Unauthorized.");
    err.status = 401;
    err.extra = extra;
    throw err;
  }
}

function threadStub(env, name) {
  if (!env.THREADS) {
    const err = new Error("Comments storage is unavailable.");
    err.status = 503;
    throw err;
  }
  return env.THREADS.get(env.THREADS.idFromName(name));
}

function cacheStore(env) {
  if (env.__cache) return env.__cache;
  if (typeof caches !== "undefined" && caches.default) return caches.default;
  return null;
}

function cacheUrl(kind, slug, extra = "") {
  return "https://comments-cache/" + kind + "/" + encodeURIComponent(slug) + extra;
}

function listCacheUrl(slug, after, version) {
  return cacheUrl("list", slug, "?after=" + encodeURIComponent(after || "") + "&v=" + encodeURIComponent(String(version)));
}

async function cacheMatch(env, url) {
  const cache = cacheStore(env);
  if (!cache) return null;
  return cache.match(new Request(url));
}

async function cachePut(env, url, response) {
  const cache = cacheStore(env);
  if (!cache) return;
  await cache.put(new Request(url), response);
}

async function readCachedVersion(env, slug) {
  const hit = await cacheMatch(env, cacheUrl("ver", slug));
  if (!hit) return "";
  return String(await hit.text()).trim();
}

async function writeCachedVersion(env, slug, version) {
  const current = Number(await readCachedVersion(env, slug));
  const next = Number(version);
  if (Number.isFinite(current) && Number.isFinite(next) && next < current) return false;
  await cachePut(
    env,
    cacheUrl("ver", slug),
    new Response(String(version), { headers: { "cache-control": "max-age=" + LIST_CACHE_TTL_SEC } })
  );
  return true;
}

function publicListHeaders(state) {
  return {
    "cache-control": "no-store",
    "x-comments-cache": state,
  };
}

async function rememberDirectory(env, slug) {
  const res = await threadStub(env, DIR_NAME).fetch(
    new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug }) })
  );
  const data = await res.json().catch(() => ({}));
  return Boolean(res.ok && data.ok);
}

function schedule(ctx, work) {
  const pending = Promise.resolve().then(work);
  if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(pending);
  return pending;
}

async function insertComment(env, row, ctx) {
  const res = await threadStub(env, row.slug).fetch(
    new Request("https://do/insert", { method: "POST", body: JSON.stringify(row) })
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok || !data.comment) {
    const err = new Error(data.error || "Could not save comment.");
    err.status = res.status && res.status >= 400 ? res.status : 500;
    throw err;
  }
  await writeCachedVersion(env, row.slug, data.version);
  schedule(ctx, async () => {
    try {
      if (await rememberDirectory(env, row.slug)) return;
      console.log("comments directory remember retry", row.slug);
      await rememberDirectory(env, row.slug);
    } catch (err) {
      console.log("comments directory remember failed", row.slug, err && err.message);
    }
  });
  return data.comment;
}

async function listThread(env, slug, { includeHidden = false, limit, after } = {}) {
  const params = new URLSearchParams();
  if (includeHidden) params.set("hidden", "1");
  if (limit) params.set("limit", String(limit));
  if (after) params.set("after", after);
  const res = await threadStub(env, slug).fetch(new Request("https://do/list?" + params.toString()));
  return res.json();
}

async function findExistingByRequestId(env, slug, requestId) {
  if (!requestId) return null;
  const res = await threadStub(env, slug).fetch(
    new Request("https://do/by-request-id?id=" + encodeURIComponent(requestId))
  );
  const data = await res.json().catch(() => ({}));
  return data.comment || null;
}

async function listAdminDirectory(env, url) {
  const after = url.searchParams.get("after") || "";
  const limit = clampLimit(url.searchParams.get("limit"), ADMIN_SLUG_PAGE);
  const dir = await threadStub(env, DIR_NAME).fetch(
    new Request("https://do/slugs?after=" + encodeURIComponent(after) + "&limit=" + limit)
  );
  const page = await dir.json();
  const slugs = page.slugs || [];
  const comments = [];
  const nextBySlug = {};
  for (let i = 0; i < slugs.length; i += ADMIN_PARALLEL) {
    const batch = slugs.slice(i, i + ADMIN_PARALLEL);
    const parts = await Promise.all(batch.map((item) => listThread(env, item, { includeHidden: true })));
    parts.forEach((part, index) => {
      comments.push(...(part.comments || []));
      if (part.next) nextBySlug[batch[index]] = part.next;
    });
  }
  comments.sort((a, b) => {
    const time = String(b.createdAt).localeCompare(String(a.createdAt));
    return time !== 0 ? time : String(b.id).localeCompare(String(a.id));
  });
  return { comments, nextSlug: page.nextSlug || null, nextBySlug };
}

async function publicList(request, env, slug, url) {
  const after = url.searchParams.get("after") || "";
  const cachedVersion = await readCachedVersion(env, slug);
  if (cachedVersion !== "") {
    const hit = await cacheMatch(env, listCacheUrl(slug, after, cachedVersion));
    if (hit) {
      return json(request, env, 200, await hit.json(), publicListHeaders("hit"));
    }
  }
  const data = await listThread(env, slug, { after });
  const extra = publicListHeaders("miss");
  const response = json(request, env, 200, data, extra);
  if (await writeCachedVersion(env, slug, data.version ?? 0)) {
    await cachePut(env, listCacheUrl(slug, after, data.version ?? 0), response.clone());
  }
  return response;
}

export async function handleRequest(request, env, ctx = {}) {
  if (request.method === "OPTIONS") return json(request, env, 204, null);

  const url = new URL(request.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const slug = clean(url.searchParams.get("slug") || "", 80);
  const ip = clientIp(request);

  try {
    if (request.method === "GET" && parts.length === 1 && parts[0] === "comments") {
      if (url.searchParams.get("all") === "1") {
        await requireAdmin(request, env, ip);
        return json(request, env, 200, await listAdminDirectory(env, url), adminHeaders());
      }
      if (url.searchParams.get("hidden") === "1") {
        await requireAdmin(request, env, ip);
        await assertKnownSlug(env, slug);
        return json(
          request,
          env,
          200,
          await listThread(env, slug, {
            includeHidden: true,
            limit: url.searchParams.get("limit"),
            after: url.searchParams.get("after") || "",
          }),
          adminHeaders()
        );
      }
      await assertKnownSlug(env, slug);
      return await publicList(request, env, slug, url);
    }

    if (request.method === "POST" && parts.length === 1 && parts[0] === "comments") {
      const body = await request.json().catch(() => ({}));
      await verifyTurnstile(body.turnstileToken || request.headers.get("X-Turnstile-Token"), ip, env);
      if (clean(body[HONEYPOT_FIELD], 200)) {
        return json(request, env, 201, { ok: true, ignored: true });
      }
      const nextSlug = clean(body.slug, 80);
      const name = clean(body.name, MAX_NAME);
      const text = clean(body.body, MAX_BODY);
      const requestId = String(body.requestId || body.request_id || "").trim();
      await assertKnownSlug(env, nextSlug);
      if (requestId && !REQUEST_ID_RE.test(requestId)) {
        const err = new Error("Invalid request id.");
        err.status = 400;
        throw err;
      }
      if (name.length < MIN_NAME) {
        const err = new Error("Name is required.");
        err.status = 400;
        throw err;
      }
      if (text.length < MIN_BODY) {
        const err = new Error("Comment is too short.");
        err.status = 400;
        throw err;
      }
      if (requestId) {
        const replayed = await findExistingByRequestId(env, nextSlug, requestId);
        if (replayed) return json(request, env, 201, { comment: replayed, replayed: true });
      }
      const limited = await rateCall(env, "/hit", ip);
      if (!limited.ok) {
        const err = new Error(limited.message);
        err.status = limited.status;
        throw err;
      }
      const row = {
        id: `${nextSlug}~${crypto.randomUUID().replace(/-/g, "")}`,
        slug: nextSlug,
        name,
        body: text,
        createdAt: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
        hidden: false,
        requestId: requestId || crypto.randomUUID(),
      };
      return json(request, env, 201, { comment: await insertComment(env, row, ctx) });
    }

    if (request.method === "POST" && parts.length === 3 && parts[0] === "comments" && (parts[2] === "hide" || parts[2] === "unhide")) {
      await requireAdmin(request, env, ip);
      const hidden = parts[2] === "hide";
      const parsed = parseThreadId(parts[1]);
      await assertKnownSlug(env, parsed.slug || slug);
      const res = await threadStub(env, parsed.slug || slug).fetch(
        new Request(`https://do/hide?id=${encodeURIComponent(parts[1])}&hidden=${hidden ? "1" : "0"}`, {
          method: "POST",
        })
      );
      const payload = await res.json();
      const updated = payload.comment;
      if (!updated) {
        const err = new Error("Comment not found.");
        err.status = 404;
        err.extra = adminHeaders();
        throw err;
      }
      await writeCachedVersion(env, updated.slug, payload.version);
      return json(request, env, 200, { comment: updated }, adminHeaders());
    }

    if (request.method === "DELETE" && parts.length === 2 && parts[0] === "comments") {
      await requireAdmin(request, env, ip);
      const parsed = parseThreadId(parts[1]);
      await assertKnownSlug(env, parsed.slug || slug);
      const target = parsed.slug || slug;
      const res = await threadStub(env, target).fetch(
        new Request(`https://do/delete?id=${encodeURIComponent(parts[1])}`, { method: "POST" })
      );
      const payload = await res.json();
      if (!payload.ok) {
        const err = new Error("Comment not found.");
        err.status = 404;
        err.extra = adminHeaders();
        throw err;
      }
      await writeCachedVersion(env, target, payload.version);
      return json(request, env, 200, { ok: true }, adminHeaders());
    }

    const err = new Error("Not found.");
    err.status = 404;
    throw err;
  } catch (err) {
    return json(request, env, err.status || 500, { error: err.message || "Server error." }, err.extra);
  }
}

export class CommentThread {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.ready = runInit(ctx, () => migrateThreadSchema(this.ctx.storage.sql));
  }

  store() {
    return new SqliteThreadStore(this.ctx.storage.sql);
  }

  async fetch(request) {
    await this.ready;
    const url = new URL(request.url);
    const store = this.store();
    if (url.pathname === "/list") {
      return Response.json(
        store.list("", {
          includeHidden: url.searchParams.get("hidden") === "1",
          limit: url.searchParams.get("limit"),
          after: url.searchParams.get("after") || "",
        })
      );
    }
    if (url.pathname === "/by-request-id") {
      return Response.json({ comment: store.findByRequestId(url.searchParams.get("id")) });
    }
    if (url.pathname === "/insert") {
      const row = await request.json();
      const result = store.insert(row);
      return Response.json({ ok: Boolean(result.comment), ...result });
    }
    if (url.pathname === "/hide") {
      return Response.json(store.hide(url.searchParams.get("id"), url.searchParams.get("hidden") === "1"));
    }
    if (url.pathname === "/delete") {
      return Response.json(store.delete(url.searchParams.get("id")));
    }
    if (url.pathname === "/slugs") {
      return Response.json(
        store.pageSlugs(url.searchParams.get("after") || "", url.searchParams.get("limit"))
      );
    }
    if (url.pathname === "/remember") {
      const body = await request.json();
      return Response.json({ ok: store.rememberSlug(body.slug) });
    }
    return new Response("no", { status: 404 });
  }
}

export class RateBucket {
  constructor(ctx) {
    this.ctx = ctx;
    this.ready = runInit(ctx, () => initRateSchema(this.ctx.storage.sql));
  }

  store() {
    return new SqliteRateStore(this.ctx.storage.sql);
  }

  async fetch(request) {
    await this.ready;
    const url = new URL(request.url);
    const ip = url.searchParams.get("ip") || "unknown";
    if (url.pathname === "/hit") return Response.json(this.store().hit(ip));
    if (url.pathname === "/auth") {
      return Response.json(this.store().authAttempt(ip, url.searchParams.get("valid") === "1"));
    }
    return new Response("no", { status: 404 });
  }
}

export default {
  fetch(request, env, ctx) {
    return handleRequest(request, env, ctx);
  },
};
