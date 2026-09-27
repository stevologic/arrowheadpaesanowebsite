/**
 * Cloudflare Worker + SQLite Durable Objects for narrative comments.
 *
 * $0 on Workers Free (SQLite-backed DOs only). One CommentThread DO per
 * story slug so post/hide/delete are atomic; RateBucket DO per IP for
 * post rate limits and bad-admin-token throttling.
 *
 * Secrets (never commit):
 *   wrangler secret put COMMENTS_ADMIN_TOKEN
 *   wrangler secret put TURNSTILE_SECRET
 *
 * Vars:
 *   COMMENTS_CORS_ORIGINS  comma-separated production origins
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
export const AUTH_FAIL_MAX = 8;
export const AUTH_FAIL_WINDOW_SEC = 15 * 60;
const DIR_NAME = "__directory__";

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

function timingSafeEqual(a, b) {
  const left = String(a || "");
  const right = String(b || "");
  if (left.length !== right.length) return false;
  let out = 0;
  for (let i = 0; i < left.length; i += 1) out |= left.charCodeAt(i) ^ right.charCodeAt(i);
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

export class MemoryThreadStore {
  constructor() {
    this.rows = new Map();
    this.slugs = new Set();
  }

  list(slug, { includeHidden = false, includeDeleted = false } = {}) {
    return [...this.rows.values()]
      .filter((row) => (!slug || row.slug === slug) && (includeDeleted || !row.deleted) && (includeHidden || !row.hidden))
      .sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)))
      .map(publicComment);
  }

  listAdmin() {
    return [...this.rows.values()]
      .filter((row) => !row.deleted)
      .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
      .map(publicComment);
  }

  insert(row) {
    this.rows.set(row.id, { ...row, hidden: false, deleted: false });
    this.slugs.add(row.slug);
    return publicComment(row);
  }

  hide(id, hidden) {
    const row = this.rows.get(id);
    if (!row || row.deleted) return null;
    row.hidden = Boolean(hidden);
    return publicComment(row);
  }

  delete(id) {
    const row = this.rows.get(id);
    if (!row || row.deleted) return false;
    row.deleted = true;
    return true;
  }

  rememberSlug(slug) {
    this.slugs.add(slug);
  }
}

export class MemoryRateStore {
  constructor() {
    this.hits = new Map();
    this.authFails = new Map();
  }

  hit(ip, now = Date.now() / 1000) {
    const key = ip || "unknown";
    const stamps = (this.hits.get(key) || []).filter((stamp) => now - stamp < RATE_WINDOW_SEC);
    if (stamps.length && now - stamps[stamps.length - 1] < RATE_MIN_INTERVAL_SEC) {
      return { ok: false, status: 429, message: "Please wait a moment before commenting again." };
    }
    if (stamps.length >= RATE_MAX) {
      return { ok: false, status: 429, message: "Too many comments. Try again later." };
    }
    stamps.push(now);
    this.hits.set(key, stamps);
    return { ok: true };
  }

  authBlocked(ip, now = Date.now() / 1000) {
    const stamps = (this.authFails.get(ip || "unknown") || []).filter((stamp) => now - stamp < AUTH_FAIL_WINDOW_SEC);
    return stamps.length >= AUTH_FAIL_MAX;
  }

  authFail(ip, now = Date.now() / 1000) {
    const key = ip || "unknown";
    const stamps = (this.authFails.get(key) || []).filter((stamp) => now - stamp < AUTH_FAIL_WINDOW_SEC);
    stamps.push(now);
    this.authFails.set(key, stamps);
    return stamps.length;
  }
}

export function createMemoryEnv(overrides = {}) {
  const thread = overrides.threadStore || new MemoryThreadStore();
  const rates = overrides.rateStore || new MemoryRateStore();
  return {
    COMMENTS_ADMIN_TOKEN: "secret-admin",
    TURNSTILE_SECRET: "turnstile-secret",
    COMMENTS_CORS_ORIGINS: "https://arrowheadpaesano.com,https://www.arrowheadpaesano.com",
    COMMENTS_DEV: "",
    __memory: { thread, rates },
    ...overrides,
  };
}

class SqliteThreadStore {
  constructor(sql) {
    this.sql = sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS comments (
        id TEXT PRIMARY KEY,
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        body TEXT NOT NULL,
        createdAt TEXT NOT NULL,
        hidden INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS slugs (
        slug TEXT PRIMARY KEY
      );
    `);
  }

  list(slug, { includeHidden = false, includeDeleted = false } = {}) {
    const rows = this.sql
      .exec(
        `SELECT id, slug, name, body, createdAt, hidden, deleted FROM comments
         WHERE (? = '' OR slug = ?)
           AND (? = 1 OR deleted = 0)
           AND (? = 1 OR hidden = 0)
         ORDER BY createdAt ASC`,
        slug || "",
        slug || "",
        includeDeleted ? 1 : 0,
        includeHidden ? 1 : 0
      )
      .toArray();
    return rows.map((row) => publicComment({ ...row, hidden: Boolean(row.hidden) }));
  }

  listAdmin() {
    return this.sql
      .exec(
        `SELECT id, slug, name, body, createdAt, hidden FROM comments
         WHERE deleted = 0 ORDER BY createdAt DESC`
      )
      .toArray()
      .map((row) => publicComment({ ...row, hidden: Boolean(row.hidden) }));
  }

  insert(row) {
    this.sql.exec(
      `INSERT INTO comments (id, slug, name, body, createdAt, hidden, deleted)
       VALUES (?, ?, ?, ?, ?, 0, 0)`,
      row.id,
      row.slug,
      row.name,
      row.body,
      row.createdAt
    );
    this.rememberSlug(row.slug);
    return publicComment(row);
  }

  hide(id, hidden) {
    const rows = this.sql
      .exec(`SELECT id, slug, name, body, createdAt, hidden, deleted FROM comments WHERE id = ?`, id)
      .toArray();
    if (!rows.length || rows[0].deleted) return null;
    this.sql.exec(`UPDATE comments SET hidden = ? WHERE id = ?`, hidden ? 1 : 0, id);
    return publicComment({ ...rows[0], hidden: Boolean(hidden) });
  }

  delete(id) {
    const rows = this.sql.exec(`SELECT deleted FROM comments WHERE id = ?`, id).toArray();
    if (!rows.length || rows[0].deleted) return false;
    this.sql.exec(`UPDATE comments SET deleted = 1 WHERE id = ?`, id);
    return true;
  }

  rememberSlug(slug) {
    this.sql.exec(`INSERT OR IGNORE INTO slugs (slug) VALUES (?)`, slug);
  }

  allSlugs() {
    return this.sql.exec(`SELECT slug FROM slugs`).toArray().map((row) => row.slug);
  }
}

class SqliteRateStore {
  constructor(sql) {
    this.sql = sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS hits (
        ip TEXT NOT NULL,
        stamp REAL NOT NULL
      );
      CREATE TABLE IF NOT EXISTS authfails (
        ip TEXT NOT NULL,
        stamp REAL NOT NULL
      );
    `);
  }

  hit(ip, now = Date.now() / 1000) {
    const key = ip || "unknown";
    this.sql.exec(`DELETE FROM hits WHERE stamp < ?`, now - RATE_WINDOW_SEC);
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

  authBlocked(ip, now = Date.now() / 1000) {
    const key = ip || "unknown";
    const stamps = this.sql
      .exec(`SELECT stamp FROM authfails WHERE ip = ? AND stamp >= ?`, key, now - AUTH_FAIL_WINDOW_SEC)
      .toArray();
    return stamps.length >= AUTH_FAIL_MAX;
  }

  authFail(ip, now = Date.now() / 1000) {
    this.sql.exec(`INSERT INTO authfails (ip, stamp) VALUES (?, ?)`, ip || "unknown", now);
  }
}

function memoryStores(env) {
  if (!env.__memory) return null;
  return env.__memory;
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

async function isAuthBlocked(env, ip) {
  const memory = memoryStores(env);
  if (memory) return memory.rates.authBlocked(ip);
  if (!env.RATES) return false;
  const res = await env.RATES.get(env.RATES.idFromName(ip || "unknown")).fetch(
    new Request("https://do/auth-blocked?ip=" + encodeURIComponent(ip || "unknown"))
  );
  return Boolean((await res.json()).blocked);
}

async function recordAuthFail(env, ip) {
  const memory = memoryStores(env);
  if (memory) {
    memory.rates.authFail(ip);
    return;
  }
  if (!env.RATES) return;
  await env.RATES.get(env.RATES.idFromName(ip || "unknown")).fetch(
    new Request("https://do/auth-fail?ip=" + encodeURIComponent(ip || "unknown"), { method: "POST" })
  );
}

async function requireAdmin(request, env, ip) {
  const extra = adminHeaders();
  if (await isAuthBlocked(env, ip)) {
    const err = new Error("Too many failed sign-in attempts. Try again later.");
    err.status = 429;
    err.extra = extra;
    throw err;
  }
  const expected = String(env.COMMENTS_ADMIN_TOKEN || "").trim();
  if (!expected) {
    const err = new Error("Moderation is not configured.");
    err.status = 503;
    err.extra = extra;
    throw err;
  }
  const provided = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!provided || !timingSafeEqual(provided, expected)) {
    await recordAuthFail(env, ip);
    const err = new Error("Unauthorized.");
    err.status = 401;
    err.extra = extra;
    throw err;
  }
}

async function rateHit(env, ip) {
  const memory = memoryStores(env);
  if (memory) return memory.rates.hit(ip);
  const res = await env.RATES.get(env.RATES.idFromName(ip || "unknown")).fetch(
    new Request("https://do/hit?ip=" + encodeURIComponent(ip || "unknown"), { method: "POST" })
  );
  return res.json();
}

function threadFromEnv(env) {
  return memoryStores(env)?.thread;
}

export async function handleRequest(request, env) {
  if (request.method === "OPTIONS") return json(request, env, 204, null);

  const url = new URL(request.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const slug = clean(url.searchParams.get("slug") || "", 80);
  const ip = clientIp(request);
  const store = threadFromEnv(env) || null;

  try {
    if (request.method === "GET" && parts.length === 1 && parts[0] === "comments") {
      if (url.searchParams.get("all") === "1" || url.searchParams.get("hidden") === "1") {
        await requireAdmin(request, env, ip);
        const rows = store
          ? url.searchParams.get("all") === "1"
            ? store.listAdmin()
            : store.list(slug, { includeHidden: true })
          : [];
        if (!store && env.THREADS) {
          const dir = await env.THREADS.get(env.THREADS.idFromName(DIR_NAME)).fetch(new Request("https://do/slugs"));
          const slugs = (await dir.json()).slugs || [];
          const all = [];
          for (const item of slugs) {
            const res = await env.THREADS.get(env.THREADS.idFromName(item)).fetch(
              new Request("https://do/list?hidden=1")
            );
            all.push(...((await res.json()).comments || []));
          }
          all.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
          return json(request, env, 200, { comments: all }, adminHeaders());
        }
        return json(request, env, 200, { comments: rows }, adminHeaders());
      }
      if (!SLUG_RE.test(slug)) {
        const err = new Error("Unknown story.");
        err.status = 400;
        throw err;
      }
      if (store) return json(request, env, 200, { comments: store.list(slug) });
      const res = await env.THREADS.get(env.THREADS.idFromName(slug)).fetch(new Request("https://do/list"));
      return json(request, env, 200, await res.json());
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
      if (!SLUG_RE.test(nextSlug)) {
        const err = new Error("Unknown story.");
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
      const limited = await rateHit(env, ip);
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
      if (store) {
        store.insert(row);
        return json(request, env, 201, { comment: publicComment(row) });
      }
      await env.THREADS.get(env.THREADS.idFromName(nextSlug)).fetch(
        new Request("https://do/insert", { method: "POST", body: JSON.stringify(row) })
      );
      await env.THREADS.get(env.THREADS.idFromName(DIR_NAME)).fetch(
        new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug: nextSlug }) })
      );
      return json(request, env, 201, { comment: publicComment(row) });
    }

    if (request.method === "POST" && parts.length === 3 && parts[0] === "comments" && (parts[2] === "hide" || parts[2] === "unhide")) {
      await requireAdmin(request, env, ip);
      const hidden = parts[2] === "hide";
      let updated = store ? store.hide(parts[1], hidden) : null;
      if (!store && env.THREADS) {
        const parsed = parseThreadId(parts[1]);
        const res = await env.THREADS.get(env.THREADS.idFromName(parsed.slug || slug)).fetch(
          new Request(`https://do/hide?id=${encodeURIComponent(parts[1])}&hidden=${hidden ? "1" : "0"}`, {
            method: "POST",
          })
        );
        updated = (await res.json()).comment;
      }
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
      let ok = store ? store.delete(parts[1]) : false;
      if (!store && env.THREADS) {
        const parsed = parseThreadId(parts[1]);
        const res = await env.THREADS.get(env.THREADS.idFromName(parsed.slug || slug)).fetch(
          new Request(`https://do/delete?id=${encodeURIComponent(parts[1])}`, { method: "POST" })
        );
        ok = Boolean((await res.json()).ok);
      }
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
  }

  store() {
    return new SqliteThreadStore(this.ctx.storage.sql);
  }

  async fetch(request) {
    const url = new URL(request.url);
    const store = this.store();
    if (url.pathname === "/list") {
      const includeHidden = url.searchParams.get("hidden") === "1";
      return Response.json({ comments: store.list("", { includeHidden }) });
    }
    if (url.pathname === "/insert") {
      const row = await request.json();
      store.insert(row);
      return Response.json({ ok: true });
    }
    if (url.pathname === "/hide") {
      const comment = store.hide(url.searchParams.get("id"), url.searchParams.get("hidden") === "1");
      return Response.json({ comment });
    }
    if (url.pathname === "/delete") {
      return Response.json({ ok: store.delete(url.searchParams.get("id")) });
    }
    if (url.pathname === "/slugs") {
      return Response.json({ slugs: store.allSlugs() });
    }
    if (url.pathname === "/remember") {
      const body = await request.json();
      store.rememberSlug(body.slug);
      return Response.json({ ok: true });
    }
    return new Response("no", { status: 404 });
  }
}

export class RateBucket {
  constructor(ctx) {
    this.ctx = ctx;
  }

  store() {
    return new SqliteRateStore(this.ctx.storage.sql);
  }

  async fetch(request) {
    const url = new URL(request.url);
    const ip = url.searchParams.get("ip") || "unknown";
    if (url.pathname === "/hit") return Response.json(this.store().hit(ip));
    if (url.pathname === "/auth-fail") {
      this.store().authFail(ip);
      return Response.json({ ok: true });
    }
    if (url.pathname === "/auth-blocked") {
      return Response.json({ blocked: this.store().authBlocked(ip) });
    }
    return new Response("no", { status: 404 });
  }
}

export default {
  fetch: handleRequest,
};
