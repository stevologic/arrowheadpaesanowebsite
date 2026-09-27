import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import {
  ADMIN_SLUG_PAGE,
  AUTH_FAIL_MAX,
  AUTH_FAIL_WINDOW_SEC,
  CommentThread,
  GET_RATE_MAX,
  HONEYPOT_FIELD,
  RATE_MAX,
  RateBucket,
  THREAD_LIST_LIMIT,
  corsOrigin,
  handleRequest,
  rateKey,
  resetSlugCache,
  timingSafeEqual,
} from "./worker.js";

const SLUG = "2026-09-26-1355";
const SLUG_B = "2026-09-25-1552";

function sqlShim() {
  const db = new DatabaseSync(":memory:");
  return {
    exec(query, ...binds) {
      const statements = String(query)
        .split(";")
        .map((item) => item.trim())
        .filter(Boolean);
      let rows = [];
      let offset = 0;
      for (const sql of statements) {
        const count = (sql.match(/\?/g) || []).length;
        const args = binds.slice(offset, offset + count);
        offset += count;
        if (/^\s*select/i.test(sql)) {
          rows = db.prepare(sql).all(...args);
        } else {
          db.prepare(sql).run(...args);
        }
      }
      return { toArray: () => rows };
    },
  };
}

function makeCtx() {
  return {
    storage: { sql: sqlShim() },
    blockConcurrencyWhile: async (fn) => fn(),
  };
}

function createDoEnv(overrides = {}) {
  const threads = new Map();
  const rates = new Map();
  const env = {
    COMMENTS_ADMIN_TOKEN: "secret-admin",
    TURNSTILE_SECRET: "turnstile-secret",
    COMMENTS_CORS_ORIGINS: "https://arrowheadpaesano.com,https://www.arrowheadpaesano.com",
    COMMENTS_DEV: "",
    COMMENTS_KNOWN_SLUGS: `${SLUG},${SLUG_B}`,
    THREADS: {
      idFromName(name) {
        return { name: String(name) };
      },
      get(id) {
        const key = id.name;
        if (!threads.has(key)) threads.set(key, new CommentThread(makeCtx(), env));
        return threads.get(key);
      },
    },
    RATES: {
      idFromName(name) {
        return { name: String(name) };
      },
      get(id) {
        const key = id.name;
        if (!rates.has(key)) rates.set(key, new RateBucket(makeCtx()));
        return rates.get(key);
      },
    },
    __threads: threads,
    __rates: rates,
    ...overrides,
  };
  return env;
}

function request(method, path, { body, headers, origin, env } = {}) {
  const hdrs = { ...(headers || {}) };
  if (origin) hdrs.Origin = origin;
  if (body && !hdrs["Content-Type"]) hdrs["Content-Type"] = "application/json";
  return handleRequest(
    new Request("https://comments.example" + path, {
      method,
      headers: hdrs,
      body: body ? JSON.stringify(body) : undefined,
    }),
    env || createDoEnv()
  );
}

async function withTurnstile(fn, success = true) {
  const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("turnstile")) {
      return Response.json({ success });
    }
    return previous(url);
  };
  try {
    return await fn();
  } finally {
    globalThis.fetch = previous;
  }
}

async function postComment(env, overrides = {}) {
  return withTurnstile(() =>
    request("POST", "/comments", {
      env,
      body: {
        slug: SLUG,
        name: "Travis",
        body: "The pass rush is the whole story.",
        [HONEYPOT_FIELD]: "",
        turnstileToken: "ok-token",
        ...overrides,
      },
    })
  );
}

test("honeypot is accepted but not stored", async () => {
  const env = createDoEnv();
  const res = await postComment(env, { [HONEYPOT_FIELD]: "http://spam.example" });
  assert.equal(res.status, 201);
  const data = await res.json();
  assert.equal(data.ignored, true);
  assert.equal(env.__threads.has(SLUG), false);
  const listed = await (await request("GET", `/comments?slug=${SLUG}`, { env })).json();
  assert.deepEqual(listed.comments, []);
});

test("rate limit rejects a burst from one IP", async () => {
  const env = createDoEnv();
  const bucket = env.RATES.get(env.RATES.idFromName("unknown"));
  await bucket.ready;
  const now = Date.now() / 1000;
  for (let i = 0; i < RATE_MAX; i += 1) {
    bucket.store().sql.exec(`INSERT INTO hits (ip, stamp) VALUES (?, ?)`, "unknown", now - 30 * (RATE_MAX - i));
  }
  const res = await postComment(env);
  assert.equal(res.status, 429);
});

test("Turnstile fail-closed on missing token and failed siteverify", async () => {
  const missing = await withTurnstile(() =>
    request("POST", "/comments", {
      body: { slug: SLUG, name: "A", body: "Hello there", turnstileToken: "" },
    })
  );
  assert.equal(missing.status, 400);

  const failed = await withTurnstile(
    () =>
      request("POST", "/comments", {
        body: { slug: SLUG, name: "A", body: "Hello there", turnstileToken: "bad" },
      }),
    false
  );
  assert.equal(failed.status, 400);

  const noSecret = await withTurnstile(() =>
    request("POST", "/comments", {
      env: createDoEnv({ TURNSTILE_SECRET: "" }),
      body: { slug: SLUG, name: "A", body: "Hello there", turnstileToken: "ok" },
    })
  );
  assert.equal(noSecret.status, 400);
});

test("admin auth returns 401, 503, and throttles bad tokens per IP for 5 minutes", async () => {
  const env = createDoEnv();
  const unauthorized = await request("POST", `/comments/${SLUG}~nope/hide`, { env });
  assert.equal(unauthorized.status, 401);
  assert.equal(unauthorized.headers.get("X-Robots-Tag"), "noindex, nofollow");

  const unconfigured = await request("DELETE", `/comments/${SLUG}~nope`, {
    env: createDoEnv({ COMMENTS_ADMIN_TOKEN: "" }),
    headers: { Authorization: "Bearer x" },
  });
  assert.equal(unconfigured.status, 503);

  const locked = createDoEnv();
  const key = "203.0.113.9";
  const bucket = locked.RATES.get(locked.RATES.idFromName(key));
  await bucket.ready;
  const now = Date.now() / 1000;
  for (let i = 0; i < AUTH_FAIL_MAX; i += 1) {
    bucket.store().sql.exec(`INSERT INTO authfails (ip, stamp) VALUES (?, ?)`, key, now - i);
  }
  const blocked = await request("POST", `/comments/${SLUG}~nope/hide`, {
    env: locked,
    headers: { Authorization: "Bearer secret-admin", "CF-Connecting-IP": key },
  });
  assert.equal(blocked.status, 429);
  assert.equal(AUTH_FAIL_WINDOW_SEC, 5 * 60);
});

test("admin routes fail closed when RATES is missing", async () => {
  const env = createDoEnv({ RATES: undefined });
  const res = await request("GET", "/comments?all=1", {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(res.status, 503);
});

test("unauthenticated GET ?all=1 is 401 and does not fan out", async () => {
  const env = createDoEnv();
  const res = await request("GET", "/comments?all=1", { env });
  assert.equal(res.status, 401);
  assert.equal(res.headers.get("X-Robots-Tag"), "noindex, nofollow");
  assert.equal(env.__threads.size, 0);
});

test("hide, unhide, and hard-DELETE go through CommentThread SQLite", async () => {
  const env = createDoEnv();
  const created = await (await postComment(env)).json();
  const id = created.comment.id;

  const hidden = await request("POST", `/comments/${id}/hide`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(hidden.status, 200);
  assert.equal((await hidden.json()).comment.hidden, true);
  const afterHide = await (await request("GET", `/comments?slug=${SLUG}`, { env })).json();
  assert.equal(afterHide.comments.length, 0);

  const shown = await request("POST", `/comments/${id}/unhide`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(shown.status, 200);
  assert.equal((await shown.json()).comment.hidden, false);
  const afterUnhide = await (await request("GET", `/comments?slug=${SLUG}`, { env })).json();
  assert.equal(afterUnhide.comments.length, 1);

  const deleted = await request("DELETE", `/comments/${id}`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(deleted.status, 200);
  const thread = env.__threads.get(SLUG);
  const leftover = thread.ctx.storage.sql.exec(`SELECT id, name, body FROM comments WHERE id = ?`, id).toArray();
  assert.deepEqual(leftover, []);
  const again = await request("DELETE", `/comments/${id}`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(again.status, 404);
});

test("THREADS/RATES routing plus directory fan-out stays paged and idempotent", async () => {
  const env = createDoEnv();
  const first = await (await postComment(env, { name: "Andy", body: "First chair at the table." })).json();
  env.__rates.clear();
  const second = await postComment(env, {
    slug: SLUG_B,
    name: "Patrick",
    body: "Second chair after the first.",
    headers: undefined,
  });
  const posted = await second.json();
  assert.equal(second.status, 201);

  const all = await request("GET", "/comments?all=1&limit=1", {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(all.status, 200);
  const page = await all.json();
  assert.ok(page.comments.length >= 1);
  assert.ok(page.nextSlug);
  assert.ok(page.comments.length <= ADMIN_SLUG_PAGE);

  const next = await request("GET", `/comments?all=1&after=${encodeURIComponent(page.nextSlug)}&limit=1`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  const pageTwo = await next.json();
  assert.ok(pageTwo.comments.length >= 1);
  const ids = [...page.comments, ...pageTwo.comments].map((row) => row.id);
  assert.ok(ids.includes(first.comment.id));
  assert.ok(ids.includes(posted.comment.id));

  const dir = env.__threads.get("__directory__");
  const before = dir.ctx.storage.sql.exec(`SELECT slug FROM slugs ORDER BY slug`).toArray();
  await dir.fetch(new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug: SLUG }) }));
  const after = dir.ctx.storage.sql.exec(`SELECT slug FROM slugs ORDER BY slug`).toArray();
  assert.deepEqual(before, after);
});

test("unknown slugs 404 without instantiating a thread DO", async () => {
  resetSlugCache();
  const env = createDoEnv();
  const listed = await request("GET", "/comments?slug=not-a-real-edition", { env });
  assert.equal(listed.status, 404);
  assert.equal(env.__threads.has("not-a-real-edition"), false);

  const posted = await postComment(env, { slug: "not-a-real-edition" });
  assert.equal(posted.status, 404);
  assert.equal(env.__threads.has("not-a-real-edition"), false);
});

test("thread list is limited and paginated", async () => {
  const env = createDoEnv();
  const thread = env.THREADS.get(env.THREADS.idFromName(SLUG));
  await thread.ready;
  for (let i = 0; i < 3; i += 1) {
    const row = {
      id: `${SLUG}~page${i}`,
      slug: SLUG,
      name: "Fan",
      body: `Take number ${i} for the table.`,
      createdAt: `2026-09-26T13:5${i}:00Z`,
    };
    assert.ok(thread.store().insert(row));
  }
  await thread.fetch(new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug: SLUG }) }));

  const first = await (await request("GET", `/comments?slug=${SLUG}&limit=2`, { env })).json();
  assert.equal(first.comments.length, 2);
  assert.ok(first.next);
  const second = await (await request("GET", `/comments?slug=${SLUG}&limit=2&after=${encodeURIComponent(first.next)}`, { env })).json();
  assert.equal(second.comments.length, 1);
  assert.equal(second.next, null);
  assert.ok(THREAD_LIST_LIMIT >= 2);
});

test("public GETs are rate-limited per IP", async () => {
  const env = createDoEnv();
  const key = "198.51.100.20";
  const bucket = env.RATES.get(env.RATES.idFromName(key));
  await bucket.ready;
  const now = Date.now() / 1000;
  for (let i = 0; i < GET_RATE_MAX; i += 1) {
    bucket.store().sql.exec(`INSERT INTO gethits (ip, stamp) VALUES (?, ?)`, key, now);
  }
  const res = await request("GET", `/comments?slug=${SLUG}`, {
    env,
    headers: { "CF-Connecting-IP": key },
  });
  assert.equal(res.status, 429);
});

test("IPv6 clients in the same /64 share a rate bucket", () => {
  assert.equal(rateKey("2001:db8:abcd:0012:0001:0000:0000:0001"), "2001:db8:abcd:12::/64");
  assert.equal(rateKey("2001:db8:abcd:12:ffff:ffff:ffff:ffff"), "2001:db8:abcd:12::/64");
  assert.equal(rateKey("203.0.113.9"), "203.0.113.9");
});

test("timingSafeEqual hashes both sides so length does not short-circuit", async () => {
  assert.equal(await timingSafeEqual("secret-admin", "secret-admin"), true);
  assert.equal(await timingSafeEqual("short", "much-longer-token"), false);
  assert.equal(await timingSafeEqual("", "x"), false);
});

test("CORS allows only configured production origins unless COMMENTS_DEV is set", async () => {
  const env = createDoEnv();
  const prod = new Request("https://comments.example/comments?slug=" + SLUG, {
    headers: { Origin: "https://arrowheadpaesano.com" },
  });
  assert.equal(corsOrigin(prod, env), "https://arrowheadpaesano.com");

  const local = new Request("https://comments.example/comments?slug=" + SLUG, {
    headers: { Origin: "http://127.0.0.1:1515" },
  });
  assert.equal(corsOrigin(local, env), "");
  assert.equal(corsOrigin(local, { ...env, COMMENTS_DEV: "1" }), "http://127.0.0.1:1515");

  const other = new Request("https://comments.example/comments?slug=" + SLUG, {
    headers: { Origin: "https://evil.example" },
  });
  assert.equal(corsOrigin(other, env), "");

  const res = await request("GET", `/comments?slug=${SLUG}`, {
    env,
    origin: "https://arrowheadpaesano.com",
  });
  assert.equal(res.headers.get("access-control-allow-origin"), "https://arrowheadpaesano.com");

  const denied = await request("GET", `/comments?slug=${SLUG}`, {
    env,
    origin: "http://localhost:1515",
  });
  assert.equal(denied.headers.get("access-control-allow-origin"), null);
});
