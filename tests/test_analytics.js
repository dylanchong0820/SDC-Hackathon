/* No network, tracker credentials, customer content or dependencies required. */
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync("src/static/analytics.js", "utf8");
const events = [];
const listeners = {};
const window = { location: { href: "https://example.test/", origin: "https://example.test" } };
const context = vm.createContext({
  window, URL, document: { addEventListener: (name, callback) => { listeners[name] = callback; } }
});
vm.runInContext(source, context);
const analytics = window.QuoteAnalytics;
assert.doesNotThrow(() => analytics.trackEvent("staff_login")); // Development: no tracker.
window.umami = { track: (name, data) => events.push([name, JSON.parse(JSON.stringify(data))]) };
analytics.trackEvent("staff_login", { password: "must-not-leave-browser", phone: "private" });
assert.deepEqual(events.pop(), ["staff_login", {}]);
analytics.trackEvent("unknown_event", { content: "private" });
assert.equal(events.length, 0);
analytics.trackEvent("comparison_requested", {
  priority: "private user text", source_count: -1, retry: "private", vendor: "private"
});
assert.deepEqual(events.pop(), ["comparison_requested", {}]);
analytics.recordResponse("/api/quote-evaluations", "POST",
  { priority: "price", message_ids: ["private-message-id"], review_token: "private" },
  { evaluation: { id: 55, status: "queued" } });
analytics.recordResponse("/api/quote-evaluations/55", "GET", null,
  { evaluation: { id: 55, status: "completed", result: "private quotation" } });
analytics.recordResponse("/api/quote-evaluations/55", "GET", null,
  { evaluation: { id: 55, status: "completed" } });
assert.deepEqual(events.splice(0), [
  ["comparison_requested", { priority: "price", source_count: 1, retry: false }],
  ["comparison_finished", { result: "completed" }]
]);
analytics.recordResponse("/api/quote-evaluations/99", "GET", null,
  { evaluation: { id: 99, status: "completed" } });
assert.equal(events.length, 0); // Viewing saved results is not a new completion.
analytics.recordResponse("/api/messages", "POST", { text: "private message" },
  { message: { status: "sent", id: "private" } });
assert.deepEqual(events.pop(), ["message_send_result", { result: "accepted" }]);
analytics.recordResponse("/api/conversations/123/history", "DELETE",
  { delete_attachments: true }, { ok: true });
assert.deepEqual(events.pop(), ["history_cleared", { scope: "chat", include_attachments: true }]);
for (const [path, format] of [["/api/quotation-register.xlsx", "xlsx"], ["/api/archive/messages.txt", "txt"]]) {
  listeners.click({ target: { closest: () => ({ href: "https://example.test" + path }) } });
  assert.deepEqual(events.pop(), ["report_download_requested", { format }]);
}
window.umami.track = () => { throw new Error("Tracker unavailable"); };
assert.doesNotThrow(() => analytics.trackEvent("staff_login"));
window.umami.track = () => Promise.reject(new Error("Tracker rejected"));
analytics.trackEvent("staff_login");
console.log("Analytics tests passed: privacy allowlists, safe no-op, failures, outcomes and polling deduplication.");
