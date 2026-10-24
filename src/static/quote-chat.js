(function () {
  "use strict";

  let activeInstance = null;
  const scriptSource = document.currentScript && document.currentScript.src;

  function ensureStylesheet() {
    if (!scriptSource) return;
    const href = new URL("quote-chat.css", scriptSource).href;
    if (!Array.from(document.querySelectorAll('link[rel="stylesheet"]')).some((link) => link.href === href)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      document.head.appendChild(link);
    }
  }

  function mount(options) {
    if (!options || !options.container || typeof options.request !== "function") {
      throw new Error("QuoteChat.mount requires a container and request helper.");
    }
    if (activeInstance) activeInstance.unmount();
    ensureStylesheet();

    const container = options.container;
    const state = {
      messages: [],
      draft: "",
      pending: false,
      error: "",
      included: null,
      destroyed: false
    };
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    };
    const button = (label, className, action) => {
      const node = el("button", className, label);
      node.type = "button";
      node.addEventListener("click", action);
      return node;
    };

    function render() {
      if (state.destroyed) return;
      const root = el("section", "qc-root");
      root.setAttribute("aria-label", "Quote AI Chat");

      const header = el("header", "qc-header");
      const brand = el("div", "qc-brand");
      const mark = el("span", "qc-brand-mark", "Q");
      mark.setAttribute("aria-hidden", "true");
      const brandCopy = el("div", "qc-brand-copy");
      brandCopy.append(el("strong", "", "Quote Bot AI"), el("span", "", "STAFF WORKSPACE"));
      brand.append(mark, brandCopy);
      const headerActions = el("div", "qc-header-actions");
      headerActions.appendChild(el("span", "qc-private-label", "Private · internal use"));
      if (typeof options.onBack === "function") {
        const back = button("Back to workspace", "qc-back", options.onBack);
        back.setAttribute("aria-label", "Back to workspace home");
        headerActions.appendChild(back);
      }
      header.append(brand, headerActions);

      const intro = el("div", "qc-intro");
      const introCopy = el("div", "qc-intro-copy");
      introCopy.append(el("p", "qc-eyebrow", "04 / Decision support"), el("h1", "qc-title", "Ask the whole record."));
      introCopy.appendChild(el("p", "qc-lede", "Work through supplier terms, pressure-test a comparison, or turn the evidence into a clear customer reply. Every reply includes a four-column PDF."));
      const scope = el("div", "qc-scope");
      scope.append(el("span", "qc-scope-label", "Context on every send"), el("strong", "", "All completed comparisons"));
      intro.append(introCopy, scope);

      const disclosure = el("aside", "qc-disclosure");
      disclosure.setAttribute("role", "note");
      const disclosureIcon = el("span", "qc-disclosure-icon", "i");
      disclosureIcon.setAttribute("aria-hidden", "true");
      const disclosureText = el("p", "qc-disclosure-copy");
      disclosureText.append(
        document.createTextNode("Each message includes facts from all saved completed quotation comparisons and is sent to the configured RunPod model. "),
        document.createTextNode("Customer phone numbers, message IDs, raw inbox attachments, and WhatsApp sending capability are not included. "),
        document.createTextNode("Each answer includes a downloadable PDF table. Chat turns stay only on this mounted page; they are not saved to inbox history.")
      );
      disclosure.append(disclosureIcon, disclosureText);

      const workspace = el("div", "qc-workspace");
      const transcriptPanel = el("section", "qc-transcript-panel");
      const panelHead = el("div", "qc-panel-head");
      panelHead.append(el("div", "qc-panel-title", "Conversation"), el("span", "qc-session-state", state.pending ? "Generating reply" : "Not saved"));
      if (state.messages.length) {
        const clear = button("Clear", "qc-clear-button", () => {
          if (state.pending) return;
          state.messages = [];
          state.error = "";
          state.included = null;
          render();
        });
        clear.setAttribute("aria-label", "Clear this temporary conversation");
        clear.disabled = state.pending;
        panelHead.appendChild(clear);
      }
      transcriptPanel.appendChild(panelHead);

      const transcript = el("div", "qc-transcript");
      transcript.setAttribute("aria-live", "polite");
      transcript.setAttribute("aria-relevant", "additions text");
      if (!state.messages.length) {
        const empty = el("div", "qc-empty");
        const emptyMark = el("span", "qc-empty-mark", "Q");
        emptyMark.setAttribute("aria-hidden", "true");
        empty.append(emptyMark, el("p", "qc-empty-kicker", "A clearer next step starts here"),
          el("h2", "", "What would you like to know?"),
          el("p", "qc-empty-copy", "Your question can draw on every completed comparison saved by your team."));
        const prompts = el("div", "qc-prompts");
        [
          "Compare the strongest price and delivery trade-offs.",
          "What should I clarify before replying to a customer?",
          "Draft a concise explanation of the recommended option."
        ].forEach((text) => {
          const prompt = button(text, "qc-prompt", () => {
            if (state.pending) return;
            state.draft = text;
            render();
            const input = container.querySelector(".qc-input");
            if (input) {
              input.focus();
              input.setSelectionRange(input.value.length, input.value.length);
            }
          });
          prompts.appendChild(prompt);
        });
        empty.appendChild(prompts);
        transcript.appendChild(empty);
      } else {
        state.messages.forEach((message) => {
          const turn = el("article", `qc-turn ${message.role === "assistant" ? "is-assistant" : "is-user"}`);
          const label = el("div", "qc-turn-label", message.role === "assistant" ? "QUOTE AI" : "YOU");
          const body = el("p", "qc-turn-body", message.displayContent || message.content);
          turn.append(label, body);
          if (message.role === "assistant" && message.pdfBase64) {
            const download = button("Download PDF", "qc-pdf-download", () => downloadPdf(message));
            download.setAttribute("aria-label", "Download this Quote AI response as a PDF");
            turn.appendChild(download);
          }
          transcript.appendChild(turn);
        });
      }
      if (state.pending) {
        const loading = el("div", "qc-loading-turn");
        loading.setAttribute("role", "status");
        loading.append(el("span", "qc-loading-label", "Quote AI is reviewing the record"),
          el("span", "qc-skeleton qc-skeleton-long"), el("span", "qc-skeleton qc-skeleton-short"));
        transcript.appendChild(loading);
      }
      transcriptPanel.appendChild(transcript);

      if (state.included) {
        const counts = el("div", "qc-counts");
        counts.setAttribute("role", "status");
        counts.append(el("span", "qc-counts-label", "Included in latest reply"),
          el("strong", "", `${numberLabel(state.included.comparison_count)} completed comparisons`),
          el("span", "qc-count-divider", "·"),
          el("strong", "", `${numberLabel(state.included.quote_count)} quotations`));
        transcriptPanel.appendChild(counts);
      }
      if (state.error) {
        const error = el("div", "qc-error");
        error.setAttribute("role", "alert");
        error.append(el("strong", "", "Reply not received"), el("span", "", state.error));
        error.appendChild(button("Try again", "qc-retry", retry));
        transcriptPanel.appendChild(error);
      }

      const composer = el("form", "qc-composer");
      const inputLabel = el("label", "qc-composer-label", "Your question or instruction");
      inputLabel.setAttribute("for", "qc-message");
      const inputRow = el("div", "qc-input-row");
      const input = el("textarea", "qc-input");
      input.id = "qc-message";
      input.name = "message";
      input.rows = 2;
      input.maxLength = 2400;
      input.placeholder = "Ask about price, delivery, or how to explain the comparison…";
      input.value = state.draft;
      input.disabled = state.pending;
      input.setAttribute("aria-describedby", "qc-composer-hint");
      input.addEventListener("input", () => {
        state.draft = input.value;
        sendButton.disabled = state.pending || !state.draft.trim();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          if (!state.pending && state.draft.trim()) submit();
        }
      });
      const sendButton = el("button", "qc-send", state.pending ? "Thinking…" : "Send");
      sendButton.type = "submit";
      sendButton.disabled = state.pending || !state.draft.trim();
      sendButton.setAttribute("aria-label", "Send question to Quote AI");
      inputRow.append(input, sendButton);
      composer.append(inputLabel, inputRow, el("p", "qc-composer-hint", "Enter to send · Shift + Enter for a new line. No message is sent to WhatsApp."));
      composer.id = "qc-composer";
      composer.addEventListener("submit", (event) => {
        event.preventDefault();
        submit();
      });
      transcriptPanel.appendChild(composer);
      workspace.appendChild(transcriptPanel);

      const side = el("aside", "qc-side");
      side.append(el("p", "qc-side-index", "HOW THIS CHAT WORKS"), el("h2", "", "Evidence in. Clarity out."));
      const steps = el("ol", "qc-steps");
      [
        ["01", "Ask", "Type a quote-related question or instruction."],
        ["02", "Ground", "Every send includes all saved completed comparison facts."],
        ["03", "Review", "Use the reply as staff guidance; decide what to share yourself."]
      ].forEach(([number, title, detail]) => {
        const item = el("li", "qc-step");
        item.append(el("span", "qc-step-number", number));
        const copy = el("div", "");
        copy.append(el("strong", "", title), el("p", "", detail));
        item.appendChild(copy);
        steps.appendChild(item);
      });
      side.appendChild(steps);
      side.append(el("div", "qc-side-foot", "A private decision aid, not an automatic customer reply."));
      workspace.appendChild(side);
      root.append(header, intro, disclosure, workspace);
      container.replaceChildren(root);
      if (state.pending) {
        const liveTranscript = container.querySelector(".qc-transcript");
        if (liveTranscript) liveTranscript.scrollTop = liveTranscript.scrollHeight;
      }
    }

    function numberLabel(value) {
      const count = Number(value);
      return Number.isFinite(count) ? new Intl.NumberFormat().format(count) : "—";
    }

    function downloadPdf(message) {
      try {
        const binary = window.atob(message.pdfBase64);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
          bytes[index] = binary.charCodeAt(index);
        }
        const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = message.pdfFilename || "quote-ai-output.pdf";
        document.body.appendChild(link);
        link.click();
        window.QuoteAnalytics?.trackEvent("report_download_requested", { format: "pdf" });
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (_error) {
        state.error = "The PDF could not be downloaded. Please ask Quote AI again.";
        render();
      }
    }

    async function submit() {
      if (state.pending || !state.draft.trim() || state.destroyed) return;
      const content = state.draft.trim();
      state.draft = "";
      state.error = "";
      if (state.messages.length && state.messages[state.messages.length - 1].role === "user") {
        state.messages.pop();
      }
      if (state.messages.length > 12) state.messages = state.messages.slice(-12);
      state.messages.push({ role: "user", content });
      await sendMessages();
    }

    async function retry() {
      if (state.pending || state.destroyed || !state.messages.length) return;
      state.error = "";
      await sendMessages();
    }

    async function sendMessages() {
      state.pending = true;
      render();
      try {
        const response = await options.request("/api/quote-chat", {
          method: "POST",
          body: {
            messages: state.messages.map((message) => ({
              role: message.role,
              content: message.content
            }))
          }
        });
        if (state.destroyed) return;
        if (!response || typeof response.reply !== "string" || !response.reply.trim() ||
            typeof response.pdf_base64 !== "string" || !response.pdf_base64) {
          throw new Error("The model reply or its PDF was not returned. Please try again.");
        }
        const reply = response.reply.trim();
        state.messages.push({
          role: "assistant",
          content: reply,
          displayContent: reply,
          pdfBase64: response.pdf_base64,
          pdfFilename: response.pdf_filename
        });
        state.included = {
          comparison_count: response.comparison_count,
          quote_count: response.quote_count
        };
        state.error = "";
        if (typeof options.toast === "function") options.toast("Quote AI replied. The PDF is ready to download.");
      } catch (error) {
        if (state.destroyed) return;
        state.error = error && error.message ? error.message : "Could not get a reply. Please try again.";
      } finally {
        state.pending = false;
        render();
      }
    }

    const instance = {
      unmount() {
        state.destroyed = true;
        container.replaceChildren();
      }
    };
    activeInstance = instance;
    render();
    return instance;
  }

  function unmount() {
    if (activeInstance) activeInstance.unmount();
    activeInstance = null;
  }

  window.QuoteChat = { mount, unmount };
})();
