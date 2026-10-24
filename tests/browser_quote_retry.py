"""Exercise retry review and ranking notices with an isolated staff archive."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from src.quote_analysis import QuoteAnalysisError, QuoteAnalysisWorker
from src.server import create_app


class QueuedWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, evaluation_id):
        self.jobs.append(evaluation_id)


def main():
    outputs = Path("generated-artifacts/quote-retry")
    outputs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        queue = QueuedWorker()
        app = create_app({
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": directory,
            "SECRET_KEY": "browser-retry-test-session",
            "STAFF_PASSWORD": "browser-retry-password",
            "RUNPOD_API_KEY": "test-only-key", "QUOTE_WORKER": queue,
        })
        store = app.extensions["store"]
        contact = store.get_or_create_conversation("test-phone", "test-contact", "Retry customer")
        ids = ["retry-good", "retry-timeout"]
        for message_id, amount in zip(ids, [120, 80]):
            store.insert_inbound(contact["id"], {
                "id": message_id, "type": "text", "body": f"MYR {amount}, delivery in 3 days.",
                "timestamp": int(time.time()), "raw": {}, "caption": "",
                "filename": "", "mime_type": "", "media_id": None, "context_id": None,
            })
        worker = QuoteAnalysisWorker(store, "https://example.api.runpod.ai", "test-only-key")
        worker.model_id = "test-model"
        _, digests = worker.preview_sources(store.quote_preview_inputs(ids))
        original = store.create_quote_evaluation(ids, "price", reviewed_digests=digests)

        def mixed(parts, *_):
            if "Quotation QUOTE_2" in str(parts):
                raise QuoteAnalysisError("RunPod comparison request timed out.")
            return {"quotes": [{
                "quote_key": "QUOTE_1", "vendor": "Saved supplier", "currency": "MYR",
                "total_price": 120,
            }]}

        with patch.object(worker, "_request_model", side_effect=mixed):
            worker._analyze(original["id"])
        store.select_quote(original["id"], ids[0])
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=os.getenv("CHROMIUM_PATH") or shutil.which("chromium"),
                    headless=True, args=["--no-sandbox"],
                )
                page = browser.new_page(viewport={"width": 1100, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.get_by_label("Staff password").fill("browser-retry-password")
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.get_by_role("button", name="Compare quotations", exact=True).click()
                # The action must work on initial saved-history loading, not just polling.
                page.get_by_role("button", name="Review and retry failed sources", exact=True).click()
                review = page.locator(".qe-review")
                expect(review).to_contain_text("only failed or unfinished sources are sent")
                send = page.get_by_role("button", name="Retry failed sources only", exact=True)
                expect(send).to_be_disabled()
                review.locator("input[type=checkbox]").nth(0).check()
                expect(send).to_be_disabled()
                review.locator("input[type=checkbox]").nth(1).check()
                expect(send).to_be_enabled()
                send.click()
                page.get_by_role("button", name="Stop comparison", exact=True).wait_for()
                assert len(queue.jobs) == 1
                retry_id = queue.jobs[0]
                expect(page.locator(".qe-partial-note")).to_be_visible()
                with patch.object(worker, "_request_model", return_value={"quotes": [{
                    "quote_key": "QUOTE_2", "vendor": "Recovered supplier",
                    "currency": "MYR", "total_price": 80,
                }]}) as request:
                    worker._analyze(retry_id)
                request.assert_called_once()
                expect(page.get_by_text("Final ranking changed.", exact=False)).to_be_visible()
                expect(page.get_by_text(
                    "Recommendation changed from Saved supplier to Recovered supplier.", exact=False,
                )).to_be_visible()
                expect(page.get_by_role("button", name="Review and retry failed sources", exact=True)).to_have_count(0)
                for width, height in [(1100, 900), (390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    notice = page.get_by_text("Final ranking changed.", exact=False)
                    notice.scroll_into_view_if_needed()
                    expect(notice).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(outputs / f"ranking-{width}.png"))
                assert store.quote_evaluation(original["id"])["selected_message_id"] == ids[0]
                assert store.quote_evaluation(retry_id)["selected_message_id"] is None
                with store.db() as db:
                    assert db.execute("SELECT COUNT(*) FROM messages WHERE direction='outbound'").fetchone()[0] == 0
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            thread.join(5)
    print("Retry browser checks passed: reviewed retry, retained facts, changed ranking, desktop and mobile.")


if __name__ == "__main__":
    main()
