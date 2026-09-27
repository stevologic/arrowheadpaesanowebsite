/**
 * Cloudflare Worker + KV for Arrowhead Paesano narrative comments.
 *
 * Secrets (wrangler secret put …) — never commit real values:
 *   COMMENTS_ADMIN_TOKEN
 *   TURNSTILE_SECRET
 *
 * Bindings: COMMENTS (KV namespace)
 *
 * Contract matches tools/comments/service.py so local tests stay meaningful.
 */
const SLUG_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const HONEYPOT_FIELD = "company";
const MAX_NAME = 40;
const MIN_NAME = 2;
const MAX_BODY = 1000;
const MIN_BODY = 2;
const RATE_WINDOW_SEC = 10 * 60;
const RATE_MAX = 5;
const RATE_MIN_INTERVAL_SEC = 20;

const ALLOWED_ORIGINS = [
  "https://arrowheadpaesano.com",
  "https://www.arrowheadpaesano.com",
  "http://localhost:1515",
  "http://127.0.0.1:1515",
];

function corsOrigin(request) {
  const origin = request.headers.get("Origin") || "";
  if (ALLOWED_ORIGINS.includes(origin)) return origin;
  if (/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(origin)) return origin;
  return "";
}

function json(request, status, payload) {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type, X-Turnstile-Token",
  };
  const origin = corsOrigin(request);
  if (origin) {
    headers["access-control-allow-origin"] = origin;
    headers.vary = "Origin";
  }
  return new Response(payload === null ? null : JSON.stringify(payload), { status, headers });
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

function requireAdmin(request, env) {
  const expected = String(env.COMMENTS_ADMIN_TOKEN || "").trim();
  if (!expected) {
    const err = new Error("Moderation is not configured.");
    err.status = 503;
    throw err;
  }
  const provided = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!provided || !timingSafeEqual(provided, expected)) {
    const err = new Error("Unauthorized.");
    err.status = 401;
    throw err;
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

async function enforceRateLimit(env, ip) {
  const key = `rate:${ip || "unknown"}`;
  const now = Date.now() / 1000;
  const hits = ((await env.COMMENTS.get(key, { type: "json" })) || []).filter(
    (stamp) => now - stamp < RATE_WINDOW_SEC
  );
  if (hits.length && now - hits[hits.length - 1] < RATE_MIN_INTERVAL_SEC) {
    const err = new Error("Please wait a moment before commenting again.");
    err.status = 429;
    throw err;
  }
  if (hits.length >= RATE_MAX) {
    const err = new Error("Too many comments. Try again later.");
    err.status = 429;
    throw err;
  }
  hits.push(now);
  await env.COMMENTS.put(key, JSON.stringify(hits), { expirationTtl: RATE_WINDOW_SEC });
}

async function readThread(env, slug) {
  return (await env.COMMENTS.get(`thread:${slug}`, { type: "json" })) || [];
}

async function writeThread(env, slug, rows) {
  await env.COMMENTS.put(`thread:${slug}`, JSON.stringify(rows));
}

async function findComment(env, id) {
  const index = (await env.COMMENTS.get("index", { type: "json" })) || {};
  const slug = index[id];
  if (!slug) return { slug: null, rows: [], idx: -1 };
  const rows = await readThread(env, slug);
  return { slug, rows, idx: rows.findIndex((row) => row.id === id) };
}

async function rememberId(env, id, slug) {
  const index = (await env.COMMENTS.get("index", { type: "json" })) || {};
  index[id] = slug;
  await env.COMMENTS.put("index", JSON.stringify(index));
}

async function forgetId(env, id) {
  const index = (await env.COMMENTS.get("index", { type: "json" })) || {};
  delete index[id];
  await env.COMMENTS.put("index", JSON.stringify(index));
}

async function listAll(env) {
  const index = (await env.COMMENTS.get("index", { type: "json" })) || {};
  const slugs = [...new Set(Object.values(index))];
  const all = [];
  for (const slug of slugs) {
    all.push(...(await readThread(env, slug)));
  }
  all.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  return all;
}

async function handleRequest(request, env) {
  if (request.method === "OPTIONS") return json(request, 204, null);

  const url = new URL(request.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const slug = url.searchParams.get("slug") || "";

  try {
    if (request.method === "GET" && parts.length === 1 && parts[0] === "comments") {
      if (url.searchParams.get("all") === "1" || url.searchParams.get("hidden") === "1") {
        requireAdmin(request, env);
        const rows = url.searchParams.get("all") === "1" ? await listAll(env) : await readThread(env, slug);
        return json(request, 200, { comments: rows.map(publicComment) });
      }
      if (!SLUG_RE.test(slug)) {
        const err = new Error("Unknown story.");
        err.status = 400;
        throw err;
      }
      const rows = (await readThread(env, slug))
        .filter((row) => !row.hidden)
        .sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
      return json(request, 200, { comments: rows.map(publicComment) });
    }

    if (request.method === "POST" && parts.length === 1 && parts[0] === "comments") {
      const body = await request.json().catch(() => ({}));
      const ip = clientIp(request);
      await verifyTurnstile(body.turnstileToken || request.headers.get("X-Turnstile-Token"), ip, env);
      if (clean(body[HONEYPOT_FIELD], 200)) {
        return json(request, 201, { ok: true, ignored: true });
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
      await enforceRateLimit(env, ip);
      const row = {
        id: crypto.randomUUID().replace(/-/g, ""),
        slug: nextSlug,
        name,
        body: text,
        createdAt: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
        hidden: false,
      };
      const rows = await readThread(env, nextSlug);
      rows.push(row);
      await writeThread(env, nextSlug, rows);
      await rememberId(env, row.id, nextSlug);
      return json(request, 201, { comment: publicComment(row) });
    }

    if (request.method === "POST" && parts.length === 3 && parts[0] === "comments" && (parts[2] === "hide" || parts[2] === "unhide")) {
      requireAdmin(request, env);
      const found = await findComment(env, parts[1]);
      if (found.idx < 0) {
        const err = new Error("Comment not found.");
        err.status = 404;
        throw err;
      }
      found.rows[found.idx].hidden = parts[2] === "hide";
      await writeThread(env, found.slug, found.rows);
      return json(request, 200, { comment: publicComment(found.rows[found.idx]) });
    }

    if (request.method === "DELETE" && parts.length === 2 && parts[0] === "comments") {
      requireAdmin(request, env);
      const found = await findComment(env, parts[1]);
      if (found.idx < 0) {
        const err = new Error("Comment not found.");
        err.status = 404;
        throw err;
      }
      found.rows.splice(found.idx, 1);
      await writeThread(env, found.slug, found.rows);
      await forgetId(env, parts[1]);
      return json(request, 200, { ok: true });
    }

    const err = new Error("Not found.");
    err.status = 404;
    throw err;
  } catch (err) {
    return json(request, err.status || 500, { error: err.message || "Server error." });
  }
}

export default {
  fetch: handleRequest,
};
