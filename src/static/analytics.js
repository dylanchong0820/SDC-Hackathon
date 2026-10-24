/* Replit injects Umami on published sites. Never add a tracker or credentials. */
(() => {
  "use strict";
  const definitions = {
    staff_login: {},
    workspace_tool_opened: { tool: ["home", "inbox", "quotes", "register", "chat", "settings", "new", "checklist"] },
    message_send_result: { result: ["accepted", "failed"] },
    comparison_requested: { priority: ["price", "time", "balanced"], source_count: "count", retry: "boolean" },
    comparison_finished: { result: ["completed", "failed"] },
    comparison_stopped: {},
    quotation_selected: {},
    quote_ai_answered: {},
    report_download_requested: { format: ["pdf", "xlsx", "txt"] },
    history_cleared: { scope: ["chat", "inbox"], include_attachments: "boolean" },
    history_imported: {}
  };
  const pending = new Set(); // Local correlation only; IDs are never transmitted.

  function trackEvent(name, properties = {}) {
    try {
      if (!Object.hasOwn(definitions, name)) return;
      const safe = {};
      for (const [key, rule] of Object.entries(definitions[name])) {
        const value = properties[key];
        if ((Array.isArray(rule) && rule.includes(value)) ||
            (rule === "boolean" && typeof value === "boolean") ||
            (rule === "count" && Number.isInteger(value) && value >= 0 && value <= 1000000)) {
          safe[key] = value;
        }
      }
      const result = window.umami?.track(name, safe);
      // The injected tracker may return a promise. Rejections must be harmless.
      if (result && typeof result.catch === "function") result.catch(() => {});
    } catch (_) {
      // Analytics must never interrupt staff work.
    }
  }

  function recordResponse(path, method, body, data) {
    try {
      if (method === "POST") {
        if (path === "/api/login") trackEvent("staff_login");
        else if (path === "/api/messages" && data.message) {
          trackEvent("message_send_result", {
            result: data.message.status === "failed" ? "failed" : "accepted"
          });
        } else if (path === "/api/quote-evaluations" ||
                   /^\/api\/quote-evaluations\/\d+\/retry$/.test(path)) {
          if (!data.evaluation?.id) return;
          const id = String(data.evaluation.id);
          // Prevent duplicate start events for a repeated response.
          if (!pending.has(id)) {
            pending.add(id);
            trackEvent("comparison_requested", {
              priority: body?.priority,
              source_count: Array.isArray(body?.message_ids) ? body.message_ids.length : 0,
              retry: path.endsWith("/retry")
            });
          }
        } else if (/^\/api\/quote-evaluations\/\d+\/cancel$/.test(path)) {
          if (data.evaluation?.status === "cancelled") {
            pending.delete(String(data.evaluation.id));
            trackEvent("comparison_stopped");
          }
        } else if (/^\/api\/quote-evaluations\/\d+\/selection$/.test(path)) {
          trackEvent("quotation_selected");
        } else if (path === "/api/quote-chat" && data.reply && data.pdf_base64) {
          trackEvent("quote_ai_answered");
        } else if (path === "/api/history/import") trackEvent("history_imported");
      } else if (method === "DELETE" &&
                 (path === "/api/history" || /^\/api\/conversations\/\d+\/history$/.test(path))) {
        trackEvent("history_cleared", {
          scope: path === "/api/history" ? "inbox" : "chat",
          include_attachments: body?.delete_attachments
        });
      }
      const evaluation = data.evaluation;
      if (evaluation && pending.has(String(evaluation.id)) &&
          ["completed", "failed", "cancelled"].includes(evaluation.status)) {
        pending.delete(String(evaluation.id));
        if (evaluation.status !== "cancelled") {
          trackEvent("comparison_finished", { result: evaluation.status });
        }
      }
    } catch (_) {
      // Never inspect or forward exception messages or response content.
    }
  }

  document.addEventListener("click", (event) => {
    try {
      const link = event.target.closest("a[href]");
      if (!link) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      const format = {
        "/api/quotation-register.xlsx": "xlsx",
        "/api/archive/messages.txt": "txt"
      }[url.pathname];
      if (format) trackEvent("report_download_requested", { format });
    } catch (_) {}
  });
  window.QuoteAnalytics = Object.freeze({ trackEvent, recordResponse });
})();
