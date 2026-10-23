(function () {
  "use strict";

  let activeInstance = null;
  const scriptSource = document.currentScript && document.currentScript.src;

  function ensureStylesheet() {
    if (!scriptSource) return;
    const href = new URL("workspace-home.css", scriptSource).href;
    if (!Array.from(document.querySelectorAll('link[rel="stylesheet"]')).some((link) => link.href === href)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      document.head.appendChild(link);
    }
  }

  function mount(options) {
    if (!options || !options.container || typeof options.onNavigate !== "function") {
      throw new Error("WorkspaceHome.mount requires a container and onNavigate callback.");
    }
    if (activeInstance) activeInstance.unmount();
    ensureStylesheet();

    const container = options.container;
    let destroyed = false;
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    };
    const button = (label, className, action, ariaLabel) => {
      const node = el("button", className, label);
      node.type = "button";
      if (ariaLabel) node.setAttribute("aria-label", ariaLabel);
      node.addEventListener("click", action);
      return node;
    };
    const navigate = (destination) => {
      if (!destroyed) options.onNavigate(destination);
    };

    function render() {
      const root = el("section", "wh-root");
      root.setAttribute("aria-label", "Quote Bot AI workspace home");
      const header = el("header", "wh-header");
      const brand = el("div", "wh-brand");
      const mark = el("span", "wh-brand-mark");
      mark.setAttribute("aria-hidden", "true");
      mark.appendChild(svg("wh-icon", '<path d="M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v6a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3v-3.2A2.5 2.5 0 0 1 5 12.5z"/><path d="M8.5 8.5h7M8.5 11.5h4"/>'));
      const name = el("span", "wh-brand-name");
      name.append(document.createTextNode("Quote Bot "), el("span", "", "AI"));
      brand.append(mark, name);
      const status = el("div", "wh-status");
      const healthy = options.healthStatus === true ||
        (options.healthStatus && ["ok", "healthy", "connected", "online"].includes(String(options.healthStatus.status || options.healthStatus).toLowerCase()));
      status.append(el("span", `wh-status-dot${healthy ? "" : " is-muted"}`),
        el("span", "", healthy ? "Workspace connected" : "Private staff workspace"));
      header.append(brand, status);

      const hero = el("div", "wh-hero");
      const copy = el("div", "wh-hero-copy");
      copy.append(el("p", "wh-eyebrow", "Staff workspace / private"),
        el("h1", "wh-title", "Good decisions,"),
        el("h2", "wh-title wh-title-second", "with the full picture."),
        el("p", "wh-lede", "A shared place to respond to customers, compare their quotations, and keep the record your team can stand behind."));
      const overview = el("aside", "wh-overview");
      overview.setAttribute("aria-label", "Workspace overview");
      overview.append(el("span", "wh-overview-label", "Your workspace"),
        el("strong", "wh-overview-count", Number.isFinite(Number(options.conversationCount))
          ? new Intl.NumberFormat().format(Number(options.conversationCount)) : "—"),
        el("span", "wh-overview-copy", "customer conversations"),
        el("span", "wh-overview-rule"),
        el("span", "wh-overview-foot", "Shared with your staff team"));
      hero.append(copy, overview);

      const nav = el("div", "wh-section-head");
       nav.append(el("span", "wh-eyebrow", "Choose where to work"),
         el("span", "wh-section-note", "Four tools. One shared workspace."));
      const destinations = el("div", "wh-destinations");
      destinations.append(
        destination("01", "Inbox", "Customer conversations", "Reply to incoming WhatsApp messages and follow each conversation in one private team inbox.", "inbox", "chat"),
        destination("02", "Price comparison", "Make the trade-offs visible", "Review client-submitted quotations side by side. Price and delivery evidence stay easy to scan.", "quotes", "compare"),
        destination("03", "Quotation register", "Saved decisions, ready to export", "Browse completed quote analyses and download the team record as an Excel workbook.", "register", "sheet"),
        destination("04", "Quote AI Chat", "Ask the full record", "Explore completed comparison facts, draft customer-ready language, and think through price or delivery trade-offs with the configured model.", "chat", "assistant")
      );
      const footer = el("footer", "wh-footer");
      footer.append(el("span", "", "QUOTE BOT AI"),
        el("span", "", "For your team. Not a customer-facing channel."));
      root.append(header, hero, nav, destinations, footer);
      container.replaceChildren(root);

      function destination(number, title, subtitle, detail, target, iconName) {
        const card = button("", "wh-destination", () => navigate(target));
        card.setAttribute("aria-label", `${title}. ${subtitle}`);
        const top = el("span", "wh-card-top");
        top.append(el("span", "wh-card-number", number), iconFor(iconName));
        const titleNode = el("span", "wh-card-title", title);
        const sub = el("span", "wh-card-subtitle", subtitle);
        const desc = el("span", "wh-card-detail", detail);
        const action = el("span", "wh-card-action", target === "register" ? "Open register" : target === "quotes" ? "Compare quotations" : target === "chat" ? "Start a conversation" : "Open inbox");
        action.appendChild(arrow());
        card.append(top, titleNode, sub, desc, action);
        return card;
      }
    }

    function svg(className, content) {
      const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      node.setAttribute("class", className);
      node.setAttribute("viewBox", "0 0 24 24");
      node.setAttribute("fill", "none");
      node.setAttribute("stroke", "currentColor");
      node.setAttribute("stroke-width", "1.7");
      node.setAttribute("stroke-linecap", "round");
      node.setAttribute("stroke-linejoin", "round");
      node.setAttribute("aria-hidden", "true");
      node.innerHTML = content;
      return node;
    }
    function iconFor(name) {
      const paths = {
        chat: '<path d="M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v6a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3v-3.2A2.5 2.5 0 0 1 5 12.5z"/><path d="M8.5 8.5h7M8.5 11.5h4"/>',
        compare: '<path d="M4 7h16M4 17h16M8 4 5 7l3 3M16 14l3 3-3 3"/><path d="M14 7H5m14 10H10"/>',
         sheet: '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M8 8h8M8 12h8M8 16h3m2 0h3"/>',
         assistant: '<path d="M12 3.5v3M12 17.5v3M3.5 12h3m11 0h3M6 6l2.2 2.2m7.6 7.6L18 18M18 6l-2.2 2.2m-7.6 7.6L6 18"/><circle cx="12" cy="12" r="4"/>'
      };
      const icon = svg("wh-card-icon", paths[name]);
      return icon;
    }
    function arrow() {
      return svg("wh-arrow", '<path d="M5 12h14M13 6l6 6-6 6"/>');
    }

    render();
    activeInstance = {
      update(next) {
        if (destroyed || !next || !container) return;
        const count = container.querySelector(".wh-overview-count");
        if (count && Number.isFinite(Number(next.conversationCount))) {
          count.textContent = new Intl.NumberFormat().format(Number(next.conversationCount));
        }
      },
      unmount() {
        destroyed = true;
        if (container) container.replaceChildren();
      }
    };
    return activeInstance;
  }

  function unmount() {
    if (activeInstance) activeInstance.unmount();
    activeInstance = null;
  }
  function update(next) {
    if (activeInstance) activeInstance.update(next);
  }
  window.WorkspaceHome = { mount, unmount, update };
})();
