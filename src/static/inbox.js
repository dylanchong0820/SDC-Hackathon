(() => {
  "use strict";

  const app = document.getElementById("app");
  const toastRegion = document.getElementById("toast-region");
  const icons = {
    chat: '<path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.9-3A7.5 7.5 0 1 1 20 11.5Z"/><path d="M8 11h.01M12.5 11h.01M17 11h.01"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4 4"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.1 1.8-1.7 2.9-2.1-.5a7.6 7.6 0 0 1-1.7 1l-.7 2.1h-3.4l-.7-2.1a7.6 7.6 0 0 1-1.7-1l-2.1.5-1.7-2.9 1.1-1.8A7.8 7.8 0 0 1 5.5 13l-2-.9v-3.4l2-.9c.1-.7.4-1.3.7-1.9L5.1 4l1.7-2.9 2.1.5c.5-.4 1.1-.7 1.7-1L11.3-1h3.4l.7 2.1c.6.3 1.2.6 1.7 1l2.1-.5L21 4.5l-1.1 1.8c.3.6.6 1.2.7 1.9l2 .9v3.4l-2 .9c-.2.6-.6 1.1-1.2 1.6Z"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    back: '<path d="m15 18-6-6 6-6M9 12h11"/>',
    send: '<path d="m21 3-7.4 18-3.9-7.7L2 9.4 21 3Z"/><path d="M9.7 13.3 21 3"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.7 9A7 7 0 0 1 18 6l2 2M4 16l2 2a7 7 0 0 0 12.3-3"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5"/><path d="M5 17v3h14v-3"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
    home: '<path d="m3 10 9-7 9 7"/><path d="M5 9.5V21h14V9.5M9 21v-6h6v6"/>',
    assistant: '<path d="M12 3.5v3M12 17.5v3M3.5 12h3m11 0h3M6 6l2.2 2.2m7.6 7.6L18 18M18 6l-2.2 2.2m-7.6 7.6L6 18"/><circle cx="12" cy="12" r="4"/>'
  };
  const state = {
    csrf: "", authenticated: false, staffConfigured: true, status: null, settings: null,
    conversations: [], visibleConversations: null, selectedId: null, messages: [], hasMore: false,
    beforeSeq: null, beforeTimestamp: null,
    mode: "inbox", search: "", drafts: new Map(), pendingSend: null, pollError: "",
    loadingMessages: false, sending: false, polling: false, lastStatusSuccess: 0,
    listError: "", initialMessagesLoaded: false, pollIssues: {}, handlingExpiredSession: false,
    conversationRequestId: 0, messageSearchRequestId: 0, messageSearchActive: false,
    messageSearchResults: [], messageSearchError: "", messageSearchLoading: false,
    openAround: null, highlightMessageId: null, historyRevision: null, historyEpoch: 0
  };
  let refs = {};
  let pollTimer = null;
  let searchTimer = null;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }
  function icon(name, className = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    if (className) svg.setAttribute("class", className);
    const parsed = new DOMParser().parseFromString(
      `<svg xmlns="http://www.w3.org/2000/svg"><g>${icons[name] || ""}</g></svg>`,
      "image/svg+xml"
    );
    svg.appendChild(document.importNode(parsed.documentElement.firstChild, true));
    return svg;
  }
  function button(label, className, symbol, action) {
    const btn = node("button", className);
    btn.type = "button";
    btn.setAttribute("aria-label", label);
    if (symbol) btn.appendChild(icon(symbol));
    if (label && !symbol) btn.textContent = label;
    btn.addEventListener("click", action);
    return btn;
  }
  function toast(message, error = false) {
    const item = node("div", `toast${error ? " error" : ""}`, message);
    toastRegion.appendChild(item);
    window.setTimeout(() => item.remove(), 4200);
  }
  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const isMultipart = typeof FormData !== "undefined" && options.body instanceof FormData;
    if (options.body !== undefined && !isMultipart) headers.set("Content-Type", "application/json");
    if (options.method && options.method !== "GET" && state.csrf) headers.set("X-CSRF-Token", state.csrf);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), path === "/api/quote-previews" ? 135000 : 15000);
    try {
      const response = await fetch(path, {
        ...options, headers, credentials: "include", signal: controller.signal,
        body: options.body === undefined ? undefined : (isMultipart ? options.body : JSON.stringify(options.body))
      });
      let data = {};
      try {
        data = await response.json();
      } catch (err) {
        if (controller.signal.aborted) throw err;
        // Keep non-JSON failures readable.
      }
      if (response.status === 401 && path !== "/api/login" && path !== "/api/session") {
        await handleExpiredSession();
      }
      if (!response.ok) {
        const error = new Error(data.error || `Request failed (${response.status})`);
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (err) {
      if (controller.signal.aborted) {
        throw new Error("The server took too long to respond. Check your connection and try again.");
      }
      throw err;
    } finally {
      window.clearTimeout(timeout);
    }
  }
  function toDate(value) {
    if (value === null || value === undefined || value === "") return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    let date;
    if (typeof value === "number" || (typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value.trim()))) {
      const seconds = Number(value);
      if (!Number.isFinite(seconds)) return null;
      date = new Date(seconds * 1000);
    } else {
      date = new Date(value);
    }
    return Number.isNaN(date.getTime()) ? null : date;
  }
  function shortTime(dateValue) {
    const date = toDate(dateValue);
    if (!date) return "";
    const now = new Date();
    if (date.toDateString() === now.toDateString()) {
      return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
    if (date.getFullYear() === now.getFullYear()) return date.toLocaleDateString([], { month: "short", day: "numeric" });
    return date.toLocaleDateString([], { year: "2-digit", month: "numeric", day: "numeric" });
  }
  function fullTime(dateValue) {
    const date = toDate(dateValue);
    return date ? date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "Time unavailable";
  }
  function initials(name) {
    const words = String(name || "").trim().split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] || "?").slice(0, 2)).toUpperCase();
  }
  function selectedConversation() {
    return state.conversations.find((item) => String(item.id) === String(state.selectedId)) || null;
  }
  function windowIsOpen(conversation) {
    if (!conversation || conversation.last_inbound_at === null || conversation.last_inbound_at === undefined) return false;
    const lastInbound = toDate(conversation.last_inbound_at);
    if (!lastInbound) return false;
    const elapsed = Date.now() - lastInbound.getTime();
    return conversation.window_open === true && Number.isFinite(elapsed) && elapsed >= 0 && elapsed < 24 * 60 * 60 * 1000;
  }
  function sameOriginMedia(path, forceDownload = false) {
    if (!path) return "";
    try {
      const url = new URL(path, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/media/")) return "";
      if (forceDownload) url.searchParams.set("download", "1");
      return url.href;
    } catch (_) { return ""; }
  }
  function brand(onClick) {
    const mark = node("span", "brand-mark");
    mark.appendChild(icon("chat"));
    const name = node("span", "brand-name");
    name.appendChild(document.createTextNode("Quote Bot "));
    const accent = node("span", "", "AI");
    name.appendChild(accent);
    const wrapper = node(onClick ? "button" : "div", "brand");
    if (onClick) {
      wrapper.type = "button";
      wrapper.setAttribute("aria-label", "Quote Bot AI: workspace home");
      wrapper.title = "Go to workspace home";
      wrapper.addEventListener("click", onClick);
    }
    wrapper.append(mark, name);
    return wrapper;
  }
  function showLoading(message = "Loading your workspace") {
    app.replaceChildren();
    const screen = node("div", "state-panel");
    const card = node("div", "state-card");
    const mark = node("div", "state-mark");
    mark.appendChild(icon("chat"));
    card.append(mark, node("h2", "", message), node("p", "", "Connecting to your staff workspace…"));
    screen.appendChild(card);
    app.appendChild(screen);
  }
  function showStartupError(message) {
    app.replaceChildren();
    const screen = node("div", "state-panel");
    const card = node("div", "state-card");
    const mark = node("div", "state-mark");
    mark.appendChild(icon("refresh"));
    card.append(mark, node("h2", "", "We couldn't reach the desk"), node("p", "", message));
    const actions = node("div", "state-actions");
    actions.appendChild(button("Try again", "primary-button", null, start));
    card.appendChild(actions);
    screen.appendChild(card);
    app.appendChild(screen);
  }
  function showAuth() {
    app.replaceChildren();
    const shell = node("div", "auth-shell");
    const aside = node("section", "auth-aside");
    aside.appendChild(brand());
    const quote = node("div", "auth-quote");
    quote.append(node("div", "eyebrow", "A quieter kind of busy"), node("h1", "", "Every customer, heard."));
    quote.appendChild(node("p", "", "One dependable place to follow the conversation, keep every attachment, and get back to people while it still matters."));
    aside.append(quote, node("div", "auth-aside-foot", "Staff workspace · private by design"));
    const main = node("section", "auth-main");
    const card = node("form", "auth-card");
    card.append(node("div", "page-kicker", "Staff access"), node("h2", "", "Sign in to the desk"), node("p", "", "Use your team password to open the conversation inbox."));
    const label = node("label", "field-label", "Staff password");
    label.htmlFor = "staff-password";
    const password = node("input", "field-input");
    password.id = "staff-password";
    password.type = "password";
    password.name = "password";
    password.autocomplete = "current-password";
    password.required = true;
    card.append(label, password);
    const error = node("div", "inline-error hidden");
    error.setAttribute("role", "alert");
    card.appendChild(error);
    const submit = node("button", "primary-button", "Sign in");
    submit.type = "submit";
    card.appendChild(submit);
    card.appendChild(node("div", "auth-note", "Multiple employees can sign in at the same time using the same staff password on their own devices or browsers. Signing out here does not sign out other employees. Do not enter Meta access tokens or app secrets here."));
    card.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.classList.add("hidden");
      submit.disabled = true;
      submit.textContent = "Signing in…";
      try {
        const result = await request("/api/login", { method: "POST", body: { password: password.value } });
        state.authenticated = true;
        state.csrf = result.csrf_token || state.csrf;
        await bootWorkspace();
      } catch (err) {
        error.textContent = err.message;
        error.classList.remove("hidden");
        password.focus();
      } finally {
        submit.disabled = false;
        submit.textContent = "Sign in";
      }
    });
    main.appendChild(card);
    shell.append(aside, main);
    app.appendChild(shell);
  }
  function showStaffSetup() {
    app.replaceChildren();
    const shell = node("div", "auth-shell");
    const aside = node("section", "auth-aside");
    aside.appendChild(brand());
    const quote = node("div", "auth-quote");
    quote.append(node("div", "eyebrow", "First, secure the desk"), node("h1", "", "A private inbox starts here."));
    quote.appendChild(node("p", "", "The staff password is not configured yet. The workspace stays closed until a secure sign-in exists."));
    aside.append(quote, node("div", "auth-aside-foot", "No customer data is shown until setup"));
    const main = node("section", "auth-main");
    const card = node("div", "auth-card");
    card.append(node("div", "page-kicker", "Replit Secrets"), node("h2", "", "Complete staff setup"), node("p", "", "Add the required secrets in your project's Secrets panel. Refresh this page when complete."));
    const instructions = node("div", "auth-note");
    instructions.textContent = "Create STAFF_PASSWORD with at least 12 characters and SESSION_SECRET with a long, random value. Keep both private; neither belongs in this page.";
    card.appendChild(instructions);
    const retry = button("Check setup again", "primary-button", null, start);
    card.appendChild(retry);
    main.appendChild(card);
    shell.append(aside, main);
    app.appendChild(shell);
  }
  function renderTopbar() {
    refs.lastEvent = null;
    const top = node("header", "topbar");
    top.appendChild(brand(() => setMode("home")));
    const status = node("div", "topbar-status");
    const right = node("nav", "topbar-right");
    right.setAttribute("aria-label", "Workspace tools");
    refs.navigation = right;
    const connection = node("div", `connection${state.pollError ? " offline" : ""}`);
    const connectionLabel = state.pollError ? "Updating paused" : "Connected";
    connection.setAttribute("aria-label", connectionLabel);
    connection.title = connectionLabel;
    const dot = node("span", "connection-dot");
    const label = node("span", "", connectionLabel);
    connection.append(dot, label);
    if (state.status && state.status.last_event_at) {
      refs.lastEvent = node("span", "top-label", `Last webhook ${shortTime(state.status.last_event_at)}`);
      status.appendChild(refs.lastEvent);
    }
    status.append(connection);
    [
      ["home", "Workspace home", "Home", "home", "Open the workspace home screen"],
      ["quotes", "Compare quotations", "Compare quotes", "list", "Compare quotations by price and delivery time"],
      ["register", "Quotation register", "Quote register", "download", "View saved quotations and download an Excel list"],
      ["chat", "Quote AI chat", "AI chat", "assistant", "Ask about saved comparisons and download answers as PDF"],
      ["checklist", "Configuration checklist", "Setup checklist", "list", "Check WhatsApp and AI configuration"],
      ["settings", "Settings", "Settings", "settings", "Manage business numbers, import history, and clear saved messages"]
    ].forEach(([mode, accessibleLabel, label, glyph, description]) => {
      const item = button(accessibleLabel, "icon-button", glyph, () => setMode(mode));
      item.appendChild(node("span", "nav-label", label));
      item.dataset.tooltip = accessibleLabel;
      item.setAttribute("aria-description", description);
      item.dataset.mode = mode;
      right.appendChild(item);
    });
    const logout = node("button", "quiet-button", "Sign out");
    logout.type = "button";
    logout.addEventListener("click", logoutStaff);
    right.appendChild(logout);
    top.append(status, right);
    return top;
  }
  function makeWorkspace() {
    app.replaceChildren();
    const shell = node("div", "app-shell");
    shell.appendChild(renderTopbar());
    const workspace = node("div", "workspace");
    const sidebar = node("aside", "sidebar");
    const head = node("div", "sidebar-head");
    head.appendChild(node("div", "eyebrow", "Customer care"));
    const titleRow = node("div", "sidebar-title-row");
    titleRow.appendChild(node("h1", "", "Inbox"));
    titleRow.appendChild(button("New conversation", "new-button", "plus", () => setMode("new")));
    head.appendChild(titleRow);
    sidebar.appendChild(head);
    const search = node("div", "search-wrap");
    search.appendChild(icon("search"));
    const searchInput = node("input", "search-input");
    searchInput.type = "search";
    searchInput.placeholder = state.messageSearchActive ? "Search saved message text" : "Find a conversation";
    searchInput.setAttribute("aria-label", state.messageSearchActive ? "Search saved message text" : "Search conversations");
    searchInput.value = state.search;
    refs.searchInput = searchInput;
    searchInput.addEventListener("input", () => {
      state.search = searchInput.value;
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(
        state.messageSearchActive ? searchMessageHistory : refreshConversations, 250);
    });
    search.appendChild(searchInput);
    sidebar.appendChild(search);
    refs.searchModeButton = button(
      state.messageSearchActive ? "Back to chats" : "Search messages",
      "secondary-button history-search-toggle", null, () => {
        state.messageSearchActive = !state.messageSearchActive;
        state.search = "";
        state.messageSearchResults = [];
        state.messageSearchError = "";
        if (refs.searchInput) {
          refs.searchInput.value = "";
          refs.searchInput.placeholder = state.messageSearchActive
            ? "Search saved message text" : "Find a conversation";
          refs.searchInput.setAttribute("aria-label", state.messageSearchActive
            ? "Search saved message text" : "Search conversations");
        }
        refs.searchModeButton.textContent = state.messageSearchActive
          ? "Back to chats" : "Search messages";
        if (state.messageSearchActive) renderConversationList();
        else refreshConversations();
      });
    sidebar.appendChild(refs.searchModeButton);
    const meta = node("div", "inbox-meta");
    refs.count = node("span", "", "— conversations");
    refs.syncLabel = node("span", "", "Syncing");
    meta.append(refs.count, refs.syncLabel);
    sidebar.appendChild(meta);
    refs.listError = node("div", "inline-error hidden");
    sidebar.appendChild(refs.listError);
    refs.listScroll = node("div", "list-scroll");
    refs.conversationList = node("div", "conversation-list");
    refs.listScroll.appendChild(refs.conversationList);
    sidebar.appendChild(refs.listScroll);
    refs.sidebar = sidebar;
    refs.workspace = workspace;
    refs.main = node("section", "inbox-main");
    workspace.append(sidebar, refs.main);
    shell.appendChild(workspace);
    app.appendChild(shell);
    renderConversationList();
  }
  function renderConversationList() {
    if (!refs.conversationList) return;
    refs.conversationList.replaceChildren();
    if (state.messageSearchActive) {
      const query = state.search.trim();
      refs.count.textContent = state.messageSearchLoading
        ? "Searching messages…" : `${state.messageSearchResults.length} message matches`;
      if (query.length < 2) {
        refs.conversationList.appendChild(node("div", "list-empty",
          "Type at least two characters to search saved message text."));
        return;
      }
      if (state.messageSearchError) {
        const error = node("div", "list-empty", state.messageSearchError);
        const retry = button("Try again", "retry-media", null, searchMessageHistory);
        error.appendChild(retry);
        refs.conversationList.appendChild(error);
        return;
      }
      if (state.messageSearchLoading) {
        refs.conversationList.appendChild(node("div", "list-empty", "Searching the saved chat history…"));
        return;
      }
      if (!state.messageSearchResults.length) {
        refs.conversationList.appendChild(node("div", "list-empty", "No saved messages match that text."));
        return;
      }
      for (const result of state.messageSearchResults) {
        const item = node("button", "history-result");
        item.type = "button";
        item.append(
          node("span", "history-result-name", result.name || result.wa_id || "Unknown customer"),
          node("span", "history-result-preview",
            `${result.direction === "outbound" ? "You" : "Client"} · ${result.snippet}`),
          node("span", "history-result-time", shortTime(result.timestamp))
        );
        item.addEventListener("click", () => {
          state.messageSearchActive = false;
          state.search = "";
          if (refs.searchInput) {
            refs.searchInput.value = "";
            refs.searchInput.placeholder = "Find a conversation";
            refs.searchInput.setAttribute("aria-label", "Search conversations");
          }
          if (refs.searchModeButton) refs.searchModeButton.textContent = "Search messages";
          state.visibleConversations = null;
          selectConversation(result.conversation_id, {
            timestamp: result.timestamp, seq: result.seq, messageId: result.message_id
          });
        });
        refs.conversationList.appendChild(item);
      }
      return;
    }
    const items = state.visibleConversations === null ? state.conversations : state.visibleConversations;
    refs.count.textContent = `${items.length} conversation${items.length === 1 ? "" : "s"}`;
    if (!items.length && !state.listError) {
      refs.conversationList.appendChild(node("div", "list-empty", state.search ? "No conversation matches that search. Try another name or number." : "Your inbox is clear. New customer conversations will appear here."));
      return;
    }
    for (const conversation of items) {
      const item = node("button", `conversation-item${String(conversation.id) === String(state.selectedId) ? " selected" : ""}`);
      item.type = "button";
      item.dataset.id = String(conversation.id);
      const avatar = node("span", "avatar", initials(conversation.name || conversation.wa_id));
      const copy = node("span", "conversation-copy");
      const nameLine = node("span", "conversation-name-line");
      nameLine.appendChild(node("span", "conversation-name", conversation.name || conversation.wa_id || "Unknown customer"));
      const mark = node("span", `window-mark${conversation.window_open ? "" : " closed"}`);
      mark.title = conversation.window_open ? "Reply window open" : "Reply window closed";
      nameLine.appendChild(mark);
      const last = node("span", "conversation-last", conversation.last_message || "No messages yet");
      copy.append(nameLine, last);
      const aside = node("span", "conversation-side");
      aside.appendChild(node("span", "conversation-time", shortTime(conversation.last_message_at)));
      if (Number(conversation.unread_count) > 0) aside.appendChild(node("span", "unread-badge", Number(conversation.unread_count) > 99 ? "99+" : conversation.unread_count));
      item.append(avatar, copy, aside);
      item.addEventListener("click", () => selectConversation(conversation.id));
      refs.conversationList.appendChild(item);
    }
  }
  function showListError(message) {
    state.listError = message;
    state.pollIssues.conversations = message;
    if (refs.listError) {
      refs.listError.textContent = `${message} `;
      refs.listError.classList.remove("hidden");
      const retry = node("button", "retry-media", "Retry");
      retry.addEventListener("click", refreshConversations);
      refs.listError.appendChild(retry);
    }
    renderConversationList();
  }
  function clearListError() {
    state.listError = "";
    delete state.pollIssues.conversations;
    if (refs.listError) {
      refs.listError.classList.add("hidden");
      refs.listError.replaceChildren();
    }
  }
  function renderInbox() {
    state.mode = "inbox";
    refs.workspace.classList.toggle("chat-open", Boolean(state.selectedId));
    refs.main.replaceChildren();
    const conversation = selectedConversation();
    if (!conversation) {
      const panel = node("div", "state-panel");
      const card = node("div", "state-card");
      const mark = node("div", "state-mark");
      mark.appendChild(icon("chat"));
      card.append(mark, node("h2", "", "A clear line to your customers"), node("p", "", state.conversations.length ? "Choose a conversation to see the full thread and reply." : "When a customer messages your business, the conversation will appear here."));
      const actions = node("div", "state-actions");
      actions.appendChild(button("Start a conversation", "primary-button", null, () => setMode("new")));
      card.appendChild(actions);
      panel.appendChild(card);
      refs.main.appendChild(panel);
      return;
    }
    const head = node("header", "chat-head");
    const person = node("div", "chat-person");
    person.appendChild(button("Back to conversations", "icon-button back-mobile", "back", () => {
      refs.workspace.classList.remove("chat-open");
      state.selectedId = null;
      renderConversationList();
    }));
    person.appendChild(node("span", "avatar", initials(conversation.name || conversation.wa_id)));
    const identity = node("div", "");
    identity.append(node("div", "chat-person-name", conversation.name || conversation.wa_id || "Unknown customer"),
      node("div", "chat-person-sub", `${conversation.wa_id || ""}${conversation.display_phone_number ? ` · via ${conversation.display_phone_number}` : ""}`));
    person.appendChild(identity);
    head.appendChild(person);
    const actions = node("div", "chat-actions");
    const pill = node("span", `window-pill${windowIsOpen(conversation) ? "" : " closed"}`, windowIsOpen(conversation) ? "24h reply window open" : "Window closed");
    refs.windowPill = pill;
    actions.append(pill, button("Refresh conversation", "icon-button", "refresh", () => refreshMessages(true)));
    actions.appendChild(button("Clear chat history", "danger-button", null,
      () => showHistoryDeletion(conversation)));
    head.appendChild(actions);
    refs.main.appendChild(head);
    refs.pollNotice = node("div", "inline-error hidden");
    refs.pollNotice.style.margin = "9px 20px 0";
    refs.main.appendChild(refs.pollNotice);
    refs.messageScroll = node("div", "message-scroll");
    refs.messageStack = node("div", "message-stack");
    refs.messageScroll.appendChild(refs.messageStack);
    refs.main.appendChild(refs.messageScroll);
    refs.composer = buildComposer(conversation);
    refs.main.appendChild(refs.composer);
    renderMessages();
    if (!state.initialMessagesLoaded) {
      refs.messageStack.appendChild(makeSkeletonMessages());
    } else {
      scrollToBottom();
    }
  }
  function makeSkeletonMessages() {
    const group = node("div", "");
    group.style.width = "100%";
    for (let i = 0; i < 4; i += 1) {
      const row = node("div", "skeleton-row");
      const a = node("div", "skeleton skeleton-avatar");
      const lines = node("div", "skeleton-lines");
      const one = node("div", "skeleton skeleton-line");
      one.style.width = `${i % 2 ? 55 : 75}%`;
      const two = node("div", "skeleton skeleton-line");
      two.style.width = `${i % 2 ? 35 : 48}%`;
      lines.append(one, two);
      row.append(a, lines);
      group.appendChild(row);
    }
    return group;
  }
  function buildComposer(conversation) {
    const wrap = node("div", "composer");
    const inner = node("div", "composer-inner");
    const isOpen = windowIsOpen(conversation);
    const pendingForConversation = state.pendingSend && String(state.pendingSend.conversationId) === String(conversation.id);
    const pendingElsewhere = state.pendingSend && !pendingForConversation;
    if (!isOpen) {
      const warning = node("div", "composer-alert", "A 24-hour reply window is not open. An approved WhatsApp template is required to start or resume this conversation; template sending is not available here.");
      inner.appendChild(warning);
    }
    if (pendingForConversation) {
      const retry = node("div", "composer-alert", `The last send did not confirm: ${state.pendingSend.error} `);
      const retryButton = node("button", "retry-media", "Retry same message");
      retryButton.addEventListener("click", () => sendMessage(true));
      retry.appendChild(retryButton);
      inner.appendChild(retry);
    } else if (pendingElsewhere) {
      const retry = node("div", "composer-alert", "A previous send needs confirmation before another message can be sent. ");
      const open = node("button", "retry-media", "Open that conversation");
      open.addEventListener("click", () => selectConversation(state.pendingSend.conversationId));
      retry.appendChild(open);
      inner.appendChild(retry);
    }
    const box = node("form", "composer-box");
    const input = node("textarea", "composer-input");
    input.rows = 1;
    input.maxLength = 4096;
    input.placeholder = isOpen ? "Write a reply…" : "Free-form replies are unavailable";
    input.disabled = !isOpen || Boolean(state.pendingSend);
    input.value = state.drafts.get(String(conversation.id)) || "";
    input.setAttribute("aria-label", "Message");
    input.addEventListener("input", () => {
      state.drafts.set(String(conversation.id), input.value);
      count.textContent = `${input.value.length}/4096`;
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
      send.disabled = !input.value.trim() || state.sending || Boolean(state.pendingSend);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!send.disabled) sendMessage(false);
      }
    });
    const count = node("span", "char-count", `${input.value.length}/4096`);
    const send = button("Send message", "send-button", "send", () => sendMessage(false));
    send.type = "submit";
    send.disabled = !input.value.trim() || state.sending || Boolean(state.pendingSend);
    box.addEventListener("submit", (event) => { event.preventDefault(); sendMessage(false); });
    box.append(input, count, send);
    inner.appendChild(box);
    const foot = node("div", "composer-foot");
    foot.append(node("span", "", "Enter to send · Shift + Enter for a new line"), node("span", "", "Up to 4,096 characters"));
    inner.appendChild(foot);
    wrap.appendChild(inner);
    refs.composerInput = input;
    refs.sendButton = send;
    refs.composerWindowOpen = isOpen;
    refs.composer = wrap;
    return wrap;
  }
  function syncSelectedConversationChrome() {
    if (state.mode !== "inbox" || state.selectedId === null) return;
    const conversation = selectedConversation();
    if (!conversation) return;
    const open = windowIsOpen(conversation);
    if (refs.windowPill) {
      refs.windowPill.classList.toggle("closed", !open);
      refs.windowPill.textContent = open ? "24h reply window open" : "Window closed";
    }
    if (refs.composer && refs.composerWindowOpen !== open) {
      if (refs.composerInput) state.drafts.set(String(conversation.id), refs.composerInput.value);
      const updatedComposer = buildComposer(conversation);
      refs.composer.replaceWith(updatedComposer);
    }
  }
  function renderMessages() {
    if (!refs.messageStack) return;
    const older = refs.messageStack.querySelector(".load-older");
    const oldTop = older && older.parentElement === refs.messageStack ? older : null;
    refs.messageStack.replaceChildren();
    if (state.hasMore) {
      const load = node("button", "load-older", state.loadingMessages ? "Loading earlier messages…" : "Load earlier messages");
      load.type = "button";
      load.disabled = state.loadingMessages;
      load.addEventListener("click", loadOlder);
      refs.messageStack.appendChild(load);
    }
    let previousDay = "";
    for (const message of state.messages) {
      const date = toDate(message.timestamp);
      const day = !date ? "Date unavailable" : date.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
      if (day !== previousDay) {
        refs.messageStack.appendChild(node("div", "day-divider", day));
        previousDay = day;
      }
      refs.messageStack.appendChild(renderMessage(message));
    }
    if (oldTop && !state.hasMore) oldTop.remove();
  }
  function typeKnown(type) {
    return ["text", "image", "sticker", "audio", "voice", "video", "document"].includes(String(type || "").toLowerCase());
  }
  function renderMessage(message) {
    const outbound = message.direction === "outbound";
    const row = node("article", `message-row${outbound ? " outgoing" : ""}${state.highlightMessageId === message.id ? " search-hit" : ""}`);
    row.dataset.messageId = String(message.id);
    const bubble = node("div", "bubble");
    const type = String(message.type || "unknown").toLowerCase();
    if (!typeKnown(type)) {
      const label = node("div", "unknown-label");
      label.append(node("span", "unknown-symbol", "?"), node("span", "", `${message.type || "Unrecognized"} message`));
      bubble.appendChild(label);
      const readable = message.body || message.caption || `This message uses an unsupported format (${message.type || "unknown"}).`;
      bubble.appendChild(node("div", "message-text", readable));
      const details = node("details", "json-details");
      details.appendChild(node("summary", "", "View raw message details"));
      const pre = node("pre", "");
      try { pre.textContent = JSON.stringify(message.raw ?? {}, null, 2); } catch (_) { pre.textContent = "Raw details are unavailable."; }
      details.appendChild(pre);
      bubble.appendChild(details);
    } else {
      if (message.body) bubble.appendChild(node("div", "message-text", message.body));
      const mediaUrl = sameOriginMedia(message.media_url);
      if (["image", "sticker", "audio", "voice", "video", "document"].includes(type)) {
        const media = node("div", "media-card");
        const filename = message.filename || `${type} attachment`;
        if ((type === "image" || type === "sticker") && mediaUrl) {
          const image = document.createElement("img");
          image.className = "media-preview";
          image.src = mediaUrl;
          image.alt = message.caption || filename;
          image.loading = "lazy";
          media.appendChild(image);
        } else if ((type === "audio" || type === "voice") && mediaUrl) {
          const audio = document.createElement("audio");
          audio.className = "audio-player";
          audio.controls = true;
          audio.preload = "none";
          audio.src = mediaUrl;
          audio.setAttribute("aria-label", "Voice message");
          media.appendChild(audio);
        } else if (type === "video" && mediaUrl) {
          const video = document.createElement("video");
          video.className = "video-player";
          video.controls = true;
          video.preload = "metadata";
          video.src = mediaUrl;
          video.setAttribute("aria-label", filename);
          media.appendChild(video);
        } else if (type === "document" || (!mediaUrl && type !== "image" && type !== "sticker")) {
          const file = node("div", "file-line");
          file.append(node("span", "file-icon", (message.mime_type || "").split("/").pop()?.slice(0, 5).toUpperCase() || "FILE"));
          const desc = node("span", "");
          desc.append(node("div", "file-name", filename), node("div", "file-type", message.mime_type || type));
          file.appendChild(desc);
          media.appendChild(file);
        }
        if (message.caption) media.appendChild(node("div", "message-caption", message.caption));
        if (mediaUrl) {
          const link = document.createElement("a");
          link.className = "media-link";
          link.href = sameOriginMedia(message.media_url, true);
          link.append(icon("download"), document.createTextNode("Download"));
          link.setAttribute("download", "");
          media.appendChild(link);
        } else if (message.attachment_state === "failed" || message.attachment_error) {
          const problem = node("div", "media-error", message.attachment_error || "Attachment could not be saved.");
          const retry = node("button", "retry-media", "Retry download");
          retry.addEventListener("click", () => retryMedia(message.id, retry));
          problem.appendChild(retry);
          media.appendChild(problem);
        } else {
          const pendingText = message.attachment_state === "downloading" ? "Saving attachment…" : "Attachment is waiting to be saved.";
          media.appendChild(node("div", "media-error", pendingText));
          if (message.attachment_state === "pending") {
            const retry = node("button", "retry-media", "Save now");
            retry.addEventListener("click", () => retryMedia(message.id, retry));
            media.appendChild(retry);
          }
        }
        bubble.appendChild(media);
      }
      if (message.caption && !["image", "sticker", "audio", "voice", "video", "document"].includes(type)) {
        bubble.appendChild(node("div", "message-caption", message.caption));
      }
    }
    const meta = node("div", "message-meta");
    meta.appendChild(node("time", "", shortTime(message.timestamp)));
    if (message.status === "history") {
      meta.appendChild(node("span", "message-status", "Imported"));
    } else if (outbound) {
      const status = String(message.status || "sending").toLowerCase();
      const statusNode = node("span", `message-status${status === "failed" ? " failed" : ""}${status === "accepted" ? " accepted" : ""}`, statusLabel(status));
      if (status === "failed" && message.error) {
        statusNode.title = message.error;
        statusNode.setAttribute("aria-label", `${statusLabel(status)}: ${message.error}`);
      }
      meta.appendChild(statusNode);
      if (status === "failed" && message.error) {
        meta.appendChild(node("span", "delivery-error", message.error));
      }
    }
    bubble.appendChild(meta);
    row.appendChild(bubble);
    return row;
  }
  function statusLabel(status) {
    const labels = { sending: "Sending", accepted: "Accepted", sent: "Sent", delivered: "Delivered", read: "Read", failed: "Failed" };
    return labels[status] || "Status unknown";
  }
  function scrollToBottom() {
    if (refs.messageScroll) refs.messageScroll.scrollTop = refs.messageScroll.scrollHeight;
  }
  async function selectConversation(id, match = null) {
    const current = state.selectedId;
    if (current !== null && refs.composerInput) state.drafts.set(String(current), refs.composerInput.value);
    state.selectedId = id;
    state.messages = [];
    state.hasMore = false;
    state.beforeSeq = null;
    state.beforeTimestamp = null;
    state.loadingMessages = false;
    state.openAround = match ? { timestamp: Number(match.timestamp), seq: Number(match.seq) } : null;
    state.highlightMessageId = match ? String(match.messageId) : null;
    state.initialMessagesLoaded = false;
    state.historyRevision = null;
    renderConversationList();
    renderInbox();
    await fetchMessages(true);
    if (!state.authenticated) return;
    request(`/api/conversations/${encodeURIComponent(id)}/read`, { method: "POST", body: {} }).then(() => {
      const conv = state.conversations.find((item) => String(item.id) === String(id));
      if (conv) conv.unread_count = 0;
      renderConversationList();
    }).catch((err) => {
      if (state.authenticated) toast(`Could not mark conversation read: ${err.message}`, true);
    });
  }
  async function fetchMessages(isInitial = false) {
    if (state.selectedId === null) return;
    const selectedId = state.selectedId;
    const epoch = state.historyEpoch;
    const around = state.openAround;
    const params = new URLSearchParams();
    if (around) {
      params.set("around_timestamp", String(around.timestamp));
      params.set("around_seq", String(around.seq));
    }
    const query = params.toString();
    const url = `/api/conversations/${encodeURIComponent(selectedId)}/messages${query ? `?${query}` : ""}`;
    try {
      const data = await request(url);
      if (String(state.selectedId) !== String(selectedId) || epoch !== state.historyEpoch) return;
      const revision = Number(data.history_revision || 0);
      if (state.historyRevision !== null && revision < state.historyRevision) return;
      if (state.historyRevision !== null && revision !== state.historyRevision) state.messages = [];
      state.historyRevision = revision;
      if (state.openAround === around) state.openAround = null;
      const wasNearBottom = refs.messageScroll && (refs.messageScroll.scrollHeight - refs.messageScroll.scrollTop - refs.messageScroll.clientHeight < 90);
      const existing = new Map(state.messages.map((message) => [String(message.id), message]));
      for (const message of (data.messages || [])) existing.set(String(message.id), { ...existing.get(String(message.id)), ...message });
      state.messages = Array.from(existing.values()).sort((a, b) =>
        Number(a.timestamp || 0) - Number(b.timestamp || 0) || Number(a.seq || 0) - Number(b.seq || 0));
      state.hasMore = Boolean(data.has_more);
      state.beforeTimestamp = data.next_before?.timestamp ?? null;
      state.beforeSeq = data.next_before?.seq ?? null;
      state.initialMessagesLoaded = true;
      delete state.pollIssues.messages;
      if (refs.main && state.mode === "inbox") {
        renderMessages();
        if (isInitial && state.highlightMessageId) {
          const hit = Array.from(refs.messageStack.querySelectorAll(".message-row"))
            .find((item) => item.dataset.messageId === state.highlightMessageId);
          if (hit) {
            hit.scrollIntoView({ block: "center" });
            state.highlightMessageId = null;
          } else scrollToBottom();
        } else if (wasNearBottom || isInitial) scrollToBottom();
      }
    } catch (err) {
      if (String(state.selectedId) !== String(selectedId)) return;
      if (!state.authenticated) return;
      state.pollIssues.messages = `Messages could not be updated: ${err.message}`;
      updateConnection();
      if (!state.initialMessagesLoaded && refs.messageStack) {
        refs.messageStack.replaceChildren();
        const errorBox = node("div", "list-empty", state.pollError);
        const retry = node("button", "retry-media", "Retry");
        retry.addEventListener("click", () => fetchMessages(true));
        errorBox.appendChild(retry);
        refs.messageStack.appendChild(errorBox);
      } else if (isInitial) toast(state.pollError, true);
    }
  }
  async function refreshMessages(initial = false) {
    if (state.mode === "inbox" && state.selectedId !== null) await fetchMessages(initial);
  }
  async function loadOlder() {
    if (state.loadingMessages || !state.hasMore || state.beforeSeq === null || state.beforeTimestamp === null) return;
    const conversationId = state.selectedId;
    const epoch = state.historyEpoch;
    state.loadingMessages = true;
    const scroll = refs.messageScroll;
    const previousHeight = scroll ? scroll.scrollHeight : 0;
    try {
      const data = await request(`/api/conversations/${encodeURIComponent(conversationId)}/messages?before_timestamp=${encodeURIComponent(state.beforeTimestamp)}&before_seq=${encodeURIComponent(state.beforeSeq)}`);
      if (String(state.selectedId) !== String(conversationId) || epoch !== state.historyEpoch) return;
      if (Number(data.history_revision || 0) !== state.historyRevision) {
        await fetchMessages(true);
        return;
      }
      const known = new Set(state.messages.map((message) => String(message.id)));
      const earlier = (data.messages || []).filter((message) => !known.has(String(message.id)));
      state.messages = [...earlier, ...state.messages].sort((a, b) =>
        Number(a.timestamp || 0) - Number(b.timestamp || 0) || Number(a.seq || 0) - Number(b.seq || 0));
      state.hasMore = Boolean(data.has_more);
      state.beforeTimestamp = data.next_before?.timestamp ?? null;
      state.beforeSeq = data.next_before?.seq ?? null;
      renderMessages();
      if (scroll) scroll.scrollTop += scroll.scrollHeight - previousHeight;
    } catch (err) {
      if (String(state.selectedId) === String(conversationId) && state.authenticated) {
        toast(`Earlier messages could not be loaded: ${err.message}`, true);
      }
    } finally {
      if (String(state.selectedId) === String(conversationId)) {
        state.loadingMessages = false;
        renderMessages();
      }
    }
  }
  async function retryMedia(messageId, buttonEl) {
    if (buttonEl) { buttonEl.disabled = true; buttonEl.textContent = "Queueing…"; }
    try {
      await request(`/api/messages/${encodeURIComponent(messageId)}/retry-media`, { method: "POST", body: {} });
      toast("Attachment download queued.");
      await fetchMessages(false);
    } catch (err) {
      if (state.authenticated) toast(`Could not retry attachment: ${err.message}`, true);
      if (buttonEl) { buttonEl.disabled = false; buttonEl.textContent = "Retry download"; }
    }
  }
  function newClientId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
      const random = Math.floor(Math.random() * 16);
      return (char === "x" ? random : (random & 3) | 8).toString(16);
    });
  }
  async function sendMessage(retry = false) {
    const conversation = selectedConversation();
    if (!conversation || !windowIsOpen(conversation) || state.sending) return;
    if (!retry && state.pendingSend) return;
    const draft = refs.composerInput ? refs.composerInput.value : "";
    if (!retry && !draft.trim()) return;
    if (!retry) {
      state.pendingSend = { conversationId: conversation.id, text: draft, clientId: newClientId(), error: "" };
    }
    if (!state.pendingSend || String(state.pendingSend.conversationId) !== String(conversation.id)) return;
    const pending = state.pendingSend;
    state.sending = true;
    if (refs.composerInput) refs.composerInput.disabled = true;
    if (refs.sendButton) refs.sendButton.disabled = true;
    try {
      const result = await request("/api/messages", {
        method: "POST",
        body: { conversation_id: pending.conversationId, text: pending.text, client_id: pending.clientId }
      });
      const stillSelected = String(state.selectedId) === String(conversation.id);
      if (stillSelected) {
        const existingIndex = state.messages.findIndex((message) => String(message.id) === String(result.message.id));
        if (existingIndex >= 0) {
          state.messages[existingIndex] = { ...state.messages[existingIndex], ...result.message };
        } else {
          state.messages.push(result.message);
        }
        state.messages.sort((a, b) => Number(a.seq || 0) - Number(b.seq || 0));
      }
      const latest = state.conversations.find((item) => String(item.id) === String(conversation.id));
      if (latest) {
        latest.last_message = result.message.body || pending.text;
        latest.last_message_at = result.message.timestamp;
      }
      state.pendingSend = null;
      const messageFailed = String(result.message.status || "").toLowerCase() === "failed";
      if (messageFailed) {
        state.drafts.set(String(conversation.id), pending.text);
        toast(`Message was not delivered: ${result.message.error || "The provider reported a failure."}`, true);
      } else {
        state.drafts.set(String(conversation.id), "");
      }
      state.conversations.sort((a, b) => (toDate(b.last_message_at)?.getTime() || 0) - (toDate(a.last_message_at)?.getTime() || 0));
      renderConversationList();
      if (state.mode === "inbox") {
        renderInbox();
        if (stillSelected) scrollToBottom();
      }
      refreshConversations();
    } catch (err) {
      if (!state.authenticated) return;
      if ([400, 403, 404, 409, 422].includes(err.status)) {
        state.pendingSend = null;
        toast(`Message was not sent: ${err.message}`, true);
        renderInbox();
        return;
      }
      pending.error = err.message;
      state.pendingSend = pending;
      toast(`Message not confirmed. Retry safely; the same request ID will be reused. ${err.message}`, true);
      renderInbox();
    } finally {
      state.sending = false;
      if (refs.sendButton && refs.composerInput && !state.pendingSend) {
        refs.sendButton.disabled = !refs.composerInput.value.trim();
      }
    }
  }
  async function refreshConversations() {
    const query = state.search.trim();
    const requestId = ++state.conversationRequestId;
    try {
      const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
      const data = await request(`/api/conversations${suffix}`);
      if (requestId !== state.conversationRequestId || query !== state.search.trim()) return;
      const results = Array.isArray(data.conversations) ? data.conversations : [];
      if (query) {
        state.visibleConversations = results;
        const allData = await request("/api/conversations");
        if (requestId !== state.conversationRequestId || query !== state.search.trim()) return;
        state.conversations = Array.isArray(allData.conversations) ? allData.conversations : [];
      } else {
        state.conversations = results;
        state.visibleConversations = null;
      }
      state.listError = "";
      clearListError();
      refs.syncLabel.textContent = "Up to date";
      if (state.mode === "home" && window.WorkspaceHome &&
          typeof window.WorkspaceHome.update === "function") {
        window.WorkspaceHome.update({ conversationCount: state.conversations.length });
      }
      renderConversationList();
      syncSelectedConversationChrome();
      updateConnection();
    } catch (err) {
      if (requestId !== state.conversationRequestId || query !== state.search.trim() || !state.authenticated) return;
      refs.syncLabel.textContent = "Update failed";
      showListError(`Conversations could not be refreshed: ${err.message}`);
      state.pollError = err.message;
      updateConnection();
    }
  }
  async function searchMessageHistory() {
    const query = state.search.trim();
    const requestId = ++state.messageSearchRequestId;
    state.messageSearchError = "";
    if (query.length < 2) {
      state.messageSearchResults = [];
      state.messageSearchLoading = false;
      renderConversationList();
      return;
    }
    state.messageSearchLoading = true;
    renderConversationList();
    try {
      const data = await request(`/api/messages/search?q=${encodeURIComponent(query)}`);
      if (requestId !== state.messageSearchRequestId || query !== state.search.trim()) return;
      state.messageSearchResults = Array.isArray(data.results) ? data.results : [];
    } catch (error) {
      if (requestId !== state.messageSearchRequestId || query !== state.search.trim()) return;
      state.messageSearchError = `Message search failed: ${error.message}`;
    } finally {
      if (requestId === state.messageSearchRequestId && query === state.search.trim()) {
        state.messageSearchLoading = false;
        renderConversationList();
      }
    }
  }
  function updateConnection() {
    const issueText = Object.values(state.pollIssues).filter(Boolean).join(" · ");
    state.pollError = issueText;
    const connection = app.querySelector(".connection");
    if (connection) {
      connection.classList.toggle("offline", Boolean(state.pollError));
      const label = connection.querySelector("span:last-child");
      if (label) label.textContent = state.pollError ? "Update problem" : "Connected";
      connection.setAttribute("aria-label", state.pollError ? "Update problem" : "Connected");
      connection.title = state.pollError || "Workspace updates are running.";
    }
    if (refs.pollNotice) {
      refs.pollNotice.textContent = issueText;
      refs.pollNotice.classList.toggle("hidden", !issueText);
    }
    if (refs.lastEvent && state.status && state.status.last_event_at) {
      refs.lastEvent.textContent = `Last webhook ${shortTime(state.status.last_event_at)}`;
    }
  }
  async function poll() {
    if (state.polling || !state.authenticated) return;
    state.polling = true;
    try {
      const [statusResult] = await Promise.all([
        request("/api/status").then((result) => {
          state.status = result;
          state.lastStatusSuccess = Date.now();
          delete state.pollIssues.status;
          return result;
        }),
        refreshConversations()
      ]);
      state.status = statusResult;
      if (state.selectedId !== null && state.mode === "inbox") await fetchMessages(false);
      if (!Object.keys(state.pollIssues).length && refs.syncLabel) refs.syncLabel.textContent = "Up to date";
      updateConnection();
    } catch (err) {
      if (!state.authenticated) return;
      state.pollIssues.status = `Status could not be updated: ${err.message}`;
      updateConnection();
      if (refs.syncLabel) refs.syncLabel.textContent = "Update failed";
    } finally {
      state.polling = false;
    }
  }
  function startPolling() {
    window.clearInterval(pollTimer);
    pollTimer = window.setInterval(poll, 5000);
  }
  function setMode(mode) {
    if (mode !== "quotes" && window.QuoteEvaluator &&
        typeof window.QuoteEvaluator.unmount === "function") {
      window.QuoteEvaluator.unmount();
    }
    if (mode !== "home" && window.WorkspaceHome &&
        typeof window.WorkspaceHome.unmount === "function") {
      window.WorkspaceHome.unmount();
    }
    if (mode !== "register" && window.QuotationRegister &&
        typeof window.QuotationRegister.unmount === "function") {
      window.QuotationRegister.unmount();
    }
    if (mode !== "chat" && window.QuoteChat &&
        typeof window.QuoteChat.unmount === "function") {
      window.QuoteChat.unmount();
    }
    state.mode = mode;
    if (refs.navigation) {
      refs.navigation.querySelectorAll("[data-mode]").forEach((item) => {
        if (item.dataset.mode === mode) item.setAttribute("aria-current", "page");
        else item.removeAttribute("aria-current");
      });
    }
    if (!refs.main) return;
    refs.workspace.classList.toggle("page-mode", mode !== "inbox");
    refs.workspace.classList.toggle("home-mode", mode === "home");
    if (mode !== "inbox") refs.workspace.classList.add("chat-open");
    if (mode === "inbox") renderInbox();
    else if (mode === "home") {
      if (window.WorkspaceHome && typeof window.WorkspaceHome.mount === "function") {
        window.WorkspaceHome.mount({
          container: refs.main,
          onNavigate: setMode,
          conversationCount: state.conversations.length,
          healthStatus: state.status
        });
      } else {
        refs.main.replaceChildren(node("div", "inline-error", "The workspace home screen could not load. Refresh the page and try again."));
      }
    }
    else if (mode === "settings") renderSettings();
    else if (mode === "new") renderNewConversation();
    else if (mode === "checklist") renderChecklist();
    else if (mode === "quotes") {
      if (window.QuoteEvaluator && typeof window.QuoteEvaluator.mount === "function") {
        window.QuoteEvaluator.mount({
          container: refs.main, request, toast, onBack: () => setMode("home")
        });
      } else {
        refs.main.replaceChildren(node("div", "inline-error", "The quotation comparison screen could not load. Refresh the page and try again."));
      }
    } else if (mode === "register") {
      if (window.QuotationRegister && typeof window.QuotationRegister.mount === "function") {
        window.QuotationRegister.mount({
          container: refs.main, request, toast, onBack: () => setMode("home")
        });
      } else {
        refs.main.replaceChildren(node("div", "inline-error", "The quotation register screen could not load. Refresh the page and try again."));
      }
    } else if (mode === "chat") {
      if (window.QuoteChat && typeof window.QuoteChat.mount === "function") {
        window.QuoteChat.mount({
          container: refs.main, request, toast, onBack: () => setMode("home")
        });
      } else {
        refs.main.replaceChildren(node("div", "inline-error", "The Quote AI chat screen could not load. Refresh the page and try again."));
      }
    }
  }
  function pageHeading(kicker, title, intro) {
    refs.main.replaceChildren();
    const page = node("div", "settings-view");
    const mobileBack = node("button", "page-mobile-back", "Back to inbox");
    mobileBack.type = "button";
    mobileBack.addEventListener("click", () => setMode("inbox"));
    page.appendChild(mobileBack);
    page.append(node("div", "page-kicker", kicker), node("h1", "page-title", title), node("p", "page-intro", intro));
    refs.main.appendChild(page);
    return page;
  }
  function backToInboxButton() {
    const back = node("button", "secondary-button", "Back to inbox");
    back.type = "button";
    back.addEventListener("click", () => setMode("inbox"));
    return back;
  }
  function historyImportCard() {
    const card = node("form", "settings-card history-import-card");
    card.append(
      node("h3", "", "Import older chat history"),
      node("p", "", "Import one WhatsApp plain-text chat export (.txt). WhatsApp's Cloud API cannot fetch past chats. Media files are not included in text exports.")
    );
    const fileLabel = node("label", "field-label", "WhatsApp chat export (.txt)");
    fileLabel.htmlFor = "history-export-file";
    const file = node("input", "field-input history-file");
    file.id = "history-export-file";
    file.name = "file";
    file.type = "file";
    file.accept = ".txt,text/plain";
    file.required = true;
    const phoneLabel = node("label", "field-label", "Client's international phone number");
    phoneLabel.htmlFor = "history-client-phone";
    const phone = node("input", "field-input");
    phone.id = "history-client-phone";
    phone.name = "client_phone";
    phone.type = "tel";
    phone.placeholder = "+60123456789";
    phone.autocomplete = "tel";
    phone.required = true;
    phone.maxLength = 25;
    const nameLabel = node("label", "field-label", "Client name (optional)");
    nameLabel.htmlFor = "history-client-name";
    const name = node("input", "field-input");
    name.id = "history-client-name";
    name.name = "client_name";
    name.maxLength = 120;
    name.placeholder = "Name to show in the inbox";
    const businessLabel = node("label", "field-label", "Your name in the export (optional)");
    businessLabel.htmlFor = "history-business-name";
    const business = node("input", "field-input");
    business.id = "history-business-name";
    business.name = "business_name";
    business.maxLength = 120;
    business.placeholder = "Use this to mark your messages as sent";
    const businessHelp = node("p", "field-help",
      "Messages labelled “You” or “Me” are marked as sent automatically.");
    const dateLabel = node("label", "field-label", "Date format for ambiguous dates");
    dateLabel.htmlFor = "history-date-order";
    const dateOrder = document.createElement("select");
    dateOrder.id = "history-date-order";
    dateOrder.name = "date_order";
    dateOrder.className = "field-select";
    for (const [value, label] of [
      ["DMY", "Day / month / year"],
      ["MDY", "Month / day / year"],
      ["YMD", "Year / month / day"]
    ]) {
      const option = node("option", "", label);
      option.value = value;
      dateOrder.appendChild(option);
    }
    const timezoneLabel = node("label", "field-label", "Time zone of the export");
    timezoneLabel.htmlFor = "history-timezone";
    const timezone = node("input", "field-input");
    timezone.id = "history-timezone";
    timezone.name = "timezone";
    timezone.maxLength = 80;
    timezone.required = true;
    timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kuala_Lumpur";
    timezone.placeholder = "Asia/Kuala_Lumpur";
    const details = node("p", "field-help",
      "Use a one-to-one chat export in UTF-8, up to 8 MB. This imports text only; photos and files are not extracted. It does not send messages or reopen WhatsApp's 24-hour reply window.");
    const error = node("div", "inline-error hidden");
    error.setAttribute("role", "alert");
    const status = node("p", "save-note history-import-status", "");
    const actions = node("div", "form-actions");
    const submit = node("button", "primary-button", "Import chat history");
    submit.type = "submit";
    actions.append(status, submit);
    card.append(fileLabel, file, phoneLabel, phone, nameLabel, name, businessLabel, business,
      businessHelp, dateLabel, dateOrder, timezoneLabel, timezone, details, error, actions);
    card.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!file.files || !file.files[0]) {
        error.textContent = "Choose a WhatsApp .txt chat export.";
        error.classList.remove("hidden");
        return;
      }
      error.classList.add("hidden");
      status.textContent = "Importing…";
      submit.disabled = true;
      const data = new FormData(card);
      try {
        const result = await request("/api/history/import", { method: "POST", body: data });
        const skipped = Number(result.duplicates_skipped || 0);
        status.textContent = `Imported ${result.messages_imported} message${result.messages_imported === 1 ? "" : "s"}` +
          (skipped ? `; skipped ${skipped} duplicate${skipped === 1 ? "" : "s"}.` : ".");
        if (Number(result.skipped_lines)) {
          status.textContent += ` Ignored ${result.skipped_lines} export line${result.skipped_lines === 1 ? "" : "s"} that were not messages.`;
        }
        file.value = "";
        await refreshConversations();
        state.messageSearchActive = false;
        state.search = "";
        state.visibleConversations = null;
        if (refs.searchInput) {
          refs.searchInput.value = "";
          refs.searchInput.placeholder = "Find a conversation";
          refs.searchInput.setAttribute("aria-label", "Search conversations");
        }
        if (refs.searchModeButton) refs.searchModeButton.textContent = "Search messages";
        setMode("inbox");
        await selectConversation(result.conversation_id);
        toast("Chat history imported. No WhatsApp messages were sent.");
      } catch (err) {
        status.textContent = "";
        error.textContent = err.message;
        error.classList.remove("hidden");
      } finally {
        submit.disabled = false;
      }
    });
    return card;
  }
  async function renderSettings() {
    const page = pageHeading("Workspace controls", "Settings", "Choose the business number, import older chat exports, and manage the desk. Secrets stay in Replit.");
    page.appendChild(historyImportCard());
    const loading = node("div", "skeleton skeleton-line");
    loading.style.width = "100%";
    loading.style.height = "120px";
    page.appendChild(loading);
    const actions = node("div", "state-actions");
    actions.appendChild(backToInboxButton());
    page.appendChild(actions);
    try {
      const [settings, status] = await Promise.all([request("/api/settings"), request("/api/status")]);
      if (state.mode !== "settings") return;
      state.settings = settings;
      state.status = status;
      loading.remove();
      const grid = node("div", "settings-grid");
      const formCard = node("form", "settings-card");
      formCard.append(node("h3", "", "Sending number"), node("p", "", "Set the default business phone number ID used for new outbound messages."));
      const label = node("label", "field-label", "Business phone number ID");
      label.htmlFor = "phone-number-id";
      const select = document.createElement("select");
      select.className = "field-select";
      select.id = "phone-number-id";
      const numbers = Array.isArray(status.phone_numbers) ? status.phone_numbers : [];
      if (numbers.length) {
        const prompt = node("option", "", "Choose a configured number");
        prompt.value = "";
        select.appendChild(prompt);
        for (const phone of numbers) {
          const option = node("option", "", `${phone.display_phone_number || phone.id}${phone.verified_name ? ` · ${phone.verified_name}` : ""}`);
          option.value = String(phone.id);
          select.appendChild(option);
        }
        select.value = String(settings.phone_number_id || status.default_phone_number_id || "");
      } else {
        const option = node("option", "", settings.phone_number_id || "Enter ID below");
        option.value = settings.phone_number_id || "";
        select.appendChild(option);
        select.disabled = true;
      }
      const manualLabel = node("label", "field-label", numbers.length ? "Or enter ID manually" : "Phone number ID");
      manualLabel.htmlFor = "phone-number-id-manual";
      const manual = node("input", "field-input");
      manual.id = "phone-number-id-manual";
      manual.inputMode = "numeric";
      manual.pattern = "[0-9]+";
      manual.placeholder = "Digits only";
      manual.value = numbers.length ? "" : (settings.phone_number_id || status.default_phone_number_id || "");
      const help = node("p", "field-help", "Find the phone number ID in Meta App Dashboard → WhatsApp → API Setup. This is an ID, not the displayed phone number.");
      const saveLine = node("div", "form-actions");
      const save = node("button", "primary-button", "Save default");
      save.type = "submit";
      const saveStatus = node("span", "save-note", "");
      saveLine.append(saveStatus, save);
      formCard.append(label, select, manualLabel, manual, help, saveLine);
      formCard.addEventListener("submit", async (event) => {
        event.preventDefault();
        const chosen = manual.value.trim() || select.value.trim();
        if (!/^\d+$/.test(chosen)) {
          saveStatus.textContent = "Enter the phone number ID using digits only.";
          saveStatus.style.color = "hsl(7 77% 47%)";
          return;
        }
        save.disabled = true;
        saveStatus.textContent = "Saving…";
        try {
          const saved = await request("/api/settings", { method: "POST", body: { phone_number_id: chosen } });
          state.settings = saved;
          saveStatus.textContent = "Default number saved.";
          saveStatus.style.color = "";
          toast("Settings saved.");
        } catch (err) {
          saveStatus.textContent = err.message;
          saveStatus.style.color = "hsl(7 77% 47%)";
        } finally { save.disabled = false; }
      });
      const infoCard = node("section", "settings-card");
      infoCard.append(node("h3", "", "Connection details"), node("p", "", "Keep Meta's webhook pointed at this application and verify signed events."));
      const hook = node("div", "info-block");
      hook.append(node("strong", "", "Webhook URL"));
      hook.appendChild(node("p", "", "Use your site's origin plus this path in Meta webhook settings."));
      hook.appendChild(node("span", "mono-value", `${window.location.origin}/webhook`));
      const secret = node("div", "info-block");
      secret.append(node("strong", "", "Webhook verification"));
      secret.appendChild(node("p", "", "WEBHOOK_VERIFY_TOKEN must match the value configured in Meta. META_APP_SECRET is required to verify signed webhook POSTs. Never paste either secret here."));
      const configNote = node("div", "info-block");
      configNote.append(node("strong", "", "Configuration is not verification"));
      configNote.appendChild(node("p", "", "The indicators below show whether required values are configured; they do not confirm that Meta accepted the credentials or that webhook delivery is working."));
      infoCard.append(hook, secret, configNote);
      grid.append(formCard, infoCard);
      page.appendChild(grid);
      const checks = node("div", "status-list");
      const items = [
        ["Webhook verify token", status.webhook_token_configured],
        ["Meta app secret", status.app_secret_configured],
        ["Access token", status.access_token_configured]
      ];
      for (const [labelText, ready] of items) {
        const item = node("div", "status-item");
        item.append(node("span", `status-check${ready ? "" : " missing"}`, ready ? "✓" : "!"),
          node("span", "", `${labelText}: ${ready ? "configured" : "not configured"}`));
        checks.appendChild(item);
      }
      page.appendChild(checks);
      const cleanup = node("section", "settings-card history-controls");
      cleanup.append(node("h3", "", "Delete local history"),
        node("p", "", "Clear message history for every chat, optionally including saved attachments. This affects every employee. It does not delete messages from WhatsApp. Contacts, settings and the actual reply window are kept."));
      cleanup.appendChild(button("Clear entire inbox history", "danger-button", null,
        () => showHistoryDeletion()));
      page.appendChild(cleanup);
    } catch (err) {
      loading.remove();
      const error = node("div", "inline-error", `Settings could not be loaded: ${err.message}`);
      const retry = node("button", "retry-media", "Retry");
      retry.addEventListener("click", renderSettings);
      error.appendChild(retry);
      page.appendChild(error);
    }
  }
  function showHistoryDeletion(conversation = null) {
    if (state.sending) {
      toast("Wait for your message to finish sending before clearing history.", true);
      return;
    }
    const dialog = node("dialog", "history-dialog");
    dialog.setAttribute("aria-labelledby", "history-delete-title");
    const form = node("form", "");
    const title = node("h2", "", conversation ? "Clear this chat's history?" : "Clear the entire inbox history?");
    title.id = "history-delete-title";
    const target = conversation ? (conversation.name || conversation.wa_id) : "ALL chats";
    form.append(title, node("p", "", `This permanently removes the local message history and readable log entries for ${target}, for every employee. This cannot be undone here and does not delete messages from WhatsApp. Pending attachment downloads are cancelled.`));
    const attachmentLabel = node("label", "history-option");
    const attachments = node("input", "");
    attachments.type = "checkbox";
    attachmentLabel.append(attachments, document.createTextNode("Also permanently delete saved attachments"));
    form.append(attachmentLabel, node("p", "history-hint", "Leave unchecked to keep downloaded files in the archive folder. Their messages and download links will still be removed. A later cleanup can delete retained files. Contacts, settings and reply windows stay unchanged."));
    const phrase = conversation ? "DELETE" : "DELETE ALL";
    const label = node("label", "field-label", `Type ${phrase} to confirm`);
    label.htmlFor = "history-confirmation";
    const confirmation = node("input", "field-input");
    confirmation.id = "history-confirmation";
    confirmation.autocomplete = "off";
    confirmation.required = true;
    const error = node("p", "inline-error hidden");
    error.setAttribute("role", "alert");
    const actions = node("div", "state-actions");
    const cancel = button("Cancel", "secondary-button", null, () => dialog.close());
    const submit = node("button", "danger-button", "Delete history");
    submit.type = "submit";
    submit.disabled = true;
    confirmation.addEventListener("input", () => { submit.disabled = confirmation.value !== phrase; });
    actions.append(cancel, submit);
    form.append(label, confirmation, error, actions);
    dialog.appendChild(form);
    let busy = false;
    dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
    dialog.addEventListener("close", () => dialog.remove());
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy || confirmation.value !== phrase) return;
      busy = true;
      submit.disabled = cancel.disabled = attachments.disabled = confirmation.disabled = true;
      submit.textContent = "Deleting…";
      error.classList.add("hidden");
      try {
        const url = conversation ? `/api/conversations/${encodeURIComponent(conversation.id)}/history` : "/api/history";
        const result = await request(url, { method: "DELETE",
          body: { confirmation: phrase, delete_attachments: attachments.checked } });
        state.historyEpoch += 1;
        if (!conversation || String(state.selectedId) === String(conversation.id)) {
          state.messages = [];
          state.historyRevision = null;
          state.initialMessagesLoaded = false;
          state.hasMore = false;
          state.beforeSeq = null;
          state.beforeTimestamp = null;
          state.openAround = null;
          state.highlightMessageId = null;
          state.pendingSend = null;
        }
        dialog.close();
        await refreshConversations();
        if (state.mode === "inbox") {
          renderInbox();
          await fetchMessages(true);
        }
        toast(`Deleted ${result.deleted_messages} messages. ${result.attachments_kept ? "Saved attachment files kept." : `${result.deleted_attachments} attachment files deleted.`}`);
      } catch (err) {
        error.textContent = err.status ? err.message :
          "The deletion result could not be confirmed. Refresh the inbox before retrying. " + err.message;
        error.classList.remove("hidden");
      } finally {
        busy = false;
        submit.disabled = cancel.disabled = attachments.disabled = confirmation.disabled = false;
        submit.textContent = "Delete history";
      }
    });
    document.body.appendChild(dialog);
    dialog.showModal();
    confirmation.focus();
  }

  function renderChecklist() {
    const page = pageHeading("Go-live readiness", "Configuration checklist", "A precise checklist for receiving customer messages and keeping attachments safe. No secrets are displayed here.");
    const steps = [
      ["Staff login", "Set STAFF_PASSWORD to at least 12 characters and SESSION_SECRET in Replit Secrets. Access remains closed until both are configured."],
      ["Connect WhatsApp", "In Meta App Dashboard, configure WhatsApp Cloud API credentials as Replit Secrets. This workspace does not collect access tokens or app secrets."],
      ["Verify webhook", "Set WEBHOOK_VERIFY_TOKEN and use your site origin plus /webhook as the Meta callback URL. Subscribe the app to the messages webhook field."],
      ["Verify signed events", "Set META_APP_SECRET. The server uses it to validate signed webhook POSTs before processing events."],
      ["Choose a sender", "Open Settings and enter the business phone number ID from Meta App Dashboard → WhatsApp → API Setup. Do not enter the phone number itself."],
      ["Test real delivery", "Send a real message from a customer test phone. Confirm the incoming message and its media state appear here, then reply within the 24-hour customer service window."]
    ];
    const list = node("div", "checklist");
    steps.forEach(([title, description], index) => {
      const step = node("article", "check-step");
      step.appendChild(node("span", "step-num", String(index + 1).padStart(2, "0")));
      const copy = node("div", "");
      copy.append(node("h3", "", title), node("p", "", description));
      step.appendChild(copy);
      list.appendChild(step);
    });
    page.appendChild(list);
    const details = node("div", "status-list");
    const status = state.status || {};
    for (const [labelText, ready] of [
      ["Webhook verify token", status.webhook_token_configured],
      ["Meta app secret", status.app_secret_configured],
      ["WhatsApp access token", status.access_token_configured],
      ["Default sender", Boolean(status.default_phone_number_id)]
    ]) {
      const item = node("div", "status-item");
      item.append(node("span", `status-check${ready ? "" : " missing"}`, ready ? "✓" : "!"), node("span", "", `${labelText}: ${ready ? "configured" : "needs attention"}`));
      details.appendChild(item);
    }
    page.appendChild(details);
    const actions = node("div", "state-actions");
    actions.append(button("Open settings", "primary-button", null, () => setMode("settings")), backToInboxButton());
    page.appendChild(actions);
  }
  function renderNewConversation() {
    const page = pageHeading("Start a customer thread", "New conversation", "Creating a conversation does not send a message. The customer must write first before a free-form reply can be sent.");
    const card = node("form", "settings-card");
    card.style.maxWidth = "620px";
    card.append(node("h3", "", "Customer details"), node("p", "", "Enter the complete international number, including country code."));
    const recipientLabel = node("label", "field-label", "International phone number");
    recipientLabel.htmlFor = "new-recipient";
    const recipient = node("input", "field-input");
    recipient.id = "new-recipient";
    recipient.type = "tel";
    recipient.autocomplete = "tel";
    recipient.placeholder = "+447700900123";
    recipient.required = true;
    recipient.pattern = "\\+?[0-9]{7,15}";
    const nameLabel = node("label", "field-label", "Customer name (optional)");
    nameLabel.htmlFor = "new-name";
    const name = node("input", "field-input");
    name.id = "new-name";
    name.autocomplete = "name";
    name.maxLength = 120;
    name.placeholder = "Name your team recognizes";
    const phones = Array.isArray(state.status?.phone_numbers) ? state.status.phone_numbers : [];
    let phoneSelect = null;
    if (phones.length > 0) {
      const phoneLabel = node("label", "field-label", "Send from");
      phoneLabel.htmlFor = "new-phone";
      phoneSelect = document.createElement("select");
      phoneSelect.id = "new-phone";
      phoneSelect.className = "field-select";
      const automatic = node("option", "", "Use default sender");
      automatic.value = "";
      phoneSelect.appendChild(automatic);
      for (const phone of phones) {
        const option = node("option", "", `${phone.display_phone_number || phone.id}${phone.verified_name ? ` · ${phone.verified_name}` : ""}`);
        option.value = String(phone.id);
        phoneSelect.appendChild(option);
      }
      phoneSelect.value = String(state.status.default_phone_number_id || "");
      card.append(recipientLabel, recipient, nameLabel, name, phoneLabel, phoneSelect);
    } else card.append(recipientLabel, recipient, nameLabel, name);
    const error = node("div", "inline-error hidden");
    const footer = node("div", "form-actions");
    footer.appendChild(backToInboxButton());
    const create = node("button", "primary-button", "Create conversation");
    create.type = "submit";
    footer.appendChild(create);
    card.append(error, footer);
    card.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.classList.add("hidden");
      const clean = recipient.value.replace(/[()\s.-]/g, "");
      if (!/^\+?\d{7,15}$/.test(clean)) {
        error.textContent = "Enter an international number with 7–15 digits.";
        error.classList.remove("hidden");
        return;
      }
      create.disabled = true;
      create.textContent = "Creating…";
      const body = { recipient: clean };
      if (name.value.trim()) body.name = name.value.trim();
      if (phoneSelect && phoneSelect.value) body.phone_number_id = phoneSelect.value;
      try {
        const result = await request("/api/conversations", { method: "POST", body });
        await refreshConversations();
        const created = result.conversation;
        if (created && created.id !== undefined) {
          const exists = state.conversations.some((item) => String(item.id) === String(created.id));
          if (!exists) state.conversations.unshift(created);
          state.selectedId = created.id;
          state.messages = [];
          state.initialMessagesLoaded = false;
          state.mode = "inbox";
          renderConversationList();
          renderInbox();
          await fetchMessages(true);
          request(`/api/conversations/${encodeURIComponent(created.id)}/read`, { method: "POST", body: {} }).catch(() => {});
        } else setMode("inbox");
        toast("Conversation created. No message was sent.");
      } catch (err) {
        error.textContent = err.message;
        error.classList.remove("hidden");
      } finally {
        create.disabled = false;
        create.textContent = "Create conversation";
      }
    });
    page.appendChild(card);
  }
  async function handleExpiredSession() {
    if (state.handlingExpiredSession) return;
    state.handlingExpiredSession = true;
    window.clearInterval(pollTimer);
    state.authenticated = false;
    state.conversations = [];
    state.visibleConversations = null;
    state.messages = [];
    state.selectedId = null;
    state.drafts.clear();
    state.pendingSend = null;
    state.search = "";
    state.conversationRequestId += 1;
    state.pollIssues = {};
    try {
      const session = await request("/api/session");
      state.csrf = session.csrf_token || "";
      state.staffConfigured = session.staff_configured !== false;
      if (state.staffConfigured) showAuth();
      else showStaffSetup();
      toast("Your staff session expired. Sign in again to continue.");
    } catch (_) {
      state.csrf = "";
      showStartupError("Your staff session expired, and the session endpoint is unavailable. Try again when the service responds.");
    } finally {
      state.handlingExpiredSession = false;
    }
  }
  async function logoutStaff() {
    try {
      await request("/api/logout", { method: "POST", body: {} });
      state.authenticated = false;
      state.csrf = "";
      state.conversations = [];
      state.visibleConversations = null;
      state.messages = [];
      state.selectedId = null;
      state.drafts.clear();
      state.pendingSend = null;
      state.search = "";
      state.conversationRequestId += 1;
      state.pollIssues = {};
      window.clearInterval(pollTimer);
      await start();
    } catch (err) {
      if (state.authenticated) toast(`Could not sign out: ${err.message}`, true);
    }
  }
  async function bootWorkspace() {
    showLoading("Opening your workspace");
    try {
      const [status, conversations] = await Promise.all([request("/api/status"), request("/api/conversations")]);
      state.status = status;
      state.pollIssues = {};
      state.conversations = Array.isArray(conversations.conversations) ? conversations.conversations : [];
      state.pollError = "";
      state.listError = "";
      if (state.selectedId && !state.conversations.some((item) => String(item.id) === String(state.selectedId))) state.selectedId = null;
      state.mode = "home";
      makeWorkspace();
      setMode("home");
      startPolling();
      if (state.selectedId !== null && state.mode === "inbox") fetchMessages(true);
    } catch (err) {
      if (!state.authenticated) return;
      showStartupError(`The staff session is active, but the inbox could not load. ${err.message}`);
    }
  }
  async function start() {
    showLoading();
    try {
      const session = await request("/api/session");
      state.csrf = session.csrf_token || "";
      state.staffConfigured = session.staff_configured !== false;
      state.authenticated = Boolean(session.authenticated);
      if (!state.staffConfigured) {
        showStaffSetup();
      } else if (!state.authenticated) {
        showAuth();
      } else {
        await bootWorkspace();
      }
    } catch (err) {
      showStartupError(`Session status is unavailable. ${err.message}`);
    }
  }
  start();
})();
