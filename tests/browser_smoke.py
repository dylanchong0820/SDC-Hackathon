"""Exercise the real UI with an isolated archive and fake Meta transport.

No live customers, secrets or business-number sends are used. Install
requirements-dev.txt. Set CHROMIUM_PATH if Chromium isn't at the Replit path.
"""
import base64
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from src.server import create_app
from src.whatsapp import process_event

CHROMIUM_PATH = os.getenv(
    "CHROMIUM_PATH",
    "/nix/store/71577rskzyhch3axhdqx7faygc2xyn4v-playwright-browsers-1.55.0-with-cjk/"
    "chromium-1187/chrome-linux/chrome",
)


class FakeGraph:
    def __init__(self):
        self.sent = []

    def send_text(self, phone, recipient, text):
        self.sent.append(text)
        return f"wamid.browser-out-{len(self.sent)}"


def run():
    root = Path(__file__).resolve().parent.parent
    outputs = root / "generated-artifacts" / "browser-check"
    outputs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as archive:
        graph = FakeGraph()
        app = create_app({
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
                # A second employee must drop cached messages when another clears history.
                colleague_context = browser.new_context(viewport={"width": 1280, "height": 800})
                colleague = colleague_context.new_page()
                colleague.on("pageerror", lambda error: errors.append(str(error)))
                colleague.goto(url)
                colleague.get_by_label("Staff password", exact=True).fill("isolated-browser-test-password")
                colleague.get_by_role("button", name="Sign in", exact=True).click()
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
        print("Browser smoke passed: login, media, fake-Meta sends, drafts, settings, chat/all deletion, attachment choices, colleague cache refresh, responsive dialogs.")
        print("Screenshots in generated-artifacts/browser-check (isolated fixture data only).")


if __name__ == "__main__":
    run()
