(function () {
  "use strict";

  const MAX_SELECTION = 8;
  let activeInstance = null;
  const scriptSource = document.currentScript && document.currentScript.src;

  function ensureStylesheet() {
    if (!scriptSource) return;
    const href = new URL("quote-evaluator.css", scriptSource).href;
    const exists = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
      .some((link) => link.href === href);
    if (!exists) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      document.head.appendChild(link);
    }
  }

  function mount(options) {
    if (!options || !options.container || typeof options.request !== "function") {
      throw new Error("QuoteEvaluator.mount requires a container and request helper.");
    }
    if (activeInstance && typeof activeInstance.destroy === "function") activeInstance.destroy();
    ensureStylesheet();

    const container = options.container;
    const request = options.request;
    const toast = typeof options.toast === "function" ? options.toast : function () {};
    const state = {
      candidates: [],
      selected: new Set(),
      priority: "balanced",
      evaluation: null,
      evaluations: [],
      loading: true,
      submitting: false,
      error: "",
      pollTimer: null,
      destroyed: false
    };

    const el = (tag, className, text) => {
      const item = document.createElement(tag);
      if (className) item.className = className;
      if (text !== undefined && text !== null) item.textContent = String(text);
      return item;
    };
    const button = (label, className, onClick) => {
      const item = el("button", className, label);
      item.type = "button";
      item.addEventListener("click", onClick);
      return item;
    };
    const formatDate = (value) => {
      if (!value) return "Time not recorded";
      const numeric = typeof value === "number" ||
        (typeof value === "string" && /^\d{9,13}$/.test(value));
      const date = new Date(numeric ? Number(value) * (Number(value) < 100000000000 ? 1000 : 1) : value);
      return Number.isNaN(date.getTime())
        ? String(value)
        : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
    };
    const isAttachment = (candidate) => Boolean(candidate.filename || candidate.mime_type || candidate.media_url);
    const isSelectable = (candidate) => {
      if (isAttachment(candidate)) return candidate.ready === true;
      return Boolean(candidate.body && String(candidate.body).trim());
    };
    const candidateLabel = (candidate) => {
      if (candidate.filename) return candidate.filename;
      if (candidate.type) return `${candidate.type} message`;
      return "Text quotation";
    };
    const clearPoll = () => {
      if (state.pollTimer) window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    };

    function render() {
      if (state.destroyed) return;
      const root = el("section", "qe-root");
      root.setAttribute("aria-label", "Quotation comparison");
      const header = el("header", "qe-header");
      const brand = el("div", "qe-brandline");
      brand.append(el("span", "qe-brand-mark", "Q"), el("span", "qe-brand-name", "Quote Bot AI"));
      const headerActions = el("div", "qe-header-actions");
      headerActions.appendChild(el("span", "qe-overline", "Internal decision support"));
      if (typeof options.onBack === "function") {
        const back = button("Back to home", "qe-back-button", options.onBack);
        headerActions.appendChild(back);
      }
      header.append(brand, headerActions);

      const intro = el("div", "qe-intro");
      const titleBlock = el("div", "qe-title-block");
      titleBlock.append(el("p", "qe-kicker", "Quotation evaluator"), el("h1", "qe-title", "Compare before you commit"));
      titleBlock.appendChild(el("p", "qe-lede", "Review client-submitted quotations side by side. The analysis is advisory; the final choice stays with your team."));
      const introAside = el("div", "qe-selection-count");
      introAside.append(el("span", "qe-count-number", `${state.selected.size}/${MAX_SELECTION}`), el("span", "qe-count-label", "items selected"));
      intro.append(titleBlock, introAside);

      const disclosure = el("aside", "qe-disclosure");
      disclosure.setAttribute("role", "note");
      disclosure.append(el("span", "qe-disclosure-mark", "i"));
      const disclosureCopy = el("p", "qe-disclosure-copy");
      disclosureCopy.append(el("strong", "", "Before analysis: "), document.createTextNode("selected quotation content is sent to RunPod for analysis. No WhatsApp reply is sent, and no quote is accepted automatically."));
      disclosure.appendChild(disclosureCopy);

      const layout = el("div", "qe-layout");
      const selectionPanel = el("section", "qe-panel qe-input-panel");
      selectionPanel.appendChild(sectionHeading("01", "Choose source material", `${state.candidates.length} available`));
      const panelHint = el("p", "qe-panel-hint", "Select up to eight incoming messages or saved quotation attachments.");
      selectionPanel.appendChild(panelHint);

      if (state.loading) {
        const loading = el("div", "qe-loading");
        loading.setAttribute("role", "status");
        loading.append(el("span", "qe-skeleton qe-sk-title"), el("span", "qe-skeleton qe-sk-row"), el("span", "qe-skeleton qe-sk-row"), el("span", "qe-skeleton qe-sk-row"));
        selectionPanel.appendChild(loading);
      } else if (state.error && !state.candidates.length) {
        selectionPanel.appendChild(statePanel("Could not load quotation sources", state.error, "Retry", loadCandidates));
      } else if (!state.candidates.length) {
        selectionPanel.appendChild(statePanel("No saved sources yet", "Incoming client messages and saved attachments will appear here when available."));
      } else {
        const list = el("div", "qe-source-list");
        state.candidates.forEach((candidate) => list.appendChild(renderCandidate(candidate)));
        selectionPanel.appendChild(list);
         const refreshRow = el("div", "qe-refresh-row");
         refreshRow.append(el("span", "qe-muted", "Only selected quotation content is sent to RunPod."), button("Refresh", "qe-link-button", loadCandidates));
        selectionPanel.appendChild(refreshRow);
      }

      const controls = el("section", "qe-panel qe-controls");
      controls.appendChild(sectionHeading("02", "Set comparison priority", "Choose what matters most"));
      const priorities = el("div", "qe-priorities");
      [
        ["price", "Lowest cost", "Compare total price first"],
        ["time", "Fastest delivery", "Compare delivery timing first"],
        ["balanced", "Both equally", "Balance price and delivery"]
      ].forEach(([value, label, detail]) => {
        const choice = el("label", `qe-priority${state.priority === value ? " is-active" : ""}`);
        const radio = el("input");
        radio.type = "radio";
        radio.name = "qe-priority";
        radio.value = value;
        radio.checked = state.priority === value;
        radio.addEventListener("change", () => {
          state.priority = value;
          render();
        });
        const radioMark = el("span", "qe-radio-mark");
        const copy = el("span", "qe-priority-copy");
        copy.append(el("strong", "", label), el("small", "", detail));
        choice.append(radio, radioMark, copy);
        priorities.appendChild(choice);
      });
      controls.appendChild(priorities);
      const startButton = button(state.submitting ? "Starting analysis…" : "Analyze selected", "qe-primary-button", startEvaluation);
      startButton.disabled = state.submitting || state.loading || state.selected.size < 1 || Boolean(state.evaluation && isActive(state.evaluation));
      controls.appendChild(startButton);
      controls.appendChild(el("p", "qe-control-note", "Analysis extracts terms and evidence; it does not decide on your behalf."));
      if (state.error && state.candidates.length) {
        const error = el("p", "qe-inline-error", state.error);
        error.setAttribute("role", "alert");
        controls.appendChild(error);
      }

      layout.append(selectionPanel, controls);
      root.append(header, intro, disclosure, layout);
      if (state.evaluations.length) root.appendChild(renderRecentEvaluations());
      if (state.evaluation) root.appendChild(renderEvaluation(state.evaluation));
      container.replaceChildren(root);

      function renderRecentEvaluations() {
        const section = el("section", "qe-panel qe-history");
        section.appendChild(sectionHeading("04", "Recent comparisons", "Saved for all staff"));
        const list = el("div", "qe-history-list");
        state.evaluations.slice(0, 6).forEach((item) => {
          const current = state.evaluation && String(state.evaluation.id) === String(item.id);
          const entry = button(
            `${formatDate(item.created_at)} · ${item.priority} · ${statusText(item.status)}`,
            `qe-history-item${current ? " is-active" : ""}`,
            () => loadEvaluation(item.id)
          );
          entry.setAttribute("aria-current", current ? "true" : "false");
          list.appendChild(entry);
        });
        section.appendChild(list);
        return section;
      }

      function renderCandidate(candidate) {
        const row = el("label", `qe-source${state.selected.has(candidate.id) ? " is-selected" : ""}${!isSelectable(candidate) ? " is-unavailable" : ""}`);
        const checkbox = el("input", "qe-checkbox");
        checkbox.type = "checkbox";
        checkbox.checked = state.selected.has(candidate.id);
        checkbox.disabled = !isSelectable(candidate) || (!checkbox.checked && state.selected.size >= MAX_SELECTION) || state.submitting;
        checkbox.setAttribute("aria-label", `${candidateLabel(candidate)}${isSelectable(candidate) ? "" : ", unavailable"}`);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) state.selected.add(candidate.id);
          else state.selected.delete(candidate.id);
          render();
        });
        const sourceMark = el("span", "qe-source-mark", isAttachment(candidate) ? "FILE" : "TEXT");
        const copy = el("span", "qe-source-copy");
        const top = el("span", "qe-source-titleline");
        top.append(el("strong", "qe-source-title", candidateLabel(candidate)));
        top.append(el("span", "qe-source-date", formatDate(candidate.timestamp)));
        copy.append(top);
        if (candidate.client_name) copy.appendChild(el("span", "qe-source-client", candidate.client_name));
        const excerpt = candidate.caption || candidate.body || candidate.attachment_state || "";
        if (excerpt) copy.appendChild(el("span", "qe-source-excerpt", excerpt));
        const status = isSelectable(candidate) ? "Ready to analyze" : "Unavailable — download incomplete";
        copy.appendChild(el("span", `qe-source-status${isSelectable(candidate) ? " ready" : " unavailable"}`, status));
        row.append(checkbox, sourceMark, copy);
        return row;
      }

      function renderEvaluation(evaluation) {
        const section = el("section", "qe-panel qe-results");
        const resultHead = el("div", "qe-results-head");
        const statusLabel = statusText(evaluation.status);
        resultHead.appendChild(sectionHeading("03", "Comparison", statusLabel));
        if (evaluation.status === "queued" || evaluation.status === "processing") {
          resultHead.appendChild(el("p", "qe-progress-note", "Analysis is running. This view will update automatically."));
        } else if (evaluation.status === "failed") {
          resultHead.appendChild(el("p", "qe-inline-error", evaluation.error || "Analysis could not be completed."));
        }
        section.appendChild(resultHead);
        if (evaluation.status === "queued" || evaluation.status === "processing") {
          const progress = el("div", "qe-progress");
          progress.setAttribute("role", "status");
          progress.append(el("span", "qe-skeleton qe-sk-title"), el("span", "qe-skeleton qe-sk-row"), el("span", "qe-skeleton qe-sk-row"));
          section.appendChild(progress);
        }
        const result = evaluation.result;
        if (evaluation.status === "completed" && result) {
          if (result.summary) section.appendChild(el("p", "qe-summary", result.summary));
          if (Array.isArray(result.limitations) && result.limitations.length) {
            const limitations = el("div", "qe-limitations");
            limitations.appendChild(el("strong", "", "What to keep in mind"));
            const ul = el("ul");
            result.limitations.forEach((item) => ul.appendChild(el("li", "", item)));
            limitations.appendChild(ul);
            section.appendChild(limitations);
          }
          const quotes = Array.isArray(result.quotes) ? result.quotes.slice().sort((a, b) => (a.rank || 999) - (b.rank || 999)) : [];
          if (quotes.length) {
            const tableWrap = el("div", "qe-table-wrap");
            const table = el("table", "qe-table");
            const thead = el("thead");
            const headerRow = el("tr");
            ["Rank", "Quotation", "Total", "Delivery", "Confidence", "Evidence"].forEach((heading) => headerRow.appendChild(el("th", "", heading)));
            thead.appendChild(headerRow);
            const tbody = el("tbody");
            quotes.forEach((quote) => tbody.appendChild(renderQuoteRow(quote, evaluation, result.recommendation_message_id)));
            table.append(thead, tbody);
            tableWrap.appendChild(table);
            section.appendChild(tableWrap);
            quotes.forEach((quote) => {
              const card = renderQuoteCard(quote, evaluation, result.recommendation_message_id);
              section.appendChild(card);
            });
            const selected = evaluation.selected_message_id;
            if (selected) {
              const chosen = quotes.find((quote) => String(quote.message_id) === String(selected));
              const choiceNotice = el("div", "qe-choice-notice");
              choiceNotice.append(el("span", "qe-choice-check", "Selected"), el("span", "", chosen ? `Your team selected ${chosen.client_name || chosen.filename || "this quotation"}.` : "Your team selected a quotation."));
              section.appendChild(choiceNotice);
            }
          } else {
            section.appendChild(statePanel("No quotations extracted", "The analysis did not return comparable quotation details. Review the source material and try again."));
          }
        }
        return section;
      }

      function renderQuoteRow(quote, evaluation, recommendedId) {
        const row = el("tr", quote.comparable === false ? "qe-not-comparable" : "");
        const rank = el("td", "qe-rank-cell");
        rank.append(el("span", "qe-rank", quote.rank ? `#${quote.rank}` : "—"));
        if (String(quote.message_id) === String(recommendedId)) rank.appendChild(el("span", "qe-recommended", "Suggested"));
        row.appendChild(rank);
        const vendorCell = el("td", "qe-vendor-cell");
        vendorCell.append(el("strong", "", quote.vendor || quote.client_name || quote.filename || "Quotation"), el("small", "", [quote.client_name, quote.filename].filter(Boolean).join(" · ")));
        if (quote.extraction_error) vendorCell.appendChild(el("small", "qe-warning", quote.extraction_error));
        row.appendChild(vendorCell);
        row.appendChild(el("td", "qe-price", money(quote.currency, quote.total_price)));
        row.appendChild(el("td", "", delivery(quote)));
        row.appendChild(el("td", "", quote.confidence == null ? "Not stated" : `${Math.round(Number(quote.confidence) * 100)}%`));
        const evidenceCell = el("td", "qe-evidence-cell");
        const evidenceText = evidenceString(quote.evidence);
        evidenceCell.appendChild(el("span", "", evidenceText || "No supporting evidence returned."));
        if (quote.notes) evidenceCell.appendChild(el("small", "qe-note", quote.notes));
        const select = button(
          String(evaluation.selected_message_id) === String(quote.message_id) ? "Chosen" : "Choose",
          `qe-choose-button${String(evaluation.selected_message_id) === String(quote.message_id) ? " is-chosen" : ""}`,
          () => chooseQuote(quote.message_id)
        );
        select.disabled = evaluation.status !== "completed";
        select.setAttribute("aria-label", `Choose ${quote.vendor || quote.client_name || "quotation"}`);
        evidenceCell.appendChild(select);
        row.appendChild(evidenceCell);
        return row;
      }

      function renderQuoteCard(quote, evaluation, recommendedId) {
        const card = el("article", `qe-quote-card${String(quote.message_id) === String(recommendedId) ? " is-recommended" : ""}`);
        const top = el("div", "qe-quote-card-top");
        const label = el("div", "");
        label.append(el("span", "qe-quote-rank", quote.rank ? `#${quote.rank}` : "Quotation"), el("h3", "", quote.vendor || quote.client_name || quote.filename || "Quotation"));
        if (String(quote.message_id) === String(recommendedId)) label.appendChild(el("span", "qe-recommended", "Suggested for selected priority"));
        top.append(label, el("strong", "qe-card-price", money(quote.currency, quote.total_price)));
        card.appendChild(top);
        const facts = el("div", "qe-card-facts");
        facts.append(fact("Delivery", delivery(quote)), fact("Confidence", quote.confidence == null ? "Not stated" : `${Math.round(Number(quote.confidence) * 100)}%`), fact("Price basis", quote.price_basis || "Not stated"));
        card.appendChild(facts);
        const evidence = evidenceString(quote.evidence);
        card.appendChild(el("p", "qe-card-evidence", evidence || "No supporting evidence returned."));
        if (quote.notes) card.appendChild(el("p", "qe-card-note", quote.notes));
        if (quote.extraction_error) card.appendChild(el("p", "qe-inline-error", quote.extraction_error));
        if (quote.comparable === false) card.appendChild(el("p", "qe-warning", "This quotation was marked as not comparable."));
        const selected = String(evaluation.selected_message_id) === String(quote.message_id);
        const choose = button(selected ? "Your choice recorded" : evaluation.selected_message_id ? "Change to this quotation" : "Choose this quotation", `qe-primary-button qe-card-choose${selected ? " is-chosen" : ""}`, () => chooseQuote(quote.message_id));
        choose.disabled = evaluation.status !== "completed";
        card.appendChild(choose);
        return card;
      }

      function fact(label, value) {
        const item = el("div", "qe-fact");
        item.append(el("span", "", label), el("strong", "", value));
        return item;
      }
    }

    function rememberEvaluation(evaluation) {
      if (!evaluation || evaluation.id === undefined || evaluation.id === null) return;
      state.evaluations = [
        evaluation,
        ...state.evaluations.filter((item) => String(item.id) !== String(evaluation.id))
      ].slice(0, 20);
    }

    function sectionHeading(number, title, meta) {
      const heading = el("div", "qe-section-heading");
      heading.append(el("span", "qe-section-number", number));
      const copy = el("div", "qe-section-heading-copy");
      copy.append(el("h2", "", title), el("span", "", meta));
      heading.appendChild(copy);
      return heading;
    }

    function statePanel(title, message, actionLabel, action) {
      const panel = el("div", "qe-state-panel");
      panel.append(el("span", "qe-state-mark", "—"), el("strong", "", title), el("p", "", message));
      if (actionLabel && action) panel.appendChild(button(actionLabel, "qe-secondary-button", action));
      return panel;
    }

    function statusText(status) {
      return ({ queued: "Queued", processing: "Processing", completed: "Analysis complete", failed: "Analysis failed" })[status] || "Status unavailable";
    }
    function isActive(evaluation) {
      return evaluation.status === "queued" || evaluation.status === "processing";
    }
    function money(currency, value) {
      if (value === null || value === undefined || value === "") return "Not stated";
      const number = Number(value);
      if (!Number.isFinite(number)) return `${currency ? `${currency} ` : ""}${value}`;
      try {
        return new Intl.NumberFormat(undefined, { style: currency ? "currency" : "decimal", currency: currency || undefined, maximumFractionDigits: 2 }).format(number);
      } catch (_) {
        return `${currency ? `${currency} ` : ""}${number.toLocaleString()}`;
      }
    }
    function delivery(quote) {
      if (quote.delivery_text) return quote.delivery_text;
      if (quote.delivery_date) return formatDate(quote.delivery_date);
      if (quote.delivery_days !== null && quote.delivery_days !== undefined) {
        return `${quote.delivery_days}${quote.delivery_unit ? ` ${quote.delivery_unit}` : " days"}`;
      }
      return "Not stated";
    }
    function evidenceString(evidence) {
      if (typeof evidence === "string") return evidence;
      if (Array.isArray(evidence)) return evidence.map((item) => typeof item === "string" ? item : item && (item.text || item.quote || item.evidence) || "").filter(Boolean).join(" · ");
      if (evidence && typeof evidence === "object") return Object.keys(evidence).map((key) => `${key}: ${String(evidence[key])}`).join(" · ");
      return "";
    }

    async function loadCandidates() {
      state.loading = true;
      state.error = "";
      render();
      try {
        const conversationId = options.conversationId || (options.conversation && options.conversation.id) || (options.requestContext && options.requestContext.conversation_id);
        const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
        const [response, history] = await Promise.all([
          request(`/api/quote-candidates${query}`),
          request("/api/quote-evaluations")
        ]);
        state.candidates = Array.isArray(response && response.candidates) ? response.candidates : [];
        state.evaluations = Array.isArray(history && history.evaluations) ? history.evaluations : [];
        if (!state.evaluation && state.evaluations.length) {
          state.evaluation = state.evaluations[0];
          if (isActive(state.evaluation)) {
            window.setTimeout(() => pollEvaluation(state.evaluation.id), 0);
          }
        }
        const validIds = new Set(state.candidates.filter(isSelectable).map((candidate) => candidate.id));
        state.selected.forEach((id) => { if (!validIds.has(id)) state.selected.delete(id); });
      } catch (error) {
        state.error = error && error.message ? error.message : "Could not load quotation sources.";
      } finally {
        state.loading = false;
        render();
      }
    }

    async function startEvaluation() {
      if (state.submitting || !state.selected.size) return;
      state.submitting = true;
      state.error = "";
      render();
      try {
        const response = await request("/api/quote-evaluations", {
          method: "POST",
          body: { message_ids: Array.from(state.selected), priority: state.priority }
        });
        state.evaluation = response && response.evaluation;
        if (!state.evaluation || !state.evaluation.id) throw new Error("The analysis could not be started.");
        rememberEvaluation(state.evaluation);
        toast("Quotation analysis started.");
        render();
        if (isActive(state.evaluation)) pollEvaluation(state.evaluation.id);
      } catch (error) {
        state.error = error && error.message ? error.message : "Could not start quotation analysis.";
      } finally {
        state.submitting = false;
        render();
      }
    }

    async function pollEvaluation(id) {
      clearPoll();
      if (state.destroyed) return;
      try {
        const response = await request(`/api/quote-evaluations/${encodeURIComponent(id)}`);
        if (response && response.evaluation) {
          state.evaluation = response.evaluation;
          rememberEvaluation(state.evaluation);
          render();
          if (isActive(state.evaluation)) state.pollTimer = window.setTimeout(() => pollEvaluation(id), 1800);
          return;
        }
        state.error = "The analysis status could not be read.";
      } catch (error) {
        state.error = error && error.message ? error.message : "Could not refresh analysis status.";
        state.pollTimer = window.setTimeout(() => pollEvaluation(id), 3500);
      }
      render();
    }

    async function loadEvaluation(id) {
      clearPoll();
      try {
        const response = await request(`/api/quote-evaluations/${encodeURIComponent(id)}`);
        state.evaluation = response && response.evaluation;
        rememberEvaluation(state.evaluation);
        render();
        if (state.evaluation && isActive(state.evaluation)) pollEvaluation(state.evaluation.id);
      } catch (error) {
        state.error = error && error.message ? error.message : "Could not load this comparison.";
        render();
      }
    }

    async function chooseQuote(messageId) {
      if (!state.evaluation || state.evaluation.status !== "completed" ||
          String(state.evaluation.selected_message_id) === String(messageId)) return;
      const id = state.evaluation.id;
      state.error = "";
      try {
        const response = await request(`/api/quote-evaluations/${encodeURIComponent(id)}/selection`, {
          method: "POST",
          body: { message_id: messageId }
        });
        if (response && response.evaluation) {
          state.evaluation = response.evaluation;
          rememberEvaluation(state.evaluation);
        }
        toast("Your quotation choice has been recorded.");
      } catch (error) {
        state.error = error && error.message ? error.message : "Could not record your quotation choice.";
        toast(state.error);
      }
      render();
    }

    const instance = {
      destroy() {
        state.destroyed = true;
        clearPoll();
        if (container) container.replaceChildren();
      },
      refresh: loadCandidates
    };
    activeInstance = instance;
    loadCandidates();
    return instance;
  }

  function unmount() {
    if (activeInstance && typeof activeInstance.destroy === "function") activeInstance.destroy();
    activeInstance = null;
  }

  window.QuoteEvaluator = { mount, unmount };
})();
