import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AUTH_FAIL_MAX,
  HONEYPOT_FIELD,
  MemoryRateStore,
  MemoryThreadStore,
  RATE_MAX,
  createMemoryEnv,
  corsOrigin,
  handleRequest,
} from "./worker.js";

const SLUG = "2026-09-26-1355";

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
    env || createMemoryEnv()
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
  const env = createMemoryEnv();
  const res = await postComment(env, { [HONEYPOT_FIELD]: "http://spam.example" });
  assert.equal(res.status, 201);
  const data = await res.json();
  assert.equal(data.ignored, true);
  const listed = await (await request("GET", `/comments?slug=${SLUG}`, { env })).json();
  assert.deepEqual(listed.comments, []);
});

test("rate limit rejects a burst from one IP", async () => {
  const env = createMemoryEnv();
  const now = Date.now() / 1000;
  env.__memory.rates.hits.set(
    "unknown",
    Array.from({ length: RATE_MAX }, (_, i) => now - 30 * (RATE_MAX - i))
  );
  const res = await postComment(env);
  assert.equal(res.status, 429);
});

test("Turnstile fail-closed on missing token and failed siteverify", async () => {
  const env = createMemoryEnv();
  const missing = await withTurnstile(() =>
    request("POST", "/comments", {
      env,
      body: { slug: SLUG, name: "A", body: "Hello there", turnstileToken: "" },
    })
  );
  assert.equal(missing.status, 400);

  const failed = await withTurnstile(
    () =>
      request("POST", "/comments", {
        env,
        body: { slug: SLUG, name: "A", body: "Hello there", turnstileToken: "bad" },
      }),
    false
  );
  assert.equal(failed.status, 400);

  const noSecret = await withTurnstile(() =>
    request("POST", "/comments", {
      env: createMemoryEnv({ TURNSTILE_SECRET: "" }),
      body: { slug: SLUG, name: "A", body: "Hello there", turnstileToken: "ok" },
    })
  );
  assert.equal(noSecret.status, 400);
});

test("admin auth returns 401, 503, and throttles bad tokens", async () => {
  const env = createMemoryEnv();
  const unauthorized = await request("POST", "/comments/nope/hide", { env });
  assert.equal(unauthorized.status, 401);
  assert.equal(unauthorized.headers.get("X-Robots-Tag"), "noindex, nofollow");

  const unconfigured = await request("DELETE", "/comments/nope", {
    env: createMemoryEnv({ COMMENTS_ADMIN_TOKEN: "" }),
    headers: { Authorization: "Bearer x" },
  });
  assert.equal(unconfigured.status, 503);

  const rates = new MemoryRateStore();
  const now = Date.now() / 1000;
  rates.authFails.set(
    "203.0.113.9",
    Array.from({ length: AUTH_FAIL_MAX }, (_, i) => now - i)
  );
  const blocked = await request("POST", "/comments/nope/hide", {
    env: createMemoryEnv({ rateStore: rates }),
    headers: { Authorization: "Bearer wrong", "CF-Connecting-IP": "203.0.113.9" },
  });
  assert.equal(blocked.status, 429);
});

test("hide and delete use tombstones that a later post cannot resurrect", async () => {
  const env = createMemoryEnv();
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

  env.__memory.rates.hits.clear();
  const second = await postComment(env, { name: "Patrick", body: "New take after hide." });
  assert.equal(second.status, 201);
  const afterPost = await (await request("GET", `/comments?slug=${SLUG}`, { env })).json();
  assert.equal(afterPost.comments.length, 1);
  assert.equal(afterPost.comments[0].name, "Patrick");
  assert.ok(env.__memory.thread.rows.get(id).hidden);

  const deleted = await request("DELETE", `/comments/${id}`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(deleted.status, 200);
  assert.equal(env.__memory.thread.rows.get(id).deleted, true);
  const again = await request("DELETE", `/comments/${id}`, {
    env,
    headers: { Authorization: "Bearer secret-admin" },
  });
  assert.equal(again.status, 404);
});

test("CORS allows only configured production origins unless COMMENTS_DEV is set", async () => {
  const env = createMemoryEnv();
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

test("memory store starts empty", () => {
  assert.equal(new MemoryThreadStore().list(SLUG).length, 0);
});
