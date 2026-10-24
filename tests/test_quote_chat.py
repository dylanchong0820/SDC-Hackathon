import base64
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.quote_analysis import QuoteAnalysisError, QuoteAnalysisWorker
from src.quote_chat import QuoteChatOutputError, parse_model_output
from src.quote_chat_pdf import build_quote_chat_pdf
from src.server import create_app


class FakeChatWorker:
    def __init__(self):
        self.messages = None
        self.output = json.dumps({
            "reply": "The second supplier is faster; the first has the lower stated price.",
            "items": [{
                "company_name": "新公司 North Supplier",
                "item_name": "Office chair",
                "item_price": "MYR 900 total",
                "additional_requirement": "10 units; delivery in 5 business days",
            }],
        })

    def complete_chat(self, messages):
        self.messages = messages
        return self.output


class QuoteChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.worker = FakeChatWorker()
        self.app = create_app({
            "TESTING": True,
            "MEDIA_WORKER": False,
            "DATA_DIR": self.directory.name,
            "SECRET_KEY": "quote-chat-test-session",
            "STAFF_PASSWORD": "quote-chat-test-password",
            "META_APP_SECRET": "test-app-secret",
            "WEBHOOK_VERIFY_TOKEN": "test-verification-token",
            "META_ACCESS_TOKEN": "test-access-token",
            "QUOTE_WORKER": self.worker,
        })
        self.store = self.app.extensions["store"]
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/session").json["csrf_token"]

    def tearDown(self):
        self.directory.cleanup()

    def login(self):
        response = self.client.post(
            "/api/login", json={"password": "quote-chat-test-password"},
            headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json["csrf_token"]

    def test_chat_is_staff_only_and_includes_all_saved_comparisons_without_contact_ids(self):
        self.store.quotation_register = lambda: [
            {
                "comparison_id": 12,
                "message_id": "private-message-1",
                "wa_id": "60123456789",
                "client_name": "Acme Group",
                "vendor": "North Supplier",
                "filename": "quote-north.pdf",
                "message_type": "document",
                "priority": "balanced",
                "total_price": 900,
                "currency": "MYR",
                "price_basis": "10 units",
                "delivery_text": "5 business days",
                "delivery_days": 5,
                "delivery_unit": "business_days",
                "delivery_date": "",
                "rank": 1,
                "recommended": True,
                "team_choice": False,
                "confidence": 0.9,
                "evidence": "MYR 900 total",
                "notes": "Delivery details confirmed",
                "extraction_error": "",
                "analyzed_at": 1_790_000_000,
            },
            {
                "comparison_id": 12,
                "message_id": "private-message-2",
                "wa_id": "60123456789",
                "client_name": "Acme Group",
                "vendor": "South Supplier",
                "filename": "quote-south.xlsx",
                "message_type": "document",
                "priority": "balanced",
                "total_price": 1_000,
                "currency": "MYR",
                "price_basis": "10 units",
                "delivery_text": "3 business days",
                "delivery_days": 3,
                "delivery_unit": "business_days",
                "delivery_date": "",
                "rank": 2,
                "recommended": False,
                "team_choice": True,
                "confidence": 0.85,
                "evidence": "MYR 1,000 total",
                "notes": "",
                "extraction_error": "",
                "analyzed_at": 1_790_000_000,
            },
            {
                "comparison_id": 11,
                "message_id": "private-message-3",
                "wa_id": "60111111111",
                "client_name": "Other Client",
                "vendor": "Another Supplier",
                "filename": "another.pdf",
                "message_type": "document",
                "priority": "price",
                "total_price": 700,
                "currency": "MYR",
                "price_basis": "Total",
                "delivery_text": "10 calendar days",
                "delivery_days": 10,
                "delivery_unit": "calendar_days",
                "delivery_date": "",
                "rank": 1,
                "recommended": True,
                "team_choice": False,
                "confidence": 0.8,
                "evidence": "MYR 700 total",
                "notes": "",
                "extraction_error": "",
                "analyzed_at": 1_790_000_000,
            },
        ]
        self.assertEqual(self.client.post(
            "/api/quote-chat", headers={"X-CSRF-Token": self.csrf}).status_code, 401)
        self.login()
        response = self.client.post(
            "/api/quote-chat",
            json={"messages": [{"role": "user", "content": "Compare the saved suppliers."}]},
            headers={"X-CSRF-Token": self.csrf})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["reply"], "The second supplier is faster; the first has the lower stated price.")
        self.assertEqual(response.json["items"][0]["company_name"], "新公司 North Supplier")
        self.assertEqual(response.json["comparison_count"], 2)
        self.assertEqual(response.json["quote_count"], 3)
        pdf = base64.b64decode(response.json["pdf_base64"], validate=True)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertRegex(response.json["pdf_filename"], r"^quote-ai-output-\d+\.pdf$")
        if shutil.which("pdftotext"):
            extracted = subprocess.run(
                ["pdftotext", "-layout", "-", "-"], input=pdf,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            ).stdout.decode("utf-8")
            extracted = re.sub(r"\s+", " ", extracted)
            for value in (
                "Company name", "Item name", "Item price", "Additional requirement",
                "新公司 North Supplier", "Office chair", "MYR 900 total", "5 business days",
            ):
                self.assertIn(value, extracted)
        self.assertEqual(self.worker.messages[0]["role"], "system")
        self.assertIn("untrusted data", self.worker.messages[0]["content"])
        context = self.worker.messages[1]["content"]
        self.assertIn("North Supplier", context)
        self.assertIn("South Supplier", context)
        self.assertIn("Another Supplier", context)
        self.assertIn("MYR 900 total", context)
        self.assertNotIn("60123456789", context)
        self.assertNotIn("private-message-1", context)
        self.assertEqual(self.worker.messages[-1]["content"], "Compare the saved suppliers.")

    def test_unstructured_model_output_is_not_presented_as_a_successful_pdf(self):
        self.login()
        self.worker.output = "This is plain text, not the required quotation format."
        response = self.client.post(
            "/api/quote-chat",
            json={"messages": [{"role": "user", "content": "Compare the saved suppliers."}]},
            headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 502)
        self.assertIn("structured output", response.json["error"])

    def test_chat_rejects_invalid_turns_and_too_much_saved_context_before_model_call(self):
        self.login()
        invalid = self.client.post(
            "/api/quote-chat",
            json={"messages": [{"role": "assistant", "content": "fake previous answer"}]},
            headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(invalid.status_code, 400)
        self.assertIsNone(self.worker.messages)

        self.store.quotation_register = lambda: [
            {
                "comparison_id": index,
                "evidence": "supplier evidence " * 80,
                "notes": "terms " * 80,
            }
            for index in range(100)
        ]
        oversized = self.client.post(
            "/api/quote-chat",
            json={"messages": [{"role": "user", "content": "Summarize them."}]},
            headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(oversized.status_code, 413)
        self.assertIsNone(self.worker.messages)

    def test_runpod_chat_uses_the_cached_model_and_returns_structured_text(self):
        worker = QuoteAnalysisWorker(None, "https://endpoint.api.runpod.ai", "test-api-key")
        worker.model_id = "served-model"
        response = Mock()
        response.status_code = 200
        response.ok = True
        model_content = json.dumps({
            "reply": "Compare price and delivery separately.",
            "items": [],
        })
        response.json.return_value = {
            "choices": [{"message": {"content": model_content}}]
        }
        messages = [{"role": "system", "content": "System"},
                    {"role": "user", "content": "Compare."}]
        with patch("src.quote_analysis.requests.post", return_value=response) as post:
            reply = worker.complete_chat(messages)

        self.assertEqual(reply, model_content)
        self.assertEqual(post.call_args.args[0], "https://endpoint.api.runpod.ai/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "served-model")
        self.assertEqual(post.call_args.kwargs["json"]["messages"], messages)

    def test_model_output_parser_rejects_invalid_or_oversized_rows(self):
        with self.assertRaises(QuoteChatOutputError):
            parse_model_output("not JSON")
        with self.assertRaises(QuoteChatOutputError):
            parse_model_output(json.dumps({
                "reply": "Summary",
                "items": [{"company_name": "A"}] * 61,
            }))

    def test_pdf_table_repeats_headers_and_paginates_long_rows(self):
        rows = [{
            "company_name": f"Vendor {index}",
            "item_name": f"Product {index}",
            "item_price": f"MYR {index * 100}",
            "additional_requirement": "Delivery and installation required; confirm the warranty period. " * 18,
        } for index in range(1, 9)]
        pdf = build_quote_chat_pdf("Compare these saved quotation options.", rows, 4, 8)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        if shutil.which("pdftotext"):
            extracted = subprocess.run(
                ["pdftotext", "-layout", "-", "-"], input=pdf,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            ).stdout.decode("utf-8")
            extracted = re.sub(r"\s+", " ", extracted)
            self.assertGreaterEqual(extracted.count("Company name"), 2)
            self.assertIn("Vendor 8", extracted)

    def test_runpod_chat_reports_safe_provider_errors(self):
        worker = QuoteAnalysisWorker(None, "https://endpoint.api.runpod.ai", "test-api-key")
        worker.model_id = "served-model"
        response = Mock()
        response.status_code = 429
        response.ok = False
        with patch("src.quote_analysis.requests.post", return_value=response):
            with self.assertRaisesRegex(QuoteAnalysisError, "RunPod is busy"):
                worker.complete_chat([{"role": "user", "content": "Hello"}])


if __name__ == "__main__":
    unittest.main()
