"""Quote comparison uses isolated messages and never calls a live provider."""
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from src.quote_analysis import QuoteAnalysisWorker, _document_text, rank_quotes
from src.server import create_app


class QueuedWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, evaluation_id):
        self.jobs.append(evaluation_id)


class QuoteAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.worker = QueuedWorker()
        self.app = create_app({
            "TESTING": True,
            "MEDIA_WORKER": False,
            "DATA_DIR": self.directory.name,
            "SECRET_KEY": "quote-test-session-secret",
            "STAFF_PASSWORD": "quote-test-password",
            "META_APP_SECRET": "test-app-secret",
            "WEBHOOK_VERIFY_TOKEN": "test-verification-token",
            "META_ACCESS_TOKEN": "test-access-token",
            "RUNPOD_API_KEY": "not-a-real-key",
            "RUNPOD_ENDPOINT_URL": "https://example.api.runpod.ai",
            "QUOTE_WORKER": self.worker,
        })
        self.store = self.app.extensions["store"]
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/session").json["csrf_token"]
        self.conversation = self.store.get_or_create_conversation(
            "123456789", "60123456789", "Test Client")

    def tearDown(self):
        self.directory.cleanup()

    def post(self, url, body):
        return self.client.post(url, json=body, headers={"X-CSRF-Token": self.csrf})

    def login(self):
        response = self.post("/api/login", {"password": "quote-test-password"})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json["csrf_token"]

    def add_message(self, message_id, body="", kind="text", filename="", mime_type="",
                    media_id=None):
        record = {
            "id": message_id,
            "type": kind,
            "body": body,
            "caption": "",
            "timestamp": int(time.time()),
            "filename": filename,
            "mime_type": mime_type,
            "media_id": media_id,
            "raw": {},
            "context_id": None,
        }
        self.assertTrue(self.store.insert_inbound(self.conversation["id"], record))
        return self.store.message(message_id)

    def test_quote_rankings_are_deterministic_and_currency_safe(self):
        quotes = [
            {"message_id": "z-expensive", "currency": "MYR", "total_price": 150,
             "delivery_days": 7, "delivery_unit": "calendar_days"},
            {"message_id": "a-cheap", "currency": "MYR", "total_price": 100,
             "delivery_days": 10, "delivery_unit": "calendar_days"},
            {"message_id": "m-balanced", "currency": "MYR", "total_price": 110,
             "delivery_days": 2, "delivery_unit": "calendar_days"},
        ]
        result = rank_quotes(quotes, "balanced")
        self.assertEqual(result["recommendation_message_id"], "m-balanced")
        self.assertEqual(result["quotes"][0]["message_id"], "m-balanced")
        self.assertTrue(result["quotes"][0]["recommended"])

        mixed = rank_quotes([
            {"message_id": "one", "currency": "MYR", "total_price": 100},
            {"message_id": "two", "currency": "USD", "total_price": 30},
        ], "price")
        self.assertIsNone(mixed["recommendation_message_id"])
        self.assertTrue(mixed["limitations"])
        dated = rank_quotes([
            {"message_id": "later", "delivery_date": "2026-05-14"},
            {"message_id": "sooner", "delivery_date": "2026-05-02"},
        ], "time")
        self.assertEqual(dated["recommendation_message_id"], "sooner")

    def test_quote_sources_are_private_persisted_and_selectable(self):
        self.add_message("quote-one", "MYR 950, delivery within 6 calendar days.")
        self.assertEqual(self.client.get("/api/quote-candidates").status_code, 401)
        self.login()

        candidates = self.client.get("/api/quote-candidates")
        self.assertEqual(candidates.status_code, 200)
        self.assertEqual(candidates.json["candidates"][0]["id"], "quote-one")
        self.assertTrue(candidates.json["candidates"][0]["ready"])

        invalid = self.post("/api/quote-evaluations", {
            "message_ids": ["quote-one"], "priority": "random"})
        self.assertEqual(invalid.status_code, 400)

        started = self.post("/api/quote-evaluations", {
            "message_ids": ["quote-one"], "priority": "price"})
        self.assertEqual(started.status_code, 202)
        evaluation_id = started.json["evaluation"]["id"]
        self.assertEqual(self.worker.jobs, [evaluation_id])
        self.assertEqual(started.json["evaluation"]["status"], "queued")

        result = {
            "quotes": [{"message_id": "quote-one", "currency": "MYR",
                        "total_price": 950, "rank": 1}],
            "recommendation_message_id": "quote-one",
            "summary": "Lowest comparable price.",
            "limitations": [],
        }
        self.store.update_quote_evaluation(evaluation_id, "completed", result=result)
        chosen = self.post(
            f"/api/quote-evaluations/{evaluation_id}/selection",
            {"message_id": "quote-one"})
        self.assertEqual(chosen.status_code, 200)
        self.assertEqual(chosen.json["evaluation"]["selected_message_id"], "quote-one")
        self.assertEqual(
            self.client.get("/api/quote-evaluations").json["evaluations"][0]["id"],
            evaluation_id)

    def test_deleting_chat_history_also_removes_derived_quote_analysis(self):
        self.add_message("quote-deleted", "MYR 25, delivery in 2 days.")
        evaluation = self.store.create_quote_evaluation(["quote-deleted"], "balanced")
        self.store.update_quote_evaluation(
            evaluation["id"], "completed",
            result={"quotes": [{"message_id": "quote-deleted", "evidence": "MYR 25"}]})

        self.store.clear_history(self.conversation["id"], delete_attachments=False)
        self.assertIsNone(self.store.quote_evaluation(evaluation["id"]))

    def test_comparison_can_be_cancelled_and_late_results_are_ignored(self):
        self.add_message("quote-cancel", "MYR 125, delivery in 4 days.")
        evaluation = self.store.create_quote_evaluation(["quote-cancel"], "balanced")
        self.assertEqual(
            self.client.post(
                f"/api/quote-evaluations/{evaluation['id']}/cancel",
                json={}, headers={"X-CSRF-Token": self.csrf}).status_code,
            401,
        )
        self.login()
        self.assertEqual(
            self.client.post(
                f"/api/quote-evaluations/{evaluation['id']}/cancel",
                json={}).status_code,
            403,
        )

        stopped = self.post(
            f"/api/quote-evaluations/{evaluation['id']}/cancel", {})
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json["evaluation"]["status"], "cancelled")

        late_result = {"quotes": [{"message_id": "quote-cancel", "total_price": 125}]}
        unchanged = self.store.update_quote_evaluation(
            evaluation["id"], "completed", result=late_result)
        self.assertEqual(unchanged["status"], "cancelled")
        self.assertEqual(unchanged["result"], {})

        completed = self.store.create_quote_evaluation(["quote-cancel"], "price")
        self.store.update_quote_evaluation(completed["id"], "completed", result={})
        rejected = self.post(
            f"/api/quote-evaluations/{completed['id']}/cancel", {})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json["evaluation"]["status"], "completed")

    def test_cancel_during_runpod_request_discards_response(self):
        self.login()
        self.add_message("quote-running", "MYR 125, delivery in 4 days.")
        evaluation = self.store.create_quote_evaluation(["quote-running"], "balanced")
        worker = QuoteAnalysisWorker(
            self.store, "https://example.api.runpod.ai", "fake-test-only-key")
        self.app.extensions["quote_worker"] = worker
        request_started = threading.Event()
        finish_request = threading.Event()

        def delayed_response(*_):
            request_started.set()
            finish_request.wait(3)
            return {"quotes": [{
                "quote_key": "QUOTE_1", "vendor": "Late vendor", "currency": "MYR",
                "total_price": 125, "delivery_days": 4,
                "delivery_unit": "calendar_days",
            }]}

        try:
            with patch.object(worker, "_request_model", side_effect=delayed_response):
                worker.submit(evaluation["id"])
                self.assertTrue(request_started.wait(3))
                stopped = self.post(
                    f"/api/quote-evaluations/{evaluation['id']}/cancel", {})
                self.assertEqual(stopped.status_code, 200)
                self.assertEqual(stopped.json["evaluation"]["status"], "cancelled")
                finish_request.set()
                worker.jobs.join()
            current = self.store.quote_evaluation(evaluation["id"])
            self.assertEqual(current["status"], "cancelled")
            self.assertEqual(current["result"], {})
        finally:
            finish_request.set()

    def test_local_readers_and_multimodal_payloads_do_not_call_runpod(self):
        root = Path(self.directory.name)
        path = root / "quotation.txt"
        path.write_text("Unit price MYR 12, delivery in 4 calendar days.", encoding="utf-8")
        self.assertIn("MYR 12", _document_text(path, "text/plain"))

        docx = root / "quotation.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>MYR 123, delivery in 5 days</w:t></w:r></w:p></w:body>'
                '</w:document>',
            )
        self.assertIn("MYR 123", _document_text(docx, ""))

        xlsx = root / "quotation.xlsx"
        with zipfile.ZipFile(xlsx, "w") as archive:
            archive.writestr(
                "xl/workbook.xml",
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Quote" sheetId="1" r:id="rId1"/></sheets></workbook>',
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Vendor A</t></is></c>'
                '<c r="B1"><v>123.5</v></c></row></sheetData></worksheet>',
            )
        extracted_sheet = _document_text(xlsx, "")
        self.assertIn("Vendor A", extracted_sheet)
        self.assertIn("123.5", extracted_sheet)

        pdf = root / "quotation.pdf"
        pdf.write_bytes(b"mock PDF handled by the local pdftotext utility")
        with patch("src.quote_analysis.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=b"MYR 88, delivery in 2 calendar days")) as run:
            self.assertIn("MYR 88", _document_text(pdf, "application/pdf"))
            self.assertEqual(run.call_args.args[0][0], "pdftotext")

        media_root = root / "attachments"
        media_root.mkdir(exist_ok=True)
        image = media_root / "image.png"
        image.write_bytes(b"small test image")
        audio = media_root / "voice.ogg"
        audio.write_bytes(b"small test audio")
        worker = QuoteAnalysisWorker(
            self.store, "https://example.api.runpod.ai", "fake-test-only-key")
        image_row = {"id": "img", "client_name": "Test Client", "filename": "image.png",
                     "timestamp": 1, "attachment_path": str(image),
                     "mime_type": "image/png", "body": "", "caption": ""}
        _, image_parts, _ = worker._prepare_message(image_row, "QUOTE_1", 0)
        self.assertEqual(image_parts[1]["type"], "image_url")

        audio_row = {"id": "aud", "client_name": "Test Client", "filename": "voice.ogg",
                     "timestamp": 1, "attachment_path": str(audio),
                     "mime_type": "audio/ogg; codecs=opus", "body": "", "caption": ""}
        _, audio_parts, _ = worker._prepare_message(audio_row, "QUOTE_2", 0)
        self.assertEqual(audio_parts[1]["type"], "input_audio")
        self.assertEqual(audio_parts[1]["input_audio"]["format"], "ogg")


if __name__ == "__main__":
    unittest.main()
