"""Exercise the real UI with an isolated archive and fake Meta transport.

No live customers, secrets or business-number sends are used. Install
requirements-dev.txt. Set CHROMIUM_PATH if Chromium isn't at the Replit path.
"""
import base64
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from src.server import create_app
from src.quote_analysis import rank_quotes
from src.whatsapp import process_event

CHROMIUM_PATH = os.getenv("CHROMIUM_PATH") or shutil.which("chromium")


class FakeGraph:
    def __init__(self):
        self.sent = []

    def send_text(self, phone, recipient, text):
        self.sent.append(text)
        return f"wamid.browser-out-{len(self.sent)}"


class FakeQuoteWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, evaluation_id):
        self.jobs.append(evaluation_id)


def check_chat_layout(page, outputs):
    """Exercise real chat rendering with synthetic replies, without calling RunPod."""
    question = "Please compare these quotations:\n" + "supplier_reference_" * 70
    reply = ("Price and delivery notes: MYR 450.00; 原价 USD 100.00.\n" * 45
             + "https://example.invalid/quotation/" + "long-reference-" * 90)
    def fake_chat(route):
        route.fulfill(json={
            "reply": reply, "items": [],
            "pdf_base64": base64.b64encode(b"%PDF-1.4 isolated-layout-fixture").decode(),
            "pdf_filename": "layout-fixture.pdf", "comparison_count": 1, "quote_count": 2,
        })
    page.route("**/api/quote-chat", fake_chat)
    try:
        for width, height in ((280, 320), (320, 360), (390, 844), (701, 400),
                              (850, 500), (1024, 450), (1440, 900)):
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_role("button", name="Workspace home", exact=True).click()
            page.get_by_role("button", name="Quote AI chat", exact=True).click()
            expect(page.locator(".qc-empty")).to_be_visible()
            assert page.locator(".qc-empty").evaluate("""el => {
                const last = el.lastElementChild.getBoundingClientRect();
                return last.bottom <= el.getBoundingClientRect().bottom + 1;
            }"""), f"Empty conversation content escapes its box at {width}x{height}"
            page.locator(".qc-input").fill(question)
            page.locator(".qc-send").click()
            expect(page.locator(".qc-turn.is-assistant")).to_be_visible()
            assert page.locator(".qc-transcript").evaluate("""el =>
                el.scrollHeight > el.clientHeight && el.scrollWidth <= el.clientWidth + 1
            """), f"Chat must scroll vertically without horizontal overflow at {width}x{height}"
            assert page.locator(".qc-root").evaluate("""root =>
                [...root.querySelectorAll('.qc-turn, .qc-turn-body, .qc-prompt, .qc-composer, .qc-panel-head')]
                    .every(el => el.scrollWidth <= el.clientWidth + 1 &&
                        el.scrollHeight <= el.clientHeight + 1)
            """), f"Conversation text or controls escape their boxes at {width}x{height}"
            assert page.locator(".qc-turn").evaluate_all("""turns =>
                turns.every((turn, i) => !i ||
                    turn.getBoundingClientRect().top >= turns[i-1].getBoundingClientRect().bottom)
            """), f"Long conversation bubbles overlap at {width}x{height}"
            transcript = page.locator(".qc-transcript")
            transcript.evaluate("el => el.scrollTop = 0")
            expect(page.locator(".qc-turn.is-user")).to_be_in_viewport()
            transcript.evaluate("el => el.scrollTop = el.scrollHeight")
            download = page.get_by_role("button", name="Download this Quote AI response as a PDF", exact=True)
            download.scroll_into_view_if_needed()
            expect(download).to_be_in_viewport()
            page.locator(".qc-input").scroll_into_view_if_needed()
            expect(page.locator(".qc-input")).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            if width in (320, 1024):
                page.locator(".qc-transcript-panel").screenshot(
                    path=str(outputs / f"chat-conversation-{width}.png"), animations="disabled")
    finally:
        page.unroute("**/api/quote-chat", fake_chat)


def run():
    root = Path(__file__).resolve().parent.parent
    outputs = root / "generated-artifacts" / "browser-check"
    outputs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as archive:
        graph = FakeGraph()
        quote_worker = FakeQuoteWorker()
        app = create_app({
            "QUOTE_WORKER": quote_worker,
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": archive,
            "SECRET_KEY": "isolated-browser-test-session-only",
            "STAFF_PASSWORD": "isolated-browser-test-password",
            "META_ACCESS_TOKEN": "not-a-live-token", "META_APP_SECRET": "not-a-live-secret",
            "WEBHOOK_VERIFY_TOKEN": "test-only", "GRAPH_CLIENT": graph,
        })
        store = app.extensions["store"]
        now = int(time.time())
        process_event(store, {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "123456789", "display_phone_number": "+60 123 456 789"},
            "contacts": [{"wa_id": "60123456789", "profile": {"name": "Browser Test Contact"}}],
            "messages": [
                {"id": "wamid.browser-text", "from": "60123456789", "type": "text",
                 "timestamp": str(now - 300), "text": {"body": "Can you review the attached documents?"}},
                {"id": "wamid.browser-image", "from": "60123456789", "type": "image",
                 "timestamp": str(now - 240), "image": {"id": "111", "caption": "Reference image"}},
                {"id": "wamid.browser-document", "from": "60123456789", "type": "document",
                 "timestamp": str(now - 180), "document": {"id": "112", "filename": "test-document.pdf"}},
                {"id": "wamid.browser-location", "from": "60123456789", "type": "location",
                 "timestamp": str(now - 120), "location": {"latitude": 3.14, "longitude": 101.5, "name": "Office"}},
            ],
        }}]}]})
        image = store.attachments / "image" / "fixture.png"
        image.parent.mkdir(exist_ok=True)
        image.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ+0AAAAASUVORK5CYII="))
        store.media_saved(store.message("wamid.browser-image"), image, "image/png", "reference.png")
        document = store.attachments / "document" / "fixture.pdf"
        document.parent.mkdir(exist_ok=True)
        document.write_bytes(b"%PDF-1.4\n% isolated download test\n%%EOF")
        store.media_saved(store.message("wamid.browser-document"), document, "application/pdf", "test-document.pdf")
        active_comparison = store.create_quote_evaluation(["wamid.browser-text"], "balanced")
        store.update_quote_evaluation(active_comparison["id"], "processing")
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=CHROMIUM_PATH, headless=True,
                                                       args=["--no-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.get_by_label("Staff password").fill("isolated-browser-test-password")
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.get_by_role("button", name="Inbox. Customer conversations", exact=True).click()
                page.get_by_text("Browser Test Contact", exact=True).first.wait_for()
                # Empty selection or initial auto-selection are both valid designs.
                if not page.get_by_label("Message", exact=True).is_visible():
                    page.get_by_text("Browser Test Contact", exact=True).first.click()
                expect(page.get_by_label("Message", exact=True)).to_be_enabled()
                expect(page.get_by_text("Can you review the attached documents?", exact=True)).to_be_visible()
                expect(page.get_by_text("test-document.pdf", exact=True)).to_be_visible()
                expect(page.get_by_text("Reference image", exact=True)).to_be_visible()
                page.screenshot(path=str(outputs / "desktop.png"), full_page=True, animations="disabled")
                page.get_by_label("Message", exact=True).fill("I have received your documents.")
                page.get_by_role("button", name="Send message", exact=True).click()
                expect(page.get_by_text("I have received your documents.", exact=True).last).to_be_visible()
                assert graph.sent == ["I have received your documents."], "Text must be sent once through Meta transport"
                expect(page.get_by_label("Message", exact=True)).to_have_value("")
                # Verify document download returns the exact isolated fixture.
                response = page.request.get(url + "/api/media/wamid.browser-document?download=1")
                assert response.status == 200 and response.body().startswith(b"%PDF")
                # Verify a polling round preserves drafts and current conversation.
                page.get_by_label("Message", exact=True).fill("Draft retained")
                page.wait_for_timeout(5500)
                expect(page.get_by_label("Message", exact=True)).to_have_value("Draft retained")
                # Show settings and save a business phone ID using normal API/UI.
                page.get_by_role("button", name="Settings", exact=True).click()
                page.get_by_text("Meta App Dashboard", exact=False).first.wait_for()
                export_file = outputs / "history-export.txt"
                export_file.write_text(
                    "2024-01-02, 10:00 - Browser Test Contact: This is the archived needle\n"
                    "2024-01-02, 10:01 - Support Team: I will review the old file\n",
                    encoding="utf-8",
                )
                page.get_by_label("WhatsApp chat export (.txt)", exact=True).set_input_files(str(export_file))
                page.get_by_label("Client's international phone number", exact=True).fill("+60123456789")
                page.get_by_label("Client name (optional)", exact=True).fill("Browser Test Contact")
                page.get_by_label("Your name in the export (optional)", exact=True).fill("Support Team")
                page.get_by_label("Date format for ambiguous dates", exact=True).select_option("YMD")
                page.get_by_label("Time zone of the export", exact=True).fill("UTC")
                page.get_by_role("button", name="Import chat history", exact=True).click()
                expect(page.get_by_text("Chat history imported. No WhatsApp messages were sent.")).to_be_visible()
                expect(page.get_by_text("This is the archived needle", exact=True)).to_be_visible()
                assert graph.sent == ["I have received your documents."], "History import must not send a WhatsApp message"
                page.get_by_role("button", name="Search messages", exact=True).click()
                page.get_by_label("Search saved message text", exact=True).fill("archived needle")
                page.locator(".history-result").first.click()
                expect(page.get_by_text("This is the archived needle", exact=True)).to_be_visible()
                page.get_by_role("button", name="Settings", exact=True).click()
                page.get_by_text("Meta App Dashboard", exact=False).first.wait_for()
                page.get_by_label("Or enter ID manually", exact=True).fill("987654321")
                page.get_by_role("button", name="Save default", exact=True).click()
                expect(page.get_by_text("Default number saved.", exact=True)).to_be_visible()
                assert store.setting("phone_number_id") == "987654321"
                page.screenshot(path=str(outputs / "settings.png"), full_page=True, animations="disabled")
                # Mobile layout should have no document-level horizontal overflow.
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("button", name="Save default", exact=True).click()
                page.screenshot(path=str(outputs / "mobile.png"), full_page=True, animations="disabled")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Mobile page overflows"
                # Every staff tool must remain reachable in narrow, short windows.
                for viewport in ({"width": 390, "height": 420}, {"width": 320, "height": 360},
                                 {"width": 641, "height": 450}, {"width": 1024, "height": 450}):
                    page.set_viewport_size(viewport)
                    navigation_bar = page.get_by_role("navigation", name="Workspace tools")
                    for label in ("Home", "Compare quotes", "Quote register", "AI chat",
                                  "Setup checklist", "Settings", "Sign out"):
                        expect(navigation_bar.get_by_text(label, exact=True)).to_be_visible()
                    page.locator(".topbar").screenshot(path=str(outputs / f"header-{viewport['width']}.png"))
                    for navigation, selector in (
                        ("Workspace home", ".wh-root"),
                        ("Compare quotations", ".qe-root"),
                        ("Quotation register", ".qr-root"),
                        ("Quote AI chat", ".qc-root"),
                        ("Configuration checklist", ".settings-view"),
                        ("Settings", ".settings-view"),
                    ):
                        page.get_by_role("button", name=navigation, exact=True).click()
                        page.locator(selector).wait_for()
                        expect(page.get_by_role("button", name=navigation, exact=True)).to_have_attribute("aria-current", "page")
                        if navigation == "Compare quotations" and viewport["width"] == 390:
                            page.get_by_role("button", name="Stop comparison", exact=True).click()
                            expect(page.get_by_text(
                                "Comparison stopped. You can change the selected sources or priority and start another comparison."
                            )).to_be_visible()
                            page.locator(".qe-source").filter(
                                has_text="Can you review the attached documents?").locator("input").check()
                            page.locator(".qe-source").filter(
                                has_text="test-document.pdf").locator("input").check()
                            page.get_by_role("button", name="Review selected", exact=True).click()
                            page.locator(".qe-review").wait_for()
                            expect(page.locator(".qe-review-text")).to_contain_text("Can you review the attached documents?")
                            expect(page.locator(".qe-review .qe-inline-error")).to_be_visible()
                            expect(page.get_by_role("button", name="Send confirmed to AI (0)", exact=True)).to_be_disabled()
                            assert not quote_worker.jobs, "Preview unexpectedly queued AI analysis"
                            page.screenshot(path=str(outputs / "quote-review-mobile.png"), full_page=True)
                            page.locator(".qe-review-source").filter(
                                has_text="Can you review the attached documents?").locator("input").check()
                            page.get_by_role("button", name="Send confirmed to AI (1)", exact=True).click()
                            page.locator(".qe-review").wait_for(state="detached")
                            assert len(quote_worker.jobs) == 1
                            reviewed_inputs = store.quote_evaluation_inputs(quote_worker.jobs[0])
                            assert [row["id"] for row in reviewed_inputs["messages"]] == ["wamid.browser-text"]
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
                            f"{navigation} overflows at {viewport}"
                        )
                        page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
                        assert page.evaluate(
                            "scrollY + innerHeight >= document.scrollingElement.scrollHeight - 2"
                        ), f"{navigation} cannot scroll to the bottom at {viewport}"
                        page.evaluate("window.scrollTo(0, 0)")
                        home_brand = page.get_by_role("button", name="Quote Bot AI: workspace home", exact=True)
                        if navigation == "Settings":
                            home_brand.focus()
                            home_brand.press("Enter")
                        else:
                            home_brand.click()
                        expect(page.locator(".wh-root")).to_be_visible()
                    page.get_by_role("button", name="Workspace home", exact=True).click()
                    page.get_by_role("button", name="Inbox. Customer conversations", exact=True).click()
                    if viewport["width"] <= 640 and page.get_by_label("Message", exact=True).is_visible():
                        page.get_by_role("button", name="Back to conversations", exact=True).click()
                    page.get_by_role("button", name="New conversation", exact=True).click()
                    expect(page.locator(".settings-view")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
                        f"New conversation overflows at {viewport}"
                    )
                    page.get_by_role("button", name="Workspace home", exact=True).click()
                    page.get_by_role("button", name="Inbox. Customer conversations", exact=True).click()
                    if not page.get_by_label("Message", exact=True).is_visible():
                        page.get_by_text("Browser Test Contact", exact=True).first.click()
                    expect(page.get_by_label("Message", exact=True)).to_be_visible()
                    assert page.locator(".message-scroll").evaluate(
                        "el => getComputedStyle(el).overflowY === 'auto' && el.clientHeight > 0"
                    ), f"Inbox messages have no scroll area at {viewport}"
                # Fixed exchange rates exercise comparison/register views without a live provider.
                ocr_warning = ("This scanned PDF has 8 pages. Only the first 5 pages are included "
                               "in OCR and AI analysis; pages 6–8 are omitted. Check the original "
                               "attachment before choosing a quotation.")
                currency_evaluation = store.create_quote_evaluation(
                    ["wamid.browser-text", "wamid.browser-document"], "price")
                currency_result = rank_quotes([
                    {"message_id": "wamid.browser-text", "client_name": "USD Supplier",
                     "currency": "USD", "total_price": 100},
                    {"message_id": "wamid.browser-document", "client_name": "MYR Supplier",
                     "currency": "MYR", "total_price": 500, "pdf_page_count": 8,
                     "ocr_page_limit": 5, "ocr_truncated": True, "source_warnings": [ocr_warning]},
                ], "price", {"USD": {"rate": "4.5", "date": "2026-10-23", "source": "Fixture rates"}})
                store.update_quote_evaluation(currency_evaluation["id"], "completed", result=currency_result)
                for currency_viewport in ({"width": 1280, "height": 900}, {"width": 390, "height": 844}):
                    page.set_viewport_size(currency_viewport)
                    page.get_by_role("button", name="Compare quotations", exact=True).click()
                    expect(page.locator(".qe-results")).to_contain_text("Original: USD")
                    expect(page.locator(".qe-results")).to_contain_text("450.00")
                    expect(page.locator(".qe-results")).to_contain_text("2026-10-23")
                    expect(page.locator(".qe-results .qe-source-warning:visible").first).to_contain_text("8 pages")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Currency comparison overflows"
                    page.screenshot(path=str(outputs / f"currency-comparison-{currency_viewport['width']}.png"), full_page=True)
                    page.get_by_role("button", name="Quotation register", exact=True).click()
                    expect(page.locator(".qr-root")).to_contain_text("Original: USD")
                    expect(page.locator(".qr-root")).to_contain_text("450.00")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(outputs / f"currency-register-{currency_viewport['width']}.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("button", name="Settings", exact=True).click()
                # The review warning must appear beside the selected file and its confirmation.
                def fake_ocr_preview(route):
                    route.fulfill(json={
                        "sources": [{"id": "wamid.browser-document", "filename": "test-document.pdf",
                                     "text": "OCR of the first five pages: MYR 500", "error": "",
                                     "pdf_page_count": 8, "ocr_page_limit": 5, "ocr_truncated": True,
                                     "source_warnings": [ocr_warning]}],
                        "review_token": "isolated-ui-fixture-not-submitted", "expires_in": 600,
                    })
                page.route("**/api/quote-previews", fake_ocr_preview)
                try:
                    page.get_by_role("button", name="Compare quotations", exact=True).click()
                    pdf_source = page.locator(".qe-source").filter(has_text="test-document.pdf")
                    pdf_source.locator('input[type="checkbox"]').check()
                    page.get_by_role("button", name="Review selected", exact=True).click()
                    expect(page.locator(".qe-review .qe-source-warning")).to_contain_text("pages 6–8 are omitted")
                    expect(pdf_source.locator(".qe-source-warning")).to_be_visible()
                    expect(page.get_by_role("checkbox", name="Confirm test-document.pdf", exact=True)).to_be_enabled()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.locator(".qe-review").screenshot(path=str(outputs / "ocr-limit-review.png"), animations="disabled")
                finally:
                    page.unroute("**/api/quote-previews", fake_ocr_preview)
                check_chat_layout(page, outputs)
                page.get_by_role("button", name="Settings", exact=True).click()
                # A second employee must drop cached messages when another clears history.
                colleague_context = browser.new_context(viewport={"width": 1280, "height": 800})
                colleague = colleague_context.new_page()
                colleague.on("pageerror", lambda error: errors.append(str(error)))
                colleague.goto(url)
                colleague.get_by_label("Staff password", exact=True).fill("isolated-browser-test-password")
                colleague.get_by_role("button", name="Sign in", exact=True).click()
                colleague.get_by_role("button", name="Inbox. Customer conversations", exact=True).click()
                colleague.get_by_text("Browser Test Contact", exact=True).first.click()
                expect(colleague.get_by_text("Can you review the attached documents?", exact=True)).to_be_visible()

                page.set_viewport_size({"width": 1280, "height": 800})
                page.get_by_role("button", name="Back to inbox", exact=True).click()
                page.get_by_role("button", name="Clear chat history", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(dialog.get_by_role("button", name="Delete history", exact=True)).to_be_disabled()
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                expect(page.get_by_text("Can you review the attached documents?", exact=True)).to_be_visible()
                page.get_by_role("button", name="Clear chat history", exact=True).click()
                expect(dialog.get_by_label("Also permanently delete saved attachments")).not_to_be_checked()
                dialog.get_by_label("Type DELETE to confirm", exact=True).fill("DELETE")
                page.screenshot(path=str(outputs / "delete-chat.png"), full_page=True, animations="disabled")
                saved_files = [Path(row["attachment_path"]) for row in
                               [store.message("wamid.browser-image"), store.message("wamid.browser-document")]]
                dialog.get_by_role("button", name="Delete history", exact=True).click()
                expect(dialog).not_to_be_visible()
                expect(page.get_by_text("Can you review the attached documents?", exact=True)).to_have_count(0)
                assert all(path.exists() for path in saved_files), "Unchecked option must retain files"
                expect(colleague.get_by_text("Can you review the attached documents?", exact=True)).to_have_count(0, timeout=12000)
                assert store.log_path.read_text() == "", "Deleted content must leave the readable archive"

                # Entire-inbox cleanup can remove the previously retained files.
                page.get_by_role("button", name="Settings", exact=True).click()
                page.get_by_role("button", name="Clear entire inbox history", exact=True).click()
                page.set_viewport_size({"width": 390, "height": 844})
                dialog.get_by_label("Also permanently delete saved attachments").check()
                dialog.get_by_label("Type DELETE ALL to confirm", exact=True).fill("DELETE ALL")
                page.screenshot(path=str(outputs / "delete-all-mobile.png"), full_page=True, animations="disabled")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Deletion dialog overflows"
                dialog.get_by_role("button", name="Delete history", exact=True).click()
                expect(dialog).not_to_be_visible()
                assert not any(path.exists() for path in saved_files), "Checked option must remove files"
                assert not store.messages(store.conversations()[0]["id"])["messages"]
                colleague_context.close()
                assert not errors, f"Browser errors: {errors}"
                browser.close()
        finally:
            server.shutdown()
        print("Browser smoke passed: login, media, fake-Meta sends, drafts, all staff screens in narrow/short windows, chat/all deletion, attachment choices, colleague cache refresh, responsive dialogs.")
        print("Screenshots in generated-artifacts/browser-check (isolated fixture data only).")


if __name__ == "__main__":
    run()
