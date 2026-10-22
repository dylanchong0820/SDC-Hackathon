const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Exercise the real request helper without loading private data or sending messages.
const source = fs.readFileSync("src/static/inbox.js", "utf8");
const helper = source.slice(source.indexOf("  async function request("),
  source.indexOf("  function toDate("));

function makeRequest(fetch) {
  const context = vm.createContext({
    Headers, AbortController, fetch, state: { csrf: "test-csrf" },
    handleExpiredSession: async () => {},
    window: {
      setTimeout: (callback, delay) => {
        assert.equal(delay, 15000);
        return setTimeout(callback, 10);
      },
      clearTimeout,
    },
  });
  return vm.runInContext(`${helper}\nrequest`, context);
}

test("stalled startup request times out rather than leaving the loading screen forever", async () => {
  const request = makeRequest((path, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  }));
  await assert.rejects(request("/api/session"), /server took too long/);
});

test("stalled response body also times out", async () => {
  const request = makeRequest(async (path, { signal }) => ({
    ok: true, status: 200,
    json: () => new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    }),
  }));
  await assert.rejects(request("/api/status"), /server took too long/);
});

test("successful requests retain credentials and CSRF protection", async () => {
  const request = makeRequest(async (path, options) => {
    assert.equal(options.credentials, "include");
    assert.equal(options.headers.get("X-CSRF-Token"), "test-csrf");
    assert.equal(options.body, '{"text":"test"}');
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  });
  assert.equal((await request("/api/messages", { method: "POST", body: { text: "test" } })).ok, true);
});

test("HTTP errors retain their status", async () => {
  const request = makeRequest(async () => ({
    ok: false, status: 403, json: async () => ({ error: "Security token expired" }),
  }));
  await assert.rejects(request("/api/login", { method: "POST" }),
    (err) => err.status === 403 && err.message === "Security token expired");
});
