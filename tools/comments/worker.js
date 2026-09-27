/**
 * Cloudflare Worker + SQLite Durable Objects for narrative comments.
 *
 * $0 on Workers Free (SQLite-backed DOs only). One CommentThread DO per
 * known story slug so post/hide/delete are atomic; RateBucket DO per IPv4
 * or IPv6 /64 for post/GET limits and bad-admin-token lockout.
 *
 * There is no hot global key per comment. The thread DO is the source of
 * truth; the directory DO is an idempotent slug index used only so
 * /moderate/ can page a handful of threads in bounded parallel.
 *
 * Secrets (never commit):
 *   wrangler secret put COMMENTS_ADMIN_TOKEN
 *   wrangler secret put TURNSTILE_SECRET
 *
 * Vars:
 *   COMMENTS_CORS_ORIGINS  comma-separated production origins
 *   COMMENTS_SLUGS_URL     Hugo-emitted allowlist (comments-slugs.json)
 *   COMMENTS_DEV           "1" to allow localhost CORS
 */
export const HONEYPOT_FIELD = "nrt_hp_x7";
export const SLUG_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const MAX_NAME = 40;
const MIN_NAME = 2;
const MAX_BODY = 1000;
const MIN_BODY = 2;
export const RATE_WINDOW_SEC = 10 * 60;
export const RATE_MAX = 5;
export const RATE_MIN_INTERVAL_SEC = 20;
export const GET_RATE_WINDOW_SEC = 60;
export const GET_RATE_MAX = 60;
export const AUTH_FAIL_MAX = 8;
export const AUTH_FAIL_WINDOW_SEC = 5 * 60;
export const THREAD_LIST_LIMIT = 50;
export const ADMIN_SLUG_PAGE = 8;
export const ADMIN_PARALLEL = 4;
const DIR_NAME = "__directory__";
const DEFAULT_SLUGS_URL = "https://arrowheadpaesano.com/comments-slugs.json";
const SLUGS_TTL_MS = 5 * 60 * 1000;

let slugCache = { at: 0, slugs: null };

export function resetSlugCache() {
  slugCache = { at: 0, slugs: null };
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

export async function knownSlugs(env) {
  if (env.__knownSlugs instanceof Set) return env.__knownSlugs;
  if (Array.isArray(env.__knownSlugs)) return new Set(env.__knownSlugs);
  const bundled = String(env.COMMENTS_KNOWN_SLUGS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  if (bundled.length) return new Set(bundled);
  if (slugCache.slugs && Date.now() - slugCache.at < SLUGS_TTL_MS) return slugCache.slugs;
  const url = String(env.COMMENTS_SLUGS_URL || "").trim() || DEFAULT_SLUGS_URL;
  const res = await fetch(url);
  if (!res.ok) {
    const err = new Error("Story list is unavailable.");
    err.status = 503;
    throw err;
  }
  const slugs = parseKnownSlugs(await res.json().catch(() => []));
  slugCache = { at: Date.now(), slugs };
  return slugs;
}

async function assertKnownSlug(env, slug) {
  if (!SLUG_RE.test(slug) || slug === DIR_NAME) throw unknownStory();
  const known = await knownSlugs(env);
  if (!known.has(slug)) throw unknownStory();
}

export function initThreadSchema(sql) {
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
  sql.exec(`CREATE INDEX IF NOT EXISTS comments_slug_created ON comments (slug, createdAt, id)`);
}

export function initRateSchema(sql) {
  sql.exec(`CREATE TABLE IF NOT EXISTS hits (
    ip TEXT NOT NULL,
    stamp REAL NOT NULL
  )`);
  sql.exec(`CREATE TABLE IF NOT EXISTS gethits (
    ip TEXT NOT NULL,
    stamp REAL NOT NULL
  )`);
  sql.exec(`CREATE TABLE IF NOT EXISTS authfails (
    ip TEXT NOT NULL,
    stamp REAL NOT NULL
  )`);
  sql.exec(`CREATE INDEX IF NOT EXISTS hits_ip_stamp ON hits (ip, stamp)`);
  sql.exec(`CREATE INDEX IF NOT EXISTS gethits_ip_stamp ON gethits (ip, stamp)`);
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

export class SqliteThreadStore {
  constructor(sql) {
    this.sql = sql;
  }

  list(slug, { includeHidden = false, limit = THREAD_LIST_LIMIT, after = "" } = {}) {
    const cap = clampLimit(limit, THREAD_LIST_LIMIT);
    let afterCreated = "";
    let afterId = "";
    if (after) {
      const cut = String(after).indexOf("|");
      if (cut > 0) {
        afterCreated = after.slice(0, cut);
        afterId = after.slice(cut + 1);
      }
    }
    const rows = this.sql
      .exec(
        `SELECT id, slug, name, body, createdAt, hidden FROM comments
         WHERE (? = '' OR slug = ?)
           AND (? = 1 OR hidden = 0)
           AND (? = '' OR createdAt > ? OR (createdAt = ? AND id > ?))
         ORDER BY createdAt ASC, id ASC
         LIMIT ?`,
        slug || "",
        slug || "",
        includeHidden ? 1 : 0,
        afterCreated,
        afterCreated,
        afterCreated,
        afterId,
        cap + 1
      )
      .toArray();
    const hasMore = rows.length > cap;
    const page = rows.slice(0, cap);
    const last = page[page.length - 1];
    return {
      comments: page.map((row) => publicComment({ ...row, hidden: Boolean(row.hidden) })),
      next: hasMore && last ? `${last.createdAt}|${last.id}` : null,
    };
  }

  insert(row) {
    this.sql.exec(
      `INSERT INTO comments (id, slug, name, body, createdAt, hidden)
       VALUES (?, ?, ?, ?, ?, 0)`,
      row.id,
      row.slug,
      row.name,
      row.body,
      row.createdAt
    );
    const saved = this.sql.exec(`SELECT id FROM comments WHERE id = ?`, row.id).toArray();
    if (!saved.length) return null;
    return publicComment(row);
  }

  hide(id, hidden) {
    const rows = this.sql
      .exec(`SELECT id, slug, name, body, createdAt, hidden FROM comments WHERE id = ?`, id)
      .toArray();
    if (!rows.length) return null;
    this.sql.exec(`UPDATE comments SET hidden = ? WHERE id = ?`, hidden ? 1 : 0, id);
    return publicComment({ ...rows[0], hidden: Boolean(hidden) });
  }

  delete(id) {
    const rows = this.sql.exec(`SELECT id FROM comments WHERE id = ?`, id).toArray();
    if (!rows.length) return false;
    this.sql.exec(`DELETE FROM comments WHERE id = ?`, id);
    const leftover = this.sql.exec(`SELECT id FROM comments WHERE id = ?`, id).toArray();
    return leftover.length === 0;
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
    else if (table === "gethits") this.sql.exec(`DELETE FROM gethits WHERE stamp < ?`, now - windowSec);
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

  getHit(ip, now = Date.now() / 1000) {
    const key = ip || "unknown";
    this._prune("gethits", GET_RATE_WINDOW_SEC, now);
    const stamps = this.sql.exec(`SELECT stamp FROM gethits WHERE ip = ?`, key).toArray();
    if (stamps.length >= GET_RATE_MAX) {
      return { ok: false, status: 429, message: "Too many requests. Try again later." };
    }
    this.sql.exec(`INSERT INTO gethits (ip, stamp) VALUES (?, ?)`, key, now);
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

async function rememberDirectory(env, slug) {
  const res = await threadStub(env, DIR_NAME).fetch(
    new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug }) })
  );
  const data = await res.json().catch(() => ({}));
  return Boolean(res.ok && data.ok);
}

async function insertComment(env, row) {
  const res = await threadStub(env, row.slug).fetch(
    new Request("https://do/insert", { method: "POST", body: JSON.stringify(row) })
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok || !data.comment) {
    const err = new Error(data.error || "Could not save comment.");
    err.status = res.status && res.status >= 400 ? res.status : 500;
    throw err;
  }
  if (!(await rememberDirectory(env, row.slug))) {
    await rememberDirectory(env, row.slug);
  }
  return data.comment;
}

async function listThread(env, slug, { includeHidden = false, limit, after } = {}) {
  const params = new URLSearchParams();
  if (includeHidden) params.set("hidden", "1");
  if (limit) params.set("limit", String(limit));
  if (after) params.set("after", after);
  const res = await threadStub(env, slug).fetch(new Request("https://do/list?" + params.toString()));
  const data = await res.json();
  if ((data.comments || []).length) {
    await rememberDirectory(env, slug);
  }
  return data;
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
  for (let i = 0; i < slugs.length; i += ADMIN_PARALLEL) {
    const batch = slugs.slice(i, i + ADMIN_PARALLEL);
    const parts = await Promise.all(
      batch.map(async (item) => {
        const res = await threadStub(env, item).fetch(new Request("https://do/list?hidden=1"));
        return (await res.json()).comments || [];
      })
    );
    for (const part of parts) comments.push(...part);
  }
  comments.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  return { comments, nextSlug: page.nextSlug || null };
}

export async function handleRequest(request, env) {
  if (request.method === "OPTIONS") return json(request, env, 204, null);

  const url = new URL(request.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const slug = clean(url.searchParams.get("slug") || "", 80);
  const ip = clientIp(request);

  try {
    if (request.method === "GET" && parts.length === 1 && parts[0] === "comments") {
      if (url.searchParams.get("all") === "1" || url.searchParams.get("hidden") === "1") {
        await requireAdmin(request, env, ip);
        const payload = await listAdminDirectory(env, url);
        return json(request, env, 200, payload, adminHeaders());
      }
      const limited = await rateCall(env, "/get", ip);
      if (!limited.ok) {
        const err = new Error(limited.message);
        err.status = limited.status;
        throw err;
      }
      await assertKnownSlug(env, slug);
      return json(
        request,
        env,
        200,
        await listThread(env, slug, {
          limit: url.searchParams.get("limit"),
          after: url.searchParams.get("after") || "",
        })
      );
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
      await assertKnownSlug(env, nextSlug);
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
      };
      return json(request, env, 201, { comment: await insertComment(env, row) });
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
      const updated = (await res.json()).comment;
      if (!updated) {
        const err = new Error("Comment not found.");
        err.status = 404;
        err.extra = adminHeaders();
        throw err;
      }
      return json(request, env, 200, { comment: updated }, adminHeaders());
    }

    if (request.method === "DELETE" && parts.length === 2 && parts[0] === "comments") {
      await requireAdmin(request, env, ip);
      const parsed = parseThreadId(parts[1]);
      await assertKnownSlug(env, parsed.slug || slug);
      const res = await threadStub(env, parsed.slug || slug).fetch(
        new Request(`https://do/delete?id=${encodeURIComponent(parts[1])}`, { method: "POST" })
      );
      const ok = Boolean((await res.json()).ok);
      if (!ok) {
        const err = new Error("Comment not found.");
        err.status = 404;
        err.extra = adminHeaders();
        throw err;
      }
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
    this.ready = runInit(ctx, () => initThreadSchema(this.ctx.storage.sql));
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
    if (url.pathname === "/insert") {
      const row = await request.json();
      const comment = store.insert(row);
      return Response.json({ ok: Boolean(comment), comment });
    }
    if (url.pathname === "/hide") {
      const comment = store.hide(url.searchParams.get("id"), url.searchParams.get("hidden") === "1");
      return Response.json({ comment });
    }
    if (url.pathname === "/delete") {
      return Response.json({ ok: store.delete(url.searchParams.get("id")) });
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
    if (url.pathname === "/get") return Response.json(this.store().getHit(ip));
    if (url.pathname === "/auth") {
      return Response.json(this.store().authAttempt(ip, url.searchParams.get("valid") === "1"));
    }
    return new Response("no", { status: 404 });
  }
}

export default {
  fetch: handleRequest,
};
