"""Exercise live timers, polling, focus, partial facts and terminal states."""
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


class QueuedWorker:
    def submit(self, evaluation_id):
        pass


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app({
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": directory,
            "SECRET_KEY": "synthetic-timing-session",
            "STAFF_PASSWORD": "synthetic-timing-password", "QUOTE_WORKER": QueuedWorker(),
        })
        store = app.extensions["store"]
        contact = store.get_or_create_conversation("synthetic-phone", "synthetic-contact", "Synthetic")
        store.insert_inbound(contact["id"], {
            "id": "timing-source", "type": "text", "body": "MYR 20, delivery 2 days",
            "timestamp": int(time.time()), "raw": {}, "caption": "",
            "filename": "", "mime_type": "", "media_id": None, "context_id": None,
        })
        job = store.create_quote_evaluation(["timing-source"], "price")
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    executable_path=os.getenv("CHROMIUM_PATH") or shutil.which("chromium"),
                    headless=True, args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1100, "height": 950})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.get_by_label("Staff password").fill("synthetic-timing-password")
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.get_by_role("button", name="Compare quotations", exact=True).click()
                expect(page.locator(".qe-timings")).to_be_visible()
                expect(page.locator('[data-qe-timing="queue"]')).to_contain_text("running")
                store.update_quote_evaluation(job["id"], "processing", result={
                    "partial": True, "quotes": [],
                    "progress": {"stage": "reading", "completed": 0, "total": 1}})
                reading = page.locator('[data-qe-timing="reading"]')
                expect(reading).to_contain_text("running", timeout=6000)
                stop = page.get_by_role("button", name="Stop comparison", exact=True)
                stop.focus()
                node = stop.element_handle()
                before = reading.inner_text()
                page.wait_for_function(
                    "(before) => document.querySelector('[data-qe-timing=\"reading\"]').textContent !== before",
                    arg=before)
                page.wait_for_timeout(2000)  # Allow an unchanged poll as well as timer ticks.
                assert node.evaluate("(node) => node === document.activeElement && node.isConnected")
                partial = {
                    "partial": True,
                    "quotes": [{"message_id": "timing-source", "vendor": "Synthetic supplier",
                                "currency": "MYR", "total_price": 20}],
                    "progress": {"stage": "connecting", "completed": 0, "total": 1},
                }
                store.update_quote_evaluation(job["id"], "processing", result=partial)
                expect(page.locator('[data-qe-timing="connecting"]')).to_contain_text("running", timeout=6000)
                expect(page.locator(".qe-partial-note")).to_contain_text("Final ranking")
                expect(page.locator(".qe-table")).to_contain_text("Synthetic supplier")
                partial["progress"]["stage"] = "analyzing"
                store.update_quote_evaluation(job["id"], "processing", result=partial)
                expect(page.locator('[data-qe-timing="analyzing"]')).to_contain_text("running", timeout=6000)
                # Cancellation stays responsive while remote inference is outstanding.
                stop.click()
                expect(page.locator(".qe-cancelled-note")).to_be_visible()
                frozen = page.locator(".qe-timing-list").inner_text()
                page.wait_for_timeout(1200)
                assert page.locator(".qe-timing-list").inner_text() == frozen
                expect(page.locator(".qe-table")).to_contain_text("Synthetic supplier")
                output = Path("generated-artifacts/quote-timing")
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / "desktop.jpg"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator(".qe-timings").evaluate(
                    "(node) => node.scrollWidth <= node.clientWidth")
                page.screenshot(path=str(output / "mobile.jpg"), full_page=True)
                # A separate failed comparison displays frozen timings and its error.
                failed = store.create_quote_evaluation(["timing-source"], "price")
                store.update_quote_evaluation(failed["id"], "processing", result=partial)
                store.update_quote_evaluation(failed["id"], "failed", error="Synthetic endpoint failure")
                page.get_by_role("button", name="Back to home", exact=True).click()
                page.get_by_role("button", name="Compare quotations", exact=True).click()
                expect(page.locator(".qe-inline-error")).to_contain_text("Synthetic endpoint failure")
                expect(page.locator(".qe-timing-list")).not_to_contain_text("running")
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print("Comparison timing browser checks passed.")


if __name__ == "__main__":
    main()
