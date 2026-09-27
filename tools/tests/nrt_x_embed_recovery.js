#!/usr/bin/env node
/**
 * Unit test for late widgets.js recovery and the 'rendered' Event target.
 * Loads the helper functions from public/js/main.js via vm so it tests
 * the real source, not a copy.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "../..");
const src = fs.readFileSync(path.join(ROOT, "public/js/main.js"), "utf8");
const start = src.indexOf("function nrtXHasRendered");
const end = src.indexOf("function initNarrativeXEmbeds");
assert.ok(start >= 0 && end > start, "could not extract nrt-x helpers from main.js");

function makeClassList() {
  const set = new Set();
  return {
    add(...xs) {
      xs.forEach((x) => set.add(x));
    },
    remove(...xs) {
      xs.forEach((x) => set.delete(x));
    },
    contains(x) {
      return set.has(x);
    },
    [Symbol.iterator]() {
      return set[Symbol.iterator]();
    },
  };
}

function makeEmbed({ rendered = false } = {}) {
  const fallback = { hidden: true, className: "nrt-x-embed__fallback" };
  const iframe = rendered ? { nodeType: 1, tagName: "IFRAME" } : null;
  const el = {
    nodeType: 1,
    classList: makeClassList(),
    _nrtXFallbackTimer: null,
    fallback,
    iframe,
    querySelector(sel) {
      if (sel === "iframe" || sel === "twitter-widget") return this.iframe;
      if (sel === ".nrt-x-embed__fallback") return this.fallback;
      return null;
    },
    contains(node) {
      if (!node || typeof node.nodeType !== "number") {
        throw new TypeError("Failed to execute 'contains' on 'Node'");
      }
      return node === this || node === this.iframe;
    },
  };
  return el;
}

const sandbox = {
  window: { twttr: { events: { bind() {} } } },
  document: {},
  setTimeout,
  clearTimeout,
  MutationObserver: class {
    observe() {}
    disconnect() {}
  },
};
vm.runInNewContext(src.slice(start, end), sandbox);

const {
  nrtXHasRendered,
  nrtXWidgetFromRendered,
  nrtXMarkReady,
  nrtXMarkFallback,
} = sandbox;

assert.equal(typeof nrtXMarkReady, "function");
assert.equal(typeof nrtXWidgetFromRendered, "function");

// Sequence: 5s fallback, then late render restores the frame.
const late = makeEmbed();
late.classList.add("is-loading");
nrtXMarkFallback(late);
assert.ok(late.classList.contains("is-fallback"), "should collapse at 5s");
assert.ok(!late.classList.contains("is-loading"));
assert.equal(late.fallback.hidden, false, "fallback link visible while blocked");

late.iframe = { nodeType: 1, tagName: "IFRAME" };
assert.ok(nrtXHasRendered(late));
nrtXMarkReady(late);
assert.ok(late.classList.contains("is-ready"), "late render marks ready");
assert.ok(!late.classList.contains("is-fallback"), "is-fallback must be removed");
assert.ok(!late.classList.contains("is-loading"));
assert.equal(late.fallback.hidden, true, "fallback link hidden after recovery");

// widgets.js passes an Event; contains() must receive event.target.
const event = { type: "rendered", target: late.iframe };
assert.strictEqual(nrtXWidgetFromRendered(event), late.iframe);
assert.doesNotThrow(() => late.contains(nrtXWidgetFromRendered(event)));
assert.strictEqual(nrtXWidgetFromRendered({ type: "rendered" }), null);
assert.strictEqual(nrtXWidgetFromRendered(late.iframe), late.iframe);
assert.throws(
  () => late.contains({ type: "rendered" }),
  /contains/,
  "raw Event must not be passed to contains"
);

console.log("nrt_x_embed_recovery.js: ok");
