import io
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from src.server import create_app


class QueuedWorker:
    def submit(self, evaluation_id):
        self.evaluation_id = evaluation_id


class QuotationRegisterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.worker = QueuedWorker()
        self.app = create_app({
            "TESTING": True,
            "MEDIA_WORKER": False,
            "DATA_DIR": self.directory.name,
            "SECRET_KEY": "quotation-register-test-session",
            "STAFF_PASSWORD": "quotation-register-password",
            "META_APP_SECRET": "test-app-secret",
            "WEBHOOK_VERIFY_TOKEN": "test-verification-token",
            "META_ACCESS_TOKEN": "test-access-token",
            "QUOTE_WORKER": self.worker,
        })
        self.store = self.app.extensions["store"]
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/session").json["csrf_token"]
        self.conversation = self.store.get_or_create_conversation(
            "123456789", "60123456789", "Client <One>")

    def tearDown(self):
        self.directory.cleanup()

    def login(self):
        response = self.client.post(
            "/api/login", json={"password": "quotation-register-password"},
            headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json["csrf_token"]

    def test_shell_loads_the_separate_workspace_modules(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"workspace-home.js", response.data)
        self.assertIn(b"quotation-register.js", response.data)

    def add_completed_quote(self):
        message_id = "register-message-1"
        message = {
            "id": message_id,
            "type": "document",
            "body": "",
            "caption": "Quote for office chairs",
            "timestamp": int(time.time()),
            "filename": "Offer & terms.xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "media_id": "media-register",
            "raw": {},
            "context_id": None,
        }
        self.assertTrue(self.store.insert_inbound(self.conversation["id"], message))
        file_path = Path(self.directory.name) / "attachments" / "fixture.xlsx"
        file_path.write_bytes(b"test workbook contents")
        self.store.media_saved(
            self.store.message(message_id), file_path, message["mime_type"], message["filename"])
        evaluation = self.store.create_quote_evaluation([message_id], "balanced")
        result = {
            "quotes": [{
                "message_id": message_id,
                "client_name": "Client <One>",
                "filename": "Offer & terms.xlsx",
                "vendor": "North & South",
                "currency": "MYR",
                "total_price": 1250.5,
                "price_basis": "Total for 10 chairs",
                "delivery_days": 8,
                "delivery_unit": "calendar_days",
                "delivery_date": None,
                "delivery_text": "Within 8 calendar days",
                "rank": 1,
                "recommended": True,
                "confidence": 0.91,
                "evidence": "=1+1 & MYR 1,250.50 <listed>",
                "notes": ["Delivery excludes weekends"],
                "extraction_error": "",
            }],
            "recommendation_message_id": message_id,
            "summary": "Balanced result",
            "limitations": [],
        }
        self.store.update_quote_evaluation(evaluation["id"], "completed", result=result)
        self.store.select_quote(evaluation["id"], message_id)
        return evaluation["id"], message_id

    def test_register_and_excel_export_are_staff_only(self):
        self.assertEqual(self.client.get("/api/quotation-register").status_code, 401)
        self.assertEqual(self.client.get("/api/quotation-register.xlsx").status_code, 401)
        self.login()
        evaluation_id, message_id = self.add_completed_quote()

        response = self.client.get("/api/quotation-register")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.json["total"], 1)
        row = response.json["rows"][0]
        self.assertEqual(row["comparison_id"], evaluation_id)
        self.assertEqual(row["message_id"], message_id)
        self.assertEqual(row["client_name"], "Client <One>")
        self.assertEqual(row["vendor"], "North & South")
        self.assertEqual(row["total_price"], 1250.5)
        self.assertEqual(row["currency"], "MYR")
        self.assertTrue(row["recommended"])
        self.assertTrue(row["team_choice"])

        export = self.client.get("/api/quotation-register.xlsx")
        self.assertEqual(export.status_code, 200)
        self.assertIn("quotation-register.xlsx", export.headers["Content-Disposition"])
        self.assertTrue(export.headers["Content-Type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        with zipfile.ZipFile(io.BytesIO(export.data)) as workbook:
            self.assertIsNone(workbook.testzip())
            sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            cells = sheet.findall(".//x:sheetData/x:row/x:c", namespace)
            inline_text = [
                "".join(cell.itertext()) for cell in cells if cell.attrib.get("t") == "inlineStr"
            ]
            self.assertIn("Client <One>", inline_text)
            self.assertIn("North & South", inline_text)
            self.assertIn("=1+1 & MYR 1,250.50 <listed>", inline_text)
            price_cell = next(cell for cell in cells if cell.attrib.get("r") == "J2")
            self.assertEqual(price_cell.attrib["t"], "n")
            self.assertEqual(price_cell.find("x:v", namespace).text, "1250.5")
            self.assertTrue(sheet.find("x:autoFilter", namespace) is not None)

    def test_register_includes_each_saved_comparison_and_history_deletion_removes_rows(self):
        self.login()
        evaluation_id, message_id = self.add_completed_quote()
        duplicate = self.store.create_quote_evaluation([message_id], "price")
        self.store.update_quote_evaluation(duplicate["id"], "completed", result={
            "quotes": [{"message_id": message_id, "currency": "MYR", "total_price": 1250.5,
                        "rank": 1, "recommended": True}]
        })
        self.assertEqual(self.store.quotation_register()[0]["comparison_id"], duplicate["id"])
        self.assertEqual(len(self.store.quotation_register()), 2)

        self.store.clear_history(self.conversation["id"], delete_attachments=False)
        self.assertEqual(self.store.quotation_register(), [])
        self.assertIsNone(self.store.quote_evaluation(evaluation_id))


if __name__ == "__main__":
    unittest.main()
