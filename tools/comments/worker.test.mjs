import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import {
  ADMIN_SLUG_PAGE,
  AUTH_FAIL_MAX,
  AUTH_FAIL_WINDOW_SEC,
  CommentThread,
  HONEYPOT_FIELD,
  RATE_MAX,
  RateBucket,
  SCHEMA_VERSION,
  SLUGS_TTL_MS,
  THREAD_LIST_LIMIT,
  corsOrigin,
  handleRequest,
  expireSlugFetchBackoff,
  expireSlugList,
  migrateThreadSchema,
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
        if (/^\s*(select|pragma)/i.test(sql)) {
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

function createMemoryCache() {
  const map = new Map();
  return {
    async match(req) {
      const key = req instanceof Request ? req.url : String(req);
      const stored = map.get(key);
      return stored ? stored.clone() : undefined;
    },
    async put(req, res) {
      const key = req instanceof Request ? req.url : String(req);
      map.set(key, res.clone());
    },
    async delete(req) {
      const key = req instanceof Request ? req.url : String(req);
      return map.delete(key);
    },
  };
}

function createDoEnv(overrides = {}) {
  const threads = new Map();
  const rates = new Map();
  const pending = [];
  const env = {
    COMMENTS_ADMIN_TOKEN: "secret-admin",
    TURNSTILE_SECRET: "turnstile-secret",
    COMMENTS_CORS_ORIGINS: "https://arrowheadpaesano.com,https://www.arrowheadpaesano.com",
    COMMENTS_DEV: "",
    COMMENTS_KNOWN_SLUGS: `${SLUG},${SLUG_B}`,
    __cache: createMemoryCache(),
    __pending: pending,
    __doGets: [],
    __ctx: {
      waitUntil(promise) {
        pending.push(Promise.resolve(promise));
      },
    },
    THREADS: {
      idFromName(name) {
        return { name: String(name) };
      },
      get(id) {
        const key = id.name;
        if (!threads.has(key)) threads.set(key, new CommentThread(makeCtx(), env));
        const stub = threads.get(key);
        return {
          fetch(req) {
            env.__doGets.push({ name: key, path: new URL(req.url).pathname });
            return stub.fetch(req);
          },
        };
      },
    },
    RATES: {
      idFromName(name) {
        return { name: String(name) };
      },
      get(id) {
        const key = id.name;
        env.__rateGets.push(key);
        if (!rates.has(key)) rates.set(key, new RateBucket(makeCtx()));
        return rates.get(key);
      },
    },
    __threads: threads,
    __rates: rates,
    __rateGets: [],
    ...overrides,
  };
  return env;
}

async function flush(env) {
  const queued = (env.__pending || []).splice(0);
  await Promise.all(queued);
}

function request(method, path, { body, headers, origin, env, ctx } = {}) {
  const hdrs = { ...(headers || {}) };
  if (origin) hdrs.Origin = origin;
  if (body && !hdrs["Content-Type"]) hdrs["Content-Type"] = "application/json";
  const resolved = env || createDoEnv();
  return handleRequest(
    new Request("https://comments.example" + path, {
      method,
      headers: hdrs,
      body: body ? JSON.stringify(body) : undefined,
    }),
    resolved,
    ctx || resolved.__ctx || {}
  );
}

const usedTurnstile = new Set();
let turnstileCalls = 0;
let tokenSeq = 0;

function nextTurnstileToken() {
  tokenSeq += 1;
  return `ok-token-${tokenSeq}`;
}

function turnstileResponseFrom(init) {
  const raw = init && init.body;
  if (!raw) return "";
  if (typeof raw.get === "function") return String(raw.get("response") || "");
  return new URLSearchParams(String(raw)).get("response") || "";
}

async function withTurnstile(fn, success = true) {
  const previous = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if (String(url).includes("turnstile")) {
      turnstileCalls += 1;
      if (!success) return Response.json({ success: false });
      const token = turnstileResponseFrom(init);
      if (!token || usedTurnstile.has(token)) return Response.json({ success: false });
      usedTurnstile.add(token);
      return Response.json({ success: true });
    }
    return previous(url, init);
  };
  try {
    return await fn();
  } finally {
    globalThis.fetch = previous;
  }
}

function requestId(suffix = "1") {
  return `11111111-1111-4111-8111-11111111111${suffix}`.slice(0, 36);
}

async function postComment(env, overrides = {}) {
  return withTurnstile(() =>
    request("POST", "/comments", {
      env,
      body: {
        slug: SLUG,
        name: "Travis",
        body: "The pass rush is the whole story.",
        requestId: requestId("1"),
        [HONEYPOT_FIELD]: "",
        turnstileToken: nextTurnstileToken(),
        ...overrides,
      },
    })
  );
}

function listGets(env, slug = SLUG) {
  return (env.__doGets || []).filter((item) => item.name === slug && item.path === "/list");
}

function dirGets(env) {
  return (env.__doGets || []).filter((item) => item.name === "__directory__");
}

test("honeypot is accepted but not stored", async () => {
  const env = createDoEnv();
  const res = await postComment(env, { [HONEYPOT_FIELD]: "http://spam.example", requestId: "" });
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
  const first = await (await postComment(env, { name: "Andy", body: "First chair at the table.", requestId: requestId("2") })).json();
  await flush(env);
  env.__rates.clear();
  const second = await postComment(env, {
    slug: SLUG_B,
    name: "Patrick",
    body: "Second chair after the first.",
    requestId: requestId("3"),
  });
  await flush(env);
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

test("thread list is newest-first and paginated past 50", async () => {
  const env = createDoEnv();
  env.THREADS.get(env.THREADS.idFromName(SLUG));
  const thread = env.__threads.get(SLUG);
  await thread.ready;
  for (let i = 0; i < 51; i += 1) {
    const stamp = String(i).padStart(2, "0");
    assert.ok(
      thread.store().insert({
        id: `${SLUG}~n${String(i).padStart(3, "0")}`,
        slug: SLUG,
        name: "Fan",
        body: `Take number ${i} for the table.`,
        createdAt: `2026-09-26T13:${stamp}:00Z`,
        requestId: `22222222-2222-4222-8222-2222222222${String(i).padStart(2, "0")}`,
      }).comment
    );
  }

  const first = await request("GET", `/comments?slug=${SLUG}`, { env });
  const page = await first.json();
  assert.equal(page.comments.length, 50);
  assert.equal(page.comments[0].body, "Take number 50 for the table.");
  assert.ok(page.next);
  const second = await (await request("GET", `/comments?slug=${SLUG}&after=${encodeURIComponent(page.next)}`, { env })).json();
  assert.equal(second.comments.length, 1);
  assert.equal(second.comments[0].body, "Take number 0 for the table.");
  assert.equal(second.next, null);
  assert.equal(THREAD_LIST_LIMIT, 50);
});

test("equal-timestamp cursors tiebreak on id", async () => {
  const env = createDoEnv();
  env.THREADS.get(env.THREADS.idFromName(SLUG));
  const thread = env.__threads.get(SLUG);
  await thread.ready;
  const stamp = "2026-09-26T13:55:00Z";
  for (const ident of ["aaa", "mmm", "zzz"]) {
    assert.ok(
      thread.store().insert({
        id: `${SLUG}~${ident}`,
        slug: SLUG,
        name: "Fan",
        body: ident,
        createdAt: stamp,
        requestId: `33333333-3333-4333-8333-3333333333${ident[0]}`,
      }).comment
    );
  }
  const first = thread.store().list(SLUG, { limit: 2 });
  assert.deepEqual(
    first.comments.map((row) => row.body),
    ["zzz", "mmm"]
  );
  assert.equal(first.next, `${stamp}|${SLUG}~mmm`);
  const second = thread.store().list(SLUG, { limit: 2, after: first.next });
  assert.deepEqual(
    second.comments.map((row) => row.body),
    ["aaa"]
  );
});

test("admin ?all and ?hidden=1 page a thread past 50", async () => {
  const env = createDoEnv();
  env.THREADS.get(env.THREADS.idFromName(SLUG));
  const thread = env.__threads.get(SLUG);
  await thread.ready;
  for (let i = 0; i < 51; i += 1) {
    thread.store().insert({
      id: `${SLUG}~a${String(i).padStart(3, "0")}`,
      slug: SLUG,
      name: "Fan",
      body: `Admin ${i}`,
      createdAt: `2026-09-26T14:${String(i).padStart(2, "0")}:00Z`,
      requestId: `44444444-4444-4444-8444-4444444444${String(i).padStart(2, "0")}`,
    });
  }
  await thread.fetch(new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug: SLUG }) }));
  const dir = env.THREADS.get(env.THREADS.idFromName("__directory__"));
  await dir.fetch(new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug: SLUG }) }));

  const all = await request("GET", "/comments?all=1", {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(all.status, 200);
  const payload = await all.json();
  assert.equal(payload.comments.length, 50);
  assert.ok(payload.nextBySlug[SLUG]);
  assert.equal(ADMIN_SLUG_PAGE, 4);

  const hidden = await request("GET", `/comments?slug=${SLUG}&hidden=1&limit=50`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  const first = await hidden.json();
  assert.equal(first.comments.length, 50);
  assert.ok(first.next);
  const rest = await (
    await request("GET", `/comments?slug=${SLUG}&hidden=1&after=${encodeURIComponent(first.next)}`, {
      env,
      headers: { Authorization: "Bearer secret-admin" },
    })
  ).json();
  assert.equal(rest.comments.length, 1);
});

test("workers.dev has no Cache API so x-comments-cache is always miss", async () => {
  const env = createDoEnv({ __cache: null });
  env.THREADS.get(env.THREADS.idFromName(SLUG));
  const thread = env.__threads.get(SLUG);
  await thread.ready;
  thread.store().insert({
    id: `${SLUG}~cache0`,
    slug: SLUG,
    name: "Fan",
    body: "Uncached take.",
    createdAt: "2026-09-26T13:55:00Z",
    requestId: requestId("7"),
  });
  env.__doGets.length = 0;
  const first = await request("GET", `/comments?slug=${SLUG}`, { env });
  const second = await request("GET", `/comments?slug=${SLUG}`, { env });
  assert.equal(first.headers.get("x-comments-cache"), "miss");
  assert.equal(second.headers.get("x-comments-cache"), "miss");
  assert.equal(first.headers.get("cache-control"), "no-store");
  assert.equal(listGets(env).length, 2);
  assert.equal(dirGets(env).length, 0);
});

test("custom-domain Cache API only: 1 thread DO on miss, 0 on hit, no directory", async () => {
  const env = createDoEnv();
  env.THREADS.get(env.THREADS.idFromName(SLUG));
  const thread = env.__threads.get(SLUG);
  await thread.ready;
  thread.store().insert({
    id: `${SLUG}~cache1`,
    slug: SLUG,
    name: "Fan",
    body: "Cached take.",
    createdAt: "2026-09-26T13:55:00Z",
    requestId: requestId("8"),
  });
  env.__doGets.length = 0;
  const miss = await request("GET", `/comments?slug=${SLUG}`, { env });
  assert.equal(miss.headers.get("x-comments-cache"), "miss");
  assert.equal(miss.headers.get("cache-control"), "no-store");
  assert.equal(listGets(env).length, 1);
  assert.equal(dirGets(env).length, 0);
  assert.equal(env.__rateGets.length, 0);

  const hit = await request("GET", `/comments?slug=${SLUG}`, { env });
  assert.equal(hit.headers.get("x-comments-cache"), "hit");
  assert.equal(hit.headers.get("cache-control"), "no-store");
  assert.equal(listGets(env).length, 1);
  assert.equal(dirGets(env).length, 0);
  assert.equal(env.__rateGets.length, 0);
  assert.equal((await hit.json()).comments[0].body, "Cached take.");
});

test("post succeeds when directory registration fails and is idempotent on requestId", async () => {
  const env = createDoEnv();
  const inner = env.THREADS;
  env.THREADS = {
    idFromName: inner.idFromName,
    get(id) {
      if (id.name === "__directory__") {
        return {
          fetch: async () => {
            throw new Error("directory down");
          },
        };
      }
      return inner.get(id);
    },
  };
  const id = requestId("9");
  const first = await postComment(env, { requestId: id, body: "Once is enough for the table." });
  assert.equal(first.status, 201);
  const created = await first.json();
  env.__rates.clear();
  const replay = await postComment(env, { requestId: id, body: "Once is enough for the table." });
  assert.equal(replay.status, 201);
  const again = await replay.json();
  assert.equal(again.comment.id, created.comment.id);
  const listed = await (await request("GET", `/comments?slug=${SLUG}`, { env })).json();
  assert.equal(listed.comments.length, 1);
});

test("public GET ignores caller limit and stays a fixed page of 50", async () => {
  const env = createDoEnv();
  env.THREADS.get(env.THREADS.idFromName(SLUG));
  const thread = env.__threads.get(SLUG);
  await thread.ready;
  for (let i = 0; i < 8; i += 1) {
    thread.store().insert({
      id: `${SLUG}~lim${i}`,
      slug: SLUG,
      name: "Fan",
      body: `Poison ${i}`,
      createdAt: `2026-09-26T13:0${i}:00Z`,
      requestId: `55555555-5555-4555-8555-55555555555${i}`,
    });
  }
  const poisoned = await request("GET", `/comments?slug=${SLUG}&limit=1`, { env });
  const page = await poisoned.json();
  assert.equal(poisoned.status, 200);
  assert.equal(page.comments.length, 8);
  assert.notEqual(page.comments.length, 1);
  assert.equal(THREAD_LIST_LIMIT, 50);
});

test("hide bumps version and does not let an older cache put win", async () => {
  const env = createDoEnv();
  const created = await (await postComment(env)).json();
  env.__doGets.length = 0;
  const first = await request("GET", `/comments?slug=${SLUG}`, { env });
  assert.equal((await first.json()).comments.length, 1);
  assert.equal(first.headers.get("x-comments-cache"), "miss");
  const versionAfterList = env.__threads.get(SLUG).store().version();

  const hidden = await request("POST", `/comments/${created.comment.id}/hide`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(hidden.status, 200);
  const versionAfterHide = env.__threads.get(SLUG).store().version();
  assert.ok(versionAfterHide > versionAfterList);

  env.__doGets.length = 0;
  const afterHide = await request("GET", `/comments?slug=${SLUG}`, { env });
  assert.equal(afterHide.headers.get("x-comments-cache"), "miss");
  assert.equal((await afterHide.json()).comments.length, 0);
  assert.equal(listGets(env).length, 1);

  const stale = await env.__cache.match(new Request("https://comments-cache/ver/" + encodeURIComponent(SLUG)));
  assert.equal(String(await stale.text()), String(versionAfterHide));
});

test("migrateThreadSchema upgrades an old-shape comments table", () => {
  const db = new DatabaseSync(":memory:");
  const sql = {
    exec(query, ...binds) {
      const statements = String(query)
        .split(";")
        .map((item) => item.trim())
        .filter(Boolean);
      let rows = [];
      let offset = 0;
      for (const text of statements) {
        const count = (text.match(/\?/g) || []).length;
        const args = binds.slice(offset, offset + count);
        offset += count;
        if (/^\s*(select|pragma)/i.test(text)) {
          rows = db.prepare(text).all(...args);
        } else {
          db.prepare(text).run(...args);
        }
      }
      return { toArray: () => rows };
    },
  };
  sql.exec(`CREATE TABLE comments (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    body TEXT NOT NULL,
    createdAt TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0
  )`);
  sql.exec(
    `INSERT INTO comments (id, slug, name, body, createdAt, hidden)
     VALUES (?, ?, ?, ?, ?, 0)`,
    `${SLUG}~old`,
    SLUG,
    "Fan",
    "Old row before requestId.",
    "2026-09-26T13:55:00Z"
  );
  sql.exec(
    `INSERT INTO comments (id, slug, name, body, createdAt, hidden)
     VALUES (?, ?, ?, ?, ?, 0)`,
    `${SLUG}~old2`,
    SLUG,
    "Pat",
    "Second old row.",
    "2026-09-26T13:56:00Z"
  );
  sql.exec(
    `INSERT INTO comments (id, slug, name, body, createdAt, hidden)
     VALUES (?, ?, ?, ?, ?, 0)`,
    `${SLUG}~old3`,
    SLUG,
    "Andy",
    "Third old row.",
    "2026-09-26T13:57:00Z"
  );
  migrateThreadSchema(sql);
  migrateThreadSchema(sql);
  const columns = sql.exec(`PRAGMA table_info(comments)`).toArray().map((row) => row.name);
  assert.ok(columns.includes("requestId"));
  const schema = sql.exec(`SELECT value FROM meta WHERE key = 'schema_version'`).toArray();
  assert.equal(Number(schema[0].value), SCHEMA_VERSION);
  const indexes = sql
    .exec(`SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'comments_request_id'`)
    .toArray();
  assert.equal(indexes.length, 1);
  const leftover = sql.exec(`SELECT name, requestId FROM comments ORDER BY createdAt`).toArray();
  assert.equal(leftover.length, 3);
  assert.deepEqual(
    leftover.map((row) => row.requestId),
    [null, null, null]
  );
  assert.equal(leftover[0].name, "Fan");
});

test("unknown slug refetches the origin at most once per 60s", async () => {
  resetSlugCache();
  let fetches = 0;
  const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("comments-slugs")) {
      fetches += 1;
      return Response.json({ slugs: [SLUG] });
    }
    return previous(url);
  };
  try {
    const env = createDoEnv({ COMMENTS_KNOWN_SLUGS: "" });
    const first = await request("GET", "/comments?slug=not-a-real-edition", { env });
    const second = await request("GET", "/comments?slug=also-missing", { env });
    assert.equal(first.status, 404);
    assert.equal(second.status, 404);
    assert.equal(fetches, 1);
    assert.equal(env.__threads.has("not-a-real-edition"), false);
    assert.equal(SLUGS_TTL_MS, 60 * 1000);
  } finally {
    globalThis.fetch = previous;
    resetSlugCache();
  }
});

test("requestId replay runs before the 20s post limit", async () => {
  const env = createDoEnv();
  const id = requestId("a");
  const first = await postComment(env, { requestId: id, body: "Replay this chair." });
  assert.equal(first.status, 201);
  const created = await first.json();
  const replay = await postComment(env, { requestId: id, body: "Replay this chair." });
  assert.equal(replay.status, 201);
  const again = await replay.json();
  assert.equal(again.comment.id, created.comment.id);
  assert.equal(again.replayed, true);
});

test("requestId replay skips Turnstile so a spent token does not insert a duplicate", async () => {
  const env = createDoEnv();
  const id = requestId("c");
  const spent = "spent-turnstile-token";
  const first = await postComment(env, {
    requestId: id,
    body: "Saved then the 5xx happened.",
    turnstileToken: spent,
  });
  assert.equal(first.status, 201);
  const created = await first.json();
  const calls = turnstileCalls;
  const replay = await postComment(env, {
    requestId: id,
    body: "Saved then the 5xx happened.",
    turnstileToken: spent,
  });
  assert.equal(replay.status, 201);
  const again = await replay.json();
  assert.equal(again.comment.id, created.comment.id);
  assert.equal(again.replayed, true);
  assert.equal(turnstileCalls, calls);
  const invalid = await postComment(env, {
    requestId: id,
    body: "Saved then the 5xx happened.",
    turnstileToken: "garbage-or-reused",
  });
  assert.equal(invalid.status, 201);
  assert.equal((await invalid.json()).comment.id, created.comment.id);
  const listed = await (await request("GET", `/comments?slug=${SLUG}`, { env })).json();
  assert.equal(listed.comments.length, 1);
});

test("concurrent cold slug fetches share one in-flight origin request", async () => {
  resetSlugCache();
  let fetches = 0;
  let release;
  const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("comments-slugs")) {
      fetches += 1;
      return new Promise((resolve) => {
        release = () => resolve(Response.json({ slugs: [SLUG] }));
      });
    }
    return previous(url);
  };
  try {
    const env = createDoEnv({ COMMENTS_KNOWN_SLUGS: "" });
    const first = request("GET", `/comments?slug=${SLUG}`, { env });
    const second = request("GET", `/comments?slug=${SLUG}`, { env });
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(fetches, 1);
    release();
    const [a, b] = await Promise.all([first, second]);
    assert.equal(a.status, 200);
    assert.equal(b.status, 200);
  } finally {
    globalThis.fetch = previous;
    resetSlugCache();
  }
});

test("failed slug fetch uses last good list and retries after a short backoff", async () => {
  resetSlugCache();
  let fetches = 0;
  let ok = false;
  const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("comments-slugs")) {
      fetches += 1;
      if (!ok) return new Response("no", { status: 503 });
      return Response.json({ slugs: [SLUG] });
    }
    return previous(url);
  };
  try {
    const env = createDoEnv({ COMMENTS_KNOWN_SLUGS: "" });
    const first = await request("GET", `/comments?slug=${SLUG}`, { env });
    assert.equal(first.status, 503);
    const blocked = await request("GET", `/comments?slug=${SLUG}`, { env });
    assert.equal(blocked.status, 503);
    assert.equal(fetches, 1);
    expireSlugFetchBackoff();
    ok = true;
    const recovered = await request("GET", `/comments?slug=${SLUG}`, { env });
    assert.equal(recovered.status, 200);
    assert.equal(fetches, 2);
    expireSlugList();
    ok = false;
    const stale = await request("GET", `/comments?slug=${SLUG}`, { env });
    assert.equal(stale.status, 200);
    assert.equal(fetches, 3);
  } finally {
    globalThis.fetch = previous;
    resetSlugCache();
  }
});

test("admin ?all lists at most 4 slugs and one page each", async () => {
  const env = createDoEnv();
  const slugs = [SLUG, SLUG_B, "2026-09-24-1200", "2026-09-23-1200", "2026-09-22-1200"];
  env.COMMENTS_KNOWN_SLUGS = slugs.join(",");
  const dir = env.THREADS.get(env.THREADS.idFromName("__directory__"));
  for (const item of slugs) {
    env.THREADS.get(env.THREADS.idFromName(item));
    const thread = env.__threads.get(item);
    await thread.ready;
    for (let i = 0; i < 2; i += 1) {
      thread.store().insert({
        id: `${item}~${i}`,
        slug: item,
        name: "Fan",
        body: `${item} ${i}`,
        createdAt: `2026-09-26T13:0${i}:00Z`,
        requestId: `66666666-6666-4666-8666-${String(slugs.indexOf(item) * 10 + i).padStart(12, "0")}`,
      });
    }
    await dir.fetch(new Request("https://do/remember", { method: "POST", body: JSON.stringify({ slug: item }) }));
  }
  env.__doGets.length = 0;
  const all = await request("GET", "/comments?all=1", {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  const payload = await all.json();
  assert.equal(all.status, 200);
  assert.ok(payload.comments.length <= 4 * THREAD_LIST_LIMIT);
  assert.ok(payload.nextSlug);
  const threadLists = (env.__doGets || []).filter((item) => item.path === "/list");
  assert.ok(threadLists.length <= 4);
  assert.equal(ADMIN_SLUG_PAGE, 4);
});

test("public GET works without RATES; post and admin fail closed", async () => {
  const env = createDoEnv({ RATES: undefined });
  env.__rateGets = [];
  const listed = await request("GET", `/comments?slug=${SLUG}`, { env });
  assert.equal(listed.status, 200);
  assert.equal(listGets(env).length, 1);

  const posted = await postComment(env, { requestId: requestId("b") });
  assert.equal(posted.status, 503);

  const admin = await request("GET", "/comments?all=1", {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(admin.status, 503);
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
