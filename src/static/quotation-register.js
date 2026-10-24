(function () {
  "use strict";

  let activeInstance = null;
  const scriptSource = document.currentScript && document.currentScript.src;
  function ensureStylesheet() {
    if (!scriptSource) return;
    const href = new URL("quotation-register.css", scriptSource).href;
    if (!Array.from(document.querySelectorAll('link[rel="stylesheet"]')).some((link) => link.href === href)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      document.head.appendChild(link);
    }
  }
  function mount(options) {
    if (!options || !options.container || typeof options.request !== "function") {
      throw new Error("QuotationRegister.mount requires a container and request helper.");
    }
    if (activeInstance) activeInstance.unmount();
    ensureStylesheet();
    const container = options.container;
    const toast = typeof options.toast === "function" ? options.toast : function () {};
    const state = { rows: [], total: 0, loading: true, error: "", search: "", filter: "all", destroyed: false };
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    };
    const button = (label, className, onClick) => {
      const node = el("button", className, label);
      node.type = "button";
      node.addEventListener("click", onClick);
      return node;
    };
    const formatDate = (value) => {
      if (value === null || value === undefined || value === "") return "Time not recorded";
      const numeric = typeof value === "number" || (typeof value === "string" && /^\d{9,13}$/.test(value));
      const date = new Date(numeric ? Number(value) * (Number(value) < 100000000000 ? 1000 : 1) : value);
      return Number.isNaN(date.getTime()) ? String(value) :
        new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
    };
    const money = (currency, amount) => {
      if (amount === null || amount === undefined || amount === "") return "Not stated";
      const value = Number(amount);
      if (!Number.isFinite(value)) return `${currency ? `${currency} ` : ""}${String(amount)}`;
      try {
        return new Intl.NumberFormat(undefined, {
          style: currency ? "currency" : "decimal", currency: currency || undefined, currencyDisplay: "code", minimumFractionDigits: 2, maximumFractionDigits: 2
        }).format(value);
      } catch (_) {
        return `${currency ? `${currency} ` : ""}${value.toLocaleString()}`;
      }
    };
    const myrPrice = (row) => row.myr_total_price == null ? "MYR unavailable" : money("MYR", row.myr_total_price);
    const currencyDetails = (row) => [
      row.currency !== "MYR" && row.total_price != null ? `Original: ${money(row.currency, row.total_price)}` : "",
      row.exchange_rate_date ? `1 ${row.currency} = MYR ${Number(row.exchange_rate).toLocaleString(undefined, { maximumFractionDigits: 6 })} · ${row.exchange_rate_date} · ${row.exchange_rate_source}` : "",
      row.conversion_error || ""
    ].filter(Boolean).join(" · ");
    const delivery = (row) => {
      if (row.delivery_text) return String(row.delivery_text);
      if (row.delivery_date) return formatDate(row.delivery_date);
      if (row.delivery_days !== null && row.delivery_days !== undefined) {
        return `${row.delivery_days}${row.delivery_unit ? ` ${row.delivery_unit}` : " days"}`;
      }
      return "Not stated";
    };
    const missing = (value, fallback) => value === null || value === undefined || value === "" ? fallback : String(value);
    const searchable = (row) => [
      row.client_name, row.vendor, row.filename, row.message_id, row.wa_id, row.message_type,
      row.priority, row.price_basis, row.delivery_text, row.delivery_date, row.evidence,
      row.notes, row.message_excerpt, row.extraction_error
    ].filter(Boolean).join(" ").toLowerCase();
    const rows = () => {
      const query = state.search.trim().toLowerCase();
      return state.rows.filter((row) => {
        const matchSearch = !query || searchable(row).includes(query);
        const matchFilter = state.filter === "all" ||
          (state.filter === "recommended" && row.recommended) ||
          (state.filter === "team-choice" && row.team_choice);
        return matchSearch && matchFilter;
      });
    };

    function render() {
      if (state.destroyed) return;
      const root = el("section", "qr-root");
      root.setAttribute("aria-label", "Quotation register");
      const header = el("header", "qr-header");
      const brand = el("div", "qr-brand");
      brand.append(el("span", "qr-mark", "Q"), el("span", "qr-brand-name", "Quote Bot AI"));
      const back = button("Back to workspace", "qr-back", () => {
        if (typeof options.onBack === "function") options.onBack();
      });
      back.prepend(arrow());
      header.append(brand, back);

      const intro = el("div", "qr-intro");
      intro.append(el("p", "qr-kicker", "Staff workspace / records"),
        el("h1", "qr-title", "Quotation register"),
        el("p", "qr-lede", "Completed analyses saved across your team. Each row is one quote from one comparison run; a source can appear again when it was compared another time."));
      const summary = el("div", "qr-summary");
      summary.append(stat("Saved quote records", state.loading ? "…" : state.total),
        stat("Visible in this view", state.loading ? "…" : rows().length));
      intro.appendChild(summary);

      const toolbar = el("div", "qr-toolbar");
      const searchWrap = el("label", "qr-search-wrap");
      searchWrap.appendChild(el("span", "qr-search-label", "Search records"));
      const search = el("input", "qr-search");
      search.type = "search";
      search.placeholder = "Client, vendor, source, evidence…";
      search.value = state.search;
      search.setAttribute("aria-label", "Search quotation records");
      search.addEventListener("input", () => {
        const cursor = search.selectionStart;
        state.search = search.value;
        render();
        const replacement = container.querySelector(".qr-search");
        if (replacement) {
          replacement.focus();
          replacement.setSelectionRange(cursor, cursor);
        }
      });
      searchWrap.appendChild(search);
      const filters = el("div", "qr-filters");
      [["all", "All records"], ["recommended", "Recommended"], ["team-choice", "Team choice"]].forEach(([value, label]) => {
        const filter = button(label, `qr-filter${state.filter === value ? " is-active" : ""}`, () => {
          state.filter = value;
          render();
        });
        filter.setAttribute("aria-pressed", String(state.filter === value));
        filters.appendChild(filter);
      });
      const exportLink = el("a", "qr-export", "");
      exportLink.href = "/api/quotation-register.xlsx";
      exportLink.append(excelIcon(), document.createTextNode("Download .xlsx"));
      exportLink.setAttribute("aria-label", "Download quotation register Excel workbook");
      exportLink.title = "Download all saved quotation records as an Excel workbook";
      toolbar.append(searchWrap, filters, exportLink);
      root.append(header, intro, toolbar);

      if (state.loading) root.appendChild(loadingState());
      else if (state.error) root.appendChild(errorState());
      else if (!state.rows.length) root.appendChild(emptyState(true));
      else if (!rows().length) root.appendChild(emptyState(false));
      else root.appendChild(renderRecords(rows()));
      const foot = el("footer", "qr-foot");
      foot.append(el("span", "", "Internal decision record"),
        el("span", "", "Analysis is advisory. Your team makes the final choice."));
      root.appendChild(foot);
      container.replaceChildren(root);
    }

    function stat(label, value) {
      const node = el("div", "qr-stat");
      node.append(el("span", "", label), el("strong", "", value));
      return node;
    }
    function renderRecords(filteredRows) {
      const section = el("section", "qr-records");
      const countLine = el("div", "qr-list-head");
      countLine.append(el("span", "qr-list-label", "Completed comparisons"),
        el("span", "qr-list-count", `${filteredRows.length} ${filteredRows.length === 1 ? "record" : "records"}`));
      section.appendChild(countLine);
      const tableWrap = el("div", "qr-table-wrap");
      const table = el("table", "qr-table");
      table.setAttribute("aria-label", "Completed quotation comparison records");
      const thead = el("thead");
      const header = el("tr");
      ["Client / vendor", "Source", "Price (MYR)", "Delivery", "Priority / rank", "Recommendation / team choice", "Evidence"].forEach((label) => {
        header.appendChild(el("th", "", label));
      });
      thead.appendChild(header);
      const tbody = el("tbody");
      filteredRows.forEach((row) => tbody.appendChild(renderTableRow(row)));
      table.append(thead, tbody);
      tableWrap.appendChild(table);
      section.appendChild(tableWrap);

      const list = el("div", "qr-list");
      filteredRows.forEach((row) => list.appendChild(renderRow(row)));
      section.appendChild(list);
      return section;
    }
    function renderTableRow(row) {
      const tableRow = el("tr", `qr-table-row${row.recommended ? " is-recommended" : ""}${row.team_choice ? " is-chosen" : ""}`);
      const party = el("td", "qr-table-party");
      party.append(el("strong", "", missing(row.client_name, "Client not identified")),
        el("span", "", missing(row.vendor, missing(row.filename, "Vendor not stated"))),
        el("small", "", `Comparison #${missing(row.comparison_id, "—")}`));

      const source = el("td", "qr-table-source");
      const sourceParts = [
        row.filename && `File: ${row.filename}`,
        row.message_type && `Type: ${row.message_type}`,
        row.message_id && `Message: ${row.message_id}`,
        row.wa_id && `WhatsApp: ${row.wa_id}`
      ].filter(Boolean);
      source.append(el("span", "", sourceParts.length ? sourceParts.join(" · ") : "Source details not recorded"),
        el("small", "", `Received ${formatDate(row.received_at)}`),
        el("small", "", `Analyzed ${formatDate(row.analyzed_at)}`));

      const price = el("td", "qr-table-price");
      price.append(el("strong", "", myrPrice(row)));
      if (currencyDetails(row)) price.appendChild(el("small", "", currencyDetails(row)));
      if (row.price_basis) price.appendChild(el("small", "", `Basis: ${row.price_basis}`));

      const deliveryCell = el("td", "qr-table-delivery");
      deliveryCell.appendChild(el("span", "", delivery(row)));

      const priority = el("td", "qr-table-priority");
      priority.append(el("span", "qr-table-priority-name", missing(row.priority, "Not stated")),
        el("small", "", row.rank == null ? "Not ranked" : `Rank #${row.rank}`));

      const decision = el("td", "qr-table-decision");
      if (row.recommended) decision.appendChild(el("span", "qr-badge qr-badge-recommended", "Recommended"));
      if (row.team_choice) decision.appendChild(el("span", "qr-badge qr-badge-choice", "Team choice"));
      if (!row.recommended && !row.team_choice) decision.appendChild(el("span", "qr-table-none", "No decision recorded"));

      const evidence = el("td", "qr-table-evidence");
      evidence.appendChild(el("span", "", missing(row.evidence, "No supporting evidence recorded.")));
      if (row.notes) evidence.appendChild(el("small", "qr-note", `Notes: ${row.notes}`));
      if (row.extraction_error) evidence.appendChild(el("small", "qr-error-note", `Extraction: ${row.extraction_error}`));
      if (row.message_excerpt) evidence.appendChild(el("small", "qr-table-excerpt", `Source text: ${row.message_excerpt}`));
      tableRow.append(party, source, price, deliveryCell, priority, decision, evidence);
      return tableRow;
    }
    function renderRow(row) {
      const card = el("article", `qr-record${row.recommended ? " is-recommended" : ""}${row.team_choice ? " is-chosen" : ""}`);
      const top = el("div", "qr-record-top");
      const identity = el("div", "qr-identity");
      identity.append(el("span", "qr-record-kicker", `Comparison #${missing(row.comparison_id, "—")}`),
        el("h2", "qr-vendor", missing(row.vendor, missing(row.client_name, missing(row.filename, "Quotation")))));
      const pills = el("div", "qr-badges");
      if (row.recommended) pills.appendChild(el("span", "qr-badge qr-badge-recommended", "Recommended"));
      if (row.team_choice) pills.appendChild(el("span", "qr-badge qr-badge-choice", "Team choice"));
      if (row.priority) pills.appendChild(el("span", "qr-badge qr-badge-priority", String(row.priority)));
      top.append(identity, pills);
      const metrics = el("div", "qr-metrics");
      metrics.append(
        metric("Price (MYR)", myrPrice(row), [currencyDetails(row), row.price_basis ? `Basis: ${row.price_basis}` : ""].filter(Boolean).join(" · ")),
        metric("Delivery", delivery(row), row.delivery_text && row.delivery_days != null ? `${row.delivery_days}${row.delivery_unit ? ` ${row.delivery_unit}` : " days"}` : ""),
        metric("Rank", row.rank == null ? "Not ranked" : `#${row.rank}`, row.recommended ? "Suggested option" : row.team_choice ? "Recorded team selection" : "")
      );
      const details = el("div", "qr-detail-grid");
      const source = el("div", "qr-detail");
      source.append(el("span", "qr-detail-label", "Source & timing"));
      source.append(el("strong", "", missing(row.client_name, "Client not identified")));
      const sourceMeta = [
        row.filename && `File: ${row.filename}`,
        row.message_type && `Type: ${row.message_type}`,
        row.message_id && `Message: ${row.message_id}`,
        row.wa_id && `WhatsApp: ${row.wa_id}`,
        `Received ${formatDate(row.received_at)}`,
        `Analyzed ${formatDate(row.analyzed_at)}`
      ].filter(Boolean);
      source.append(el("span", "qr-detail-copy", sourceMeta.join(" · ")));
      if (row.message_excerpt) source.append(el("span", "qr-excerpt", `“${String(row.message_excerpt)}”`));
      details.appendChild(source);
      const evidence = el("div", "qr-detail");
      evidence.append(el("span", "qr-detail-label", "Evidence & notes"),
        el("span", "qr-detail-copy", missing(row.evidence, "No supporting evidence recorded.")));
      if (row.notes) evidence.append(el("span", "qr-note", `Team notes: ${row.notes}`));
      if (row.extraction_error) evidence.append(el("span", "qr-error-note", `Extraction note: ${row.extraction_error}`));
      if (row.confidence !== null && row.confidence !== undefined && row.confidence !== "") {
        const confidence = Number(row.confidence);
        evidence.append(el("span", "qr-confidence", `Extraction confidence: ${Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : String(row.confidence)}`));
      }
      details.appendChild(evidence);
      card.append(top, metrics, details);
      return card;
    }
    function metric(label, value, note) {
      const node = el("div", "qr-metric");
      node.append(el("span", "qr-metric-label", label), el("strong", "", value));
      if (note) node.appendChild(el("span", "qr-metric-note", note));
      return node;
    }
    function loadingState() {
      const section = el("div", "qr-state qr-loading");
      section.setAttribute("role", "status");
      section.append(el("span", "qr-state-mark"), el("strong", "", "Loading saved records"),
        el("span", "qr-state-copy", "Retrieving completed quotation comparisons."));
      const skeletons = el("div", "qr-skeleton-list");
      for (let i = 0; i < 3; i += 1) {
        const item = el("div", "qr-skeleton-card");
        item.append(el("span", "qr-skeleton qr-skeleton-short"), el("span", "qr-skeleton qr-skeleton-long"), el("span", "qr-skeleton qr-skeleton-line"));
        skeletons.appendChild(item);
      }
      section.appendChild(skeletons);
      return section;
    }
    function errorState() {
      const section = el("div", "qr-state qr-error-state");
      section.append(el("span", "qr-state-mark", "!"), el("strong", "", "Register unavailable"),
        el("span", "qr-state-copy", state.error));
      section.appendChild(button("Try again", "qr-retry", load));
      return section;
    }
    function emptyState(isEmpty) {
      const section = el("div", "qr-state");
      section.append(el("span", "qr-state-mark", isEmpty ? "—" : "⌕"),
        el("strong", "", isEmpty ? "No completed comparisons yet" : "No matching records"),
        el("span", "qr-state-copy", isEmpty
          ? "Once your team completes a quotation analysis, each saved quote will be listed here with its source, evidence, and decision details."
          : "Try another search or clear the active filters to see more saved quotations."));
      if (!isEmpty) {
        section.appendChild(button("Clear search and filters", "qr-retry", () => {
          state.search = "";
          state.filter = "all";
          render();
        }));
      }
      return section;
    }
    function arrow() {
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      icon.setAttribute("viewBox", "0 0 24 24");
      icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = '<path d="m14 5-7 7 7 7M7 12h14"/>';
      return icon;
    }
    function excelIcon() {
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      icon.setAttribute("viewBox", "0 0 24 24");
      icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10z"/><path d="M13 3v7h7M8 13l4 5m0-5-4 5"/>';
      return icon;
    }
    async function load() {
      state.loading = true;
      state.error = "";
      render();
      try {
        const response = await options.request("/api/quotation-register");
        state.rows = Array.isArray(response && response.rows) ? response.rows : [];
        state.total = Number.isFinite(Number(response && response.total)) ? Number(response.total) : state.rows.length;
      } catch (error) {
        state.error = error && error.message ? error.message : "Could not load saved quotation records.";
        toast(state.error);
      } finally {
        state.loading = false;
        render();
      }
    }
    const instance = {
      unmount() {
        state.destroyed = true;
        if (container) container.replaceChildren();
      },
      refresh: load
    };
    activeInstance = instance;
    load();
    return instance;
  }
  function unmount() {
    if (activeInstance) activeInstance.unmount();
    activeInstance = null;
  }
  window.QuotationRegister = { mount, unmount };
})();
