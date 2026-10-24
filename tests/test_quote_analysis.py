"""Quote comparison uses isolated messages and never calls a live provider."""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
import requests
from unittest.mock import Mock, patch
from pathlib import Path
from types import SimpleNamespace

from src.quote_analysis import (
    MAX_DOCUMENT_BYTES,
    MAX_OCR_PAGE_SECONDS,
    MAX_OCR_PAGES,
    MAX_XLS_CONVERT_SECONDS,
    QuoteAnalysisError,
    QuoteAnalysisWorker,
    _document_text,
    _ocr_pdf,
    _pdf_pages_with_large_images,
    rank_quotes,
)
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

    def _make_hybrid_pdf(self, pdf, pages):
        from playwright.sync_api import sync_playwright

        chromium = (
            os.getenv("CHROMIUM_PATH")
            or shutil.which("chromium")
            or ("/repl/tools/bin/chromium" if Path("/repl/tools/bin/chromium").exists() else None)
        )
        self.assertIsNotNone(chromium, "Chromium is needed to build the hybrid-PDF fixture.")
        fixture = Path(__file__).parent / "fixtures" / "scanned-quotation.html"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=chromium,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 1000})
                page.goto(fixture.resolve().as_uri(), wait_until="load")
                scan_png = base64.b64encode(page.screenshot(type="png")).decode("ascii")
                body = []
                for index, content in enumerate(pages):
                    if content is None:
                        content = f'<img alt="" src="data:image/png;base64,{scan_png}">'
                    body.append(
                        f'<section class="page{" next" if index else ""}">{content}</section>'
                    )
                page.set_content(
                    "<!doctype html><style>@page{size:A4;margin:0}"
                    "html,body{margin:0}.page{box-sizing:border-box;width:210mm;height:297mm;"
                    "padding:24mm;font:22px Arial;overflow:hidden}.next{break-before:page}"
                    "img{display:block;width:700px;height:auto}</style>" + "".join(body),
                    wait_until="load",
                )
                page.pdf(path=str(pdf), print_background=True)
            finally:
                browser.close()

    def _make_hybrid_image_table_pdf(self, pdf, decorative=False, page_count=1, image_page=1,
                                     page_layouts=None):
        from playwright.sync_api import sync_playwright

        chromium = (
            os.getenv("CHROMIUM_PATH")
            or shutil.which("chromium")
            or ("/repl/tools/bin/chromium" if Path("/repl/tools/bin/chromium").exists() else None)
        )
        self.assertIsNotNone(chromium, "Chromium is needed to build the image-table fixture.")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=chromium,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 900})
                if decorative:
                    page.set_content(
                        '<div id="art" style="width:170px;height:55px;background:#164e63;'
                        'color:white;font:bold 30px Arial;text-align:center;line-height:55px">'
                        "NORTHWIND</div>"
                    )
                    image_selector = "#art"
                else:
                    page.set_content(
                        '<div id="art" style="box-sizing:border-box;width:700px;height:270px;'
                        'padding:24px;background:#fff;border:4px solid #111;font:40px Arial">'
                        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:28px">'
                        "<b>Item</b><b>Price</b><span>Control Panel</span><strong>MYR 128</strong>"
                        "<span>Installation</span><strong>MYR 35</strong></div></div>"
                    )
                    image_selector = "#art"
                image_bytes = page.locator(image_selector).screenshot(type="jpeg", quality=95)
                if page_layouts is not None:
                    self._write_rotated_image_pdf(pdf, image_bytes, decorative, page_layouts)
                    return
                image = base64.b64encode(image_bytes).decode("ascii")
                pages = []
                for index in range(1, page_count + 1):
                    image_markup = (
                        f'<img class="quote-image" src="data:image/jpeg;base64,{image}">'
                        if index == image_page else ""
                    )
                    pages.append(
                        '<section class="page"><div class="details">'
                        f"<div>Supplier: Northwind Tools quotation Q-300{index}.</div>"
                        "<div>Delivery is included within six calendar days, with service support.</div>"
                        f"<div>Commercial details for section {index} include warranty, installation "
                        "coordination, payment milestones, and product support requirements.</div>"
                        f"</div>{image_markup}</section>"
                    )
                page.set_content(
                    "<!doctype html><style>@page{size:A4;margin:0}"
                    "html,body{margin:0}body{font:24px Arial;line-height:1.5}"
                    ".page{box-sizing:border-box;"
                    "width:210mm;height:297mm;padding:35px;overflow:hidden}"
                    ".page+.page{break-before:page}.details{margin-bottom:28px}"
                    ".quote-image{display:block}</style>" + "".join(pages),
                    wait_until="load",
                )
                page.pdf(path=str(pdf), print_background=True)
            finally:
                browser.close()

    @staticmethod
    def _write_rotated_image_pdf(pdf, image, decorative, layouts):
        """Build real /Rotate and per-page MediaBox fixtures without a PDF dependency.

        Layouts are (unrotated width, unrotated height, rotation, image width,
        image height). Image sizes use displayed-page points. Counter-rotate
        content so the rendered quotation is upright, as in exported scans.
        A zero image width omits the image on that page.
        """
        objects = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]

        def stream(data, attributes=""):
            return (f"<< /Length {len(data)} {attributes} >>\nstream\n".encode()
                    + data + b"\nendstream")

        pixels = (170, 55) if decorative else (700, 270)
        objects.append(stream(image, "/Type /XObject /Subtype /Image "
                              f"/Width {pixels[0]} /Height {pixels[1]} "
                              "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode"))
        pages = []
        for index, (width, height, rotation, image_width, image_height) in enumerate(layouts, 1):
            display_height = width if rotation in (90, 270) else height
            transform = {
                0: "1 0 0 1 0 0",
                90: f"0 1 -1 0 {width} 0",
                180: f"-1 0 0 -1 {width} {height}",
                270: f"0 -1 1 0 0 {height}",
            }[rotation]
            content = (
                f"q {transform} cm BT /F1 12 Tf 16 TL 30 {display_height - 30} Td "
                f"(Supplier: Northwind Tools quotation Q-300{index}.) Tj T* "
                "(Delivery is included within six calendar days, with service support.) Tj T* "
                f"(Section {index}: warranty, payment milestones and installation coordination.) Tj "
                "ET\n"
            )
            if image_width:
                content += (
                    f"q {image_width} 0 0 {image_height} 30 "
                    f"{display_height - 110 - image_height} cm /Im1 Do Q\n"
                )
            content += "Q"
            objects.append(stream(content.encode("ascii")))
            content_id = len(objects)
            objects.append(
                (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                 f"/Rotate {rotation} /Resources << /Font << /F1 3 0 R >> "
                 f"/XObject << /Im1 4 0 R >> >> /Contents {content_id} 0 R >>").encode()
            )
            pages.append(f"{len(objects)} 0 R")
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(pages)}] >>".encode()
        data = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(data)
        data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
        for offset in offsets:
            data.extend(f"{offset:010} 00000 n \n".encode())
        data.extend((f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                     f"startxref\n{xref}\n%%EOF\n").encode())
        pdf.write_bytes(data)

    def test_rotated_mixed_size_image_tables_are_detected_and_ocrd(self):
        pdf = Path(self.directory.name) / "rotated-mixed-pricing.pdf"
        self._make_hybrid_image_table_pdf(pdf, page_layouts=[
            (595, 842, 0, 525, 202.5),
            (595, 842, 90, 525, 202.5),
            (612, 792, 180, 525, 202.5),
            (420, 595, 270, 525, 202.5),
            (842, 595, 0, 525, 202.5),
        ])
        self.assertEqual(_pdf_pages_with_large_images(pdf, 5), ([1, 2, 3, 4, 5], False))
        metadata = {}
        text = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertEqual(metadata["pdf_page_count"], 5)
        self.assertEqual(metadata["ocr_page_limit"], 5)
        self.assertFalse(metadata["ocr_truncated"])
        self.assertEqual(metadata["source_warnings"], [])
        for number in range(1, 6):
            with self.subTest(page=number):
                self.assertIn(f"[OCR page {number}]", text)
                page_ocr = text.split(f"[OCR page {number}]", 1)[1].split("[OCR page", 1)[0]
                self.assertIn("MYR 128", page_ocr)
                self.assertIn("MYR 35", page_ocr)

    def test_rotated_image_table_uses_displayed_page_axes(self):
        pdf = Path(self.directory.name) / "rotated-small-table.pdf"
        self._make_hybrid_image_table_pdf(pdf, page_layouts=[
            (595, 842, 90, 280, 108),
            (595, 842, 270, 280, 108),
        ])
        # Both tables occupy 33% x 18% of their displayed landscape pages.
        self.assertEqual(_pdf_pages_with_large_images(pdf, 2), ([1, 2], False))
        metadata = {}
        text = _document_text(pdf, "application/pdf", metadata=metadata)
        for number in (1, 2):
            page_ocr = text.split(f"[OCR page {number}]", 1)[1].split("[OCR page", 1)[0]
            self.assertIn("MYR 128", page_ocr)
        self.assertEqual(metadata["source_warnings"], [])

    def test_rotated_mixed_size_decorative_images_do_not_request_ocr(self):
        pdf = Path(self.directory.name) / "rotated-mixed-logos.pdf"
        self._make_hybrid_image_table_pdf(pdf, decorative=True, page_layouts=[
            (595, 842, 0, 127.5, 41.25),
            (842, 595, 90, 525, 110),
            (612, 792, 180, 127.5, 41.25),
            (842, 595, 270, 525, 110),
            (842, 595, 0, 127.5, 41.25),
        ])
        # The stretched, thin banners on pages 2 and 4 are under 16% of
        # displayed page height, but would exceed it on the unrotated axes.
        self.assertEqual(_pdf_pages_with_large_images(pdf, 5), ([], False))
        metadata = {}
        with patch("src.quote_analysis._ocr_pdf") as ocr:
            text = _document_text(pdf, "application/pdf", metadata=metadata)
        ocr.assert_not_called()
        self.assertIn("Supplier: Northwind Tools", text)
        self.assertEqual(metadata["source_warnings"], [])
        self.assertFalse(metadata["ocr_truncated"])

    def test_rotated_image_table_at_page_limit_and_beyond_discloses_omission(self):
        pdf = Path(self.directory.name) / "rotated-pricing-page-limit.pdf"
        self._make_hybrid_image_table_pdf(pdf, page_layouts=[
            (595, 842, 0, 0, 0),
            (612, 792, 90, 0, 0),
            (420, 595, 180, 0, 0),
            (842, 595, 270, 0, 0),
            (595, 842, 90, 525, 202.5),
            (420, 595, 270, 525, 202.5),
        ])
        metadata = {}
        with patch("src.quote_analysis._ocr_pdf", wraps=_ocr_pdf) as ocr:
            text = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertEqual(ocr.call_args.kwargs["page_numbers"], [5])
        self.assertEqual(metadata["pdf_page_count"], 6)
        self.assertTrue(metadata["ocr_truncated"])
        self.assertIn("[OCR page 5]", text)
        self.assertIn("MYR 128", text)
        self.assertNotIn("[OCR page 6]", text)
        self.assertTrue(any("sizeable image region on page 6" in warning
                            for warning in metadata["source_warnings"]))

    def test_rotated_image_table_ocr_failures_keep_coverage_warning(self):
        pdf = Path(self.directory.name) / "rotated-pricing-failure.pdf"
        self._make_hybrid_image_table_pdf(pdf, page_layouts=[(595, 842, 90, 525, 202.5)])
        original = pdf.read_bytes()
        real_run = subprocess.run
        for failure in ("exit", "timeout", "empty"):
            with self.subTest(failure=failure):
                def fail_ocr(args, **kwargs):
                    if args[0] != "tesseract":
                        return real_run(args, **kwargs)
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(args, 1)
                    return SimpleNamespace(returncode=1 if failure == "exit" else 0,
                                           stdout=b"", stderr=b"isolated failure")

                metadata = {}
                with patch("src.quote_analysis.subprocess.run", side_effect=fail_ocr):
                    text = _document_text(pdf, "application/pdf", metadata=metadata)
                self.assertIn("Supplier: Northwind Tools", text)
                self.assertIn("Source coverage warning", text)
                self.assertTrue(metadata["ocr_truncated"])
                self.assertTrue(any("including the detected image region, on page 1" in warning
                                    for warning in metadata["source_warnings"]))
                self.assertEqual(pdf.read_bytes(), original)

    def test_rotated_page_geometry_failure_discloses_unverified_images(self):
        pdf = Path(self.directory.name) / "rotated-unknown-geometry.pdf"
        self._make_hybrid_image_table_pdf(pdf, page_layouts=[(595, 842, 90, 280, 108)])
        real_run = subprocess.run
        for failure in ("exit", "missing_rotation", "invalid_rotation", "missing_size"):
            with self.subTest(failure=failure):
                def fail_geometry(args, **kwargs):
                    result = real_run(args, **kwargs)
                    if args[0] == "pdfinfo" and "-box" in args:
                        if failure == "exit":
                            result.returncode = 1
                        elif failure == "missing_rotation":
                            result.stdout = b"\n".join(
                                line for line in result.stdout.splitlines() if b"rot:" not in line)
                        elif failure == "invalid_rotation":
                            result.stdout = result.stdout.replace(b"rot:   90", b"rot:   45")
                        else:
                            result.stdout = b"\n".join(
                                line for line in result.stdout.splitlines() if b"size:" not in line)
                    return result

                metadata = {}
                with patch("src.quote_analysis.subprocess.run", side_effect=fail_geometry):
                    text = _document_text(pdf, "application/pdf", metadata=metadata)
                self.assertIn("Supplier: Northwind Tools", text)
                self.assertIn("Source coverage warning", text)
                self.assertTrue(metadata["ocr_truncated"])
                self.assertTrue(any("Image regions could not be checked" in warning
                                    for warning in metadata["source_warnings"]))

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

    def test_runpod_request_has_bounded_timeouts_and_busy_error(self):
        worker = QuoteAnalysisWorker(
            None, "https://example.api.runpod.ai", "test-key", request_timeout=37)
        worker.model_id = "test-model"
        with patch("src.quote_analysis.requests.post", side_effect=requests.ReadTimeout) as post:
            with self.assertRaisesRegex(QuoteAnalysisError, "response timeout"):
                worker._request_model([])
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 37))

        with patch("src.quote_analysis.requests.post", return_value=SimpleNamespace(
                status_code=429, ok=False)):
            with self.assertRaisesRegex(QuoteAnalysisError, "RunPod is busy"):
                worker._request_model([])
        for status, message in (
                (401, "rejected the API key"),
                (415, "rejected one or more selected formats"),
                (504, "gateway timed out"),
                (500, "HTTP 500")):
            with self.subTest(status=status), patch(
                    "src.quote_analysis.requests.post",
                    return_value=SimpleNamespace(status_code=status, ok=False)):
                with self.assertRaisesRegex(QuoteAnalysisError, message):
                    worker._request_model([])

    def test_comparison_persists_partial_results_while_other_sources_run(self):
        self.login()
        self.add_message("partial-one", "MYR 120, delivery in 3 days.")
        self.add_message("partial-two", "MYR 140, delivery in 5 days.")
        evaluation = self.store.create_quote_evaluation(
            ["partial-one", "partial-two"], "balanced")
        worker = QuoteAnalysisWorker(
            self.store, "https://example.api.runpod.ai", "fake-test-key")
        worker.model_id = "test-model"
        slow_request_started = threading.Event()
        release_slow_request = threading.Event()
        failures = []

        def fake_model_request(user_content, _evaluation_id=None):
            prompt = "\n".join(
                part.get("text", "") for part in user_content if part.get("type") == "text")
            quote_key = "QUOTE_2" if "Quotation QUOTE_2" in prompt else "QUOTE_1"
            if quote_key == "QUOTE_2":
                slow_request_started.set()
                release_slow_request.wait(5)
            return {"quotes": [{
                "quote_key": quote_key,
                "vendor": quote_key,
                "currency": "MYR",
                "total_price": 120 if quote_key == "QUOTE_1" else 140,
                "delivery_days": 3 if quote_key == "QUOTE_1" else 5,
                "delivery_unit": "calendar_days",
            }]}

        def run_analysis():
            try:
                worker._analyze(evaluation["id"])
            except Exception as error:
                failures.append(error)

        try:
            with patch.object(worker, "_request_model", side_effect=fake_model_request):
                thread = threading.Thread(target=run_analysis)
                thread.start()
                self.assertTrue(slow_request_started.wait(3))
                deadline = time.monotonic() + 3
                partial = self.store.quote_evaluation(evaluation["id"])
                while len(partial["result"].get("quotes", [])) < 1 and time.monotonic() < deadline:
                    time.sleep(0.02)
                    partial = self.store.quote_evaluation(evaluation["id"])
                self.assertEqual(partial["status"], "processing")
                self.assertTrue(partial["result"]["partial"])
                self.assertEqual(partial["result"]["progress"]["completed"], 1)
                self.assertEqual(len(partial["result"]["quotes"]), 1)
                polled = self.client.get(f"/api/quote-evaluations/{evaluation['id']}")
                self.assertEqual(polled.status_code, 200)
                self.assertTrue(polled.json["evaluation"]["result"]["partial"])
                release_slow_request.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            completed = self.store.quote_evaluation(evaluation["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(len(completed["result"]["quotes"]), 2)
        finally:
            release_slow_request.set()

    def test_one_failed_runpod_source_does_not_discard_other_results(self):
        self.add_message("usable-one", "MYR 120, delivery in 3 days.")
        self.add_message("timeout-two", "MYR 140, delivery in 5 days.")
        evaluation = self.store.create_quote_evaluation(
            ["usable-one", "timeout-two"], "balanced")
        worker = QuoteAnalysisWorker(
            self.store, "https://example.api.runpod.ai", "fake-test-key")
        worker.model_id = "test-model"

        def fake_model_request(user_content, _evaluation_id=None):
            prompt = "\n".join(
                part.get("text", "") for part in user_content if part.get("type") == "text")
            if "Quotation QUOTE_2" in prompt:
                raise QuoteAnalysisError("RunPod response timeout for this quotation.")
            return {"quotes": [{
                "quote_key": "QUOTE_1", "currency": "MYR", "total_price": 120,
                "delivery_days": 3, "delivery_unit": "calendar_days",
            }]}

        with patch.object(worker, "_request_model", side_effect=fake_model_request):
            worker._analyze(evaluation["id"])
        saved = self.store.quote_evaluation(evaluation["id"])
        self.assertEqual(saved["status"], "completed")
        by_id = {quote["message_id"]: quote for quote in saved["result"]["quotes"]}
        self.assertEqual(by_id["usable-one"]["myr_total_price"], 120)
        self.assertIn("timeout", by_id["timeout-two"]["extraction_error"])
        self.assertTrue(any("1 of 2" in note for note in saved["result"]["limitations"]))

    def mixed_retry_fixture(self):
        self.login()
        self.add_message("retry-good", "MYR 120, delivery in 3 days.")
        self.add_message("retry-timeout", "MYR 80, delivery in 2 days.")
        review = self.post("/api/quote-previews", {
            "message_ids": ["retry-good", "retry-timeout"]}).json
        original = self.post("/api/quote-evaluations", {
            "message_ids": ["retry-good", "retry-timeout"], "priority": "price",
            "review_token": review["review_token"]}).json["evaluation"]
        worker = QuoteAnalysisWorker(
            self.store, "https://example.api.runpod.ai", "fake-test-key")
        worker.model_id = "test-model"

        def mixed_response(parts, *_):
            if any("Quotation QUOTE_2" in part.get("text", "") for part in parts):
                raise QuoteAnalysisError("RunPod comparison request timed out.")
            return {"quotes": [{
                "quote_key": "QUOTE_1", "vendor": "Saved supplier", "currency": "MYR",
                "total_price": 120, "delivery_days": 3, "delivery_unit": "calendar_days",
                "evidence": "MYR 120",
            }]}

        with patch.object(worker, "_request_model", side_effect=mixed_response):
            worker._analyze(original["id"])
        original = self.store.quote_evaluation(original["id"])
        self.assertEqual(original["status"], "completed")
        return original, review, worker

    def start_retry(self, original, review):
        return self.post(f"/api/quote-evaluations/{original['id']}/retry", {
            "message_ids": original["source_message_ids"],
            "review_token": review["review_token"],
        })

    def test_retry_only_failed_source_retains_facts_and_shows_changed_ranking(self):
        original, review, worker = self.mixed_retry_fixture()
        self.store.select_quote(original["id"], "retry-good")
        started = self.start_retry(original, review)
        self.assertEqual(started.status_code, 202)
        retry = started.json["evaluation"]
        self.assertNotEqual(retry["id"], original["id"])
        self.assertIsNone(retry["selected_message_id"])
        self.assertEqual(retry["priority"], "price")
        with patch.object(worker, "_request_model", return_value={"quotes": [{
            "quote_key": "QUOTE_2", "vendor": "Recovered supplier", "currency": "MYR",
            "total_price": 80, "delivery_days": 2, "delivery_unit": "calendar_days",
        }]}) as request:
            worker._analyze(retry["id"])
        request.assert_called_once()
        prompt = str(request.call_args)
        self.assertIn("Quotation QUOTE_2", prompt)
        self.assertNotIn("MYR 120", prompt)
        saved = self.store.quote_evaluation(retry["id"])
        self.assertEqual(saved["status"], "completed")
        result = saved["result"]
        retained = next(quote for quote in result["quotes"] if quote["message_id"] == "retry-good")
        before = next(quote for quote in original["result"]["quotes"] if quote["message_id"] == "retry-good")
        for field in ("vendor", "currency", "total_price", "delivery_days", "evidence"):
            self.assertEqual(retained[field], before[field])
        self.assertTrue(result["retry"]["ranking_changed"])
        self.assertTrue(result["retry"]["recommendation_changed"])
        self.assertEqual(result["recommendation_message_id"], "retry-timeout")
        self.assertEqual(self.store.quote_evaluation(original["id"])["result"], original["result"])
        self.assertEqual(self.store.quote_evaluation(original["id"])["selected_message_id"], "retry-good")
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE direction='outbound'").fetchone()[0], 0)
        # Once every source succeeded, there is nothing left to retry.
        self.assertEqual(self.start_retry(saved, review).status_code, 400)

    def test_retry_preserves_session_review_and_exact_original_source_set(self):
        original, review, _ = self.mixed_retry_fixture()
        url = f"/api/quote-evaluations/{original['id']}/retry"
        self.assertEqual(self.post(url, {}).status_code, 400)
        self.assertEqual(self.client.post(url, json={}).status_code, 403)
        anonymous = self.app.test_client()
        csrf = anonymous.get("/api/session").json["csrf_token"]
        self.assertEqual(anonymous.post(url, json={}, headers={"X-CSRF-Token": csrf}).status_code, 401)
        self.assertEqual(self.post(url, {
            "message_ids": ["retry-timeout"], "review_token": review["review_token"],
        }).status_code, 400)
        with self.client.session_transaction() as session:
            session["csrf"] = "another-staff-session"
        self.csrf = self.client.get("/api/session").json["csrf_token"]
        self.assertEqual(self.start_retry(original, review).status_code, 400)

    def test_retry_keeps_ranking_unchanged_when_recovered_source_is_not_comparable(self):
        original, review, worker = self.mixed_retry_fixture()
        retry = self.start_retry(original, review).json["evaluation"]
        with patch.object(worker, "_request_model", return_value={"quotes": [{
            "quote_key": "QUOTE_2", "vendor": "No stated price", "currency": "MYR",
        }]}):
            worker._analyze(retry["id"])
        saved = self.store.quote_evaluation(retry["id"])
        self.assertEqual(saved["status"], "completed")
        self.assertFalse(saved["result"]["retry"]["ranking_changed"])
        self.assertFalse(saved["result"]["retry"]["recommendation_changed"])
        self.assertEqual(saved["result"]["recommendation_message_id"], "retry-good")

    def test_currency_refresh_preserves_retry_and_second_retry_sends_only_failed_source(self):
        self.login()
        ids = ["refresh-priced", "refresh-delivery", "refresh-timeout"]
        for message_id, text in zip(ids, [
            "MYR 120, delivery in 3 days.", "Delivery in 2 days; price on request.",
            "MYR 80, delivery in 4 days.",
        ]):
            self.add_message(message_id, text)
        review = self.post("/api/quote-previews", {"message_ids": ids}).json
        original = self.post("/api/quote-evaluations", {
            "message_ids": ids, "priority": "price",
            "review_token": review["review_token"],
        }).json["evaluation"]
        worker = QuoteAnalysisWorker(self.store, "https://example.api.runpod.ai", "test-key")
        worker.model_id = "test-model"

        def mixed(parts, *_):
            if "Quotation QUOTE_3" in str(parts):
                raise QuoteAnalysisError("RunPod comparison request timed out.")
            if "Quotation QUOTE_2" in str(parts):
                return {"quotes": [{
                    "quote_key": "QUOTE_2", "delivery_days": 2, "delivery_unit": "calendar_days",
                }]}
            return {"quotes": [{"quote_key": "QUOTE_1", "currency": "MYR", "total_price": 120}]}

        with patch.object(worker, "_request_model", side_effect=mixed):
            worker._analyze(original["id"])
        original = self.store.quote_evaluation(original["id"])
        first = self.start_retry(original, review).json["evaluation"]
        with patch.object(worker, "_request_model", side_effect=mixed) as request:
            worker._analyze(first["id"])
        request.assert_called_once()
        first = self.store.quote_evaluation(first["id"])
        retry_metadata = first["result"]["retry"]
        progress = first["result"]["progress"]
        self.assertEqual(set(retry_metadata["retained"]), set(ids[:2]))
        self.assertTrue(first["result"]["quotes"][1]["conversion_error"])
        # Both saved-comparison routes invoke the normal automatic currency
        # refresh. Force its time window to elapse before each request.
        for route in (f"/api/quote-evaluations/{first['id']}", "/api/quote-evaluations"):
            snapshot = self.store.quote_evaluation(first["id"])["result"]
            snapshot["fx_checked_at"] = 0
            with self.store.db() as db:
                db.execute("UPDATE quote_evaluations SET result_json=? WHERE id=?",
                           (json.dumps(snapshot), first["id"]))
            self.assertEqual(self.client.get(route).status_code, 200)
            refreshed = self.store.quote_evaluation(first["id"])
            self.assertEqual(refreshed["result"]["retry"], retry_metadata)
            self.assertEqual(refreshed["result"]["progress"], progress)
            self.assertGreater(refreshed["result"]["fx_checked_at"], 0)
        # The next attempt still retains both successful source facts, including
        # the valid delivery-only result, instead of requesting them again.
        second = self.start_retry(refreshed, review).json["evaluation"]
        self.assertEqual(set(second["result"]["retry"]["retained"]), set(ids[:2]))
        with patch.object(worker, "_request_model", return_value={"quotes": [{
            "quote_key": "QUOTE_3", "currency": "MYR", "total_price": 80,
        }]}) as request:
            worker._analyze(second["id"])
        request.assert_called_once()
        self.assertIn("Quotation QUOTE_3", str(request.call_args))
        self.assertNotIn("Quotation QUOTE_1", str(request.call_args))
        self.assertNotIn("Quotation QUOTE_2", str(request.call_args))
        self.assertEqual(self.store.quote_evaluation(second["id"])["status"], "completed")

    def test_currency_refresh_recomputes_retry_change_notices_against_original(self):
        original, review, worker = self.mixed_retry_fixture()
        worker.rate_provider = lambda: {}
        retry = self.start_retry(original, review).json["evaluation"]
        with patch.object(worker, "_request_model", return_value={"quotes": [{
            "quote_key": "QUOTE_2", "currency": "USD", "total_price": 200,
        }]}):
            worker._analyze(retry["id"])
        saved = self.store.quote_evaluation(retry["id"])
        self.assertIsNone(saved["result"]["recommendation_message_id"])
        self.assertTrue(saved["result"]["retry"]["recommendation_changed"])
        self.assertTrue(saved["result"]["retry"]["ranking_changed"])
        snapshot = saved["result"]
        snapshot["fx_checked_at"] = 0
        with self.store.db() as db:
            db.execute("UPDATE quote_evaluations SET result_json=? WHERE id=?",
                       (json.dumps(snapshot), retry["id"]))
        rates = {"USD": {"rate": 4, "date": "2026-10-23", "source": "test reference"}}
        self.app.config["MYR_RATE_PROVIDER"] = lambda: rates
        refreshed = self.client.get(f"/api/quote-evaluations/{retry['id']}").json["evaluation"]
        self.assertEqual(refreshed["result"]["recommendation_message_id"], "retry-good")
        self.assertFalse(refreshed["result"]["retry"]["recommendation_changed"])
        self.assertTrue(refreshed["result"]["retry"]["ranking_changed"])
        for field in ("of", "retained", "previous_ranking", "previous_recommendation"):
            self.assertEqual(refreshed["result"]["retry"][field], saved["result"]["retry"][field])

    def test_retry_changed_success_requires_new_comparison_and_failed_source_needs_review(self):
        original, review, worker = self.mixed_retry_fixture()
        with self.store.db() as db:
            db.execute("UPDATE messages SET body='MYR 999' WHERE id='retry-good'")
        new_review = self.post("/api/quote-previews", {
            "message_ids": original["source_message_ids"]}).json
        rejected = self.start_retry(original, new_review)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("start a new comparison", rejected.json["error"])
        # A stale signed review cannot bypass the worker's content checks.
        started = self.start_retry(original, review).json["evaluation"]
        with patch.object(worker, "_request_model") as request:
            with self.assertRaisesRegex(QuoteAnalysisError, "retained source"):
                worker._analyze(started["id"])
        request.assert_not_called()
        with self.store.db() as db:
            db.execute("UPDATE messages SET body='MYR 120, delivery in 3 days.' WHERE id='retry-good'")
            db.execute("UPDATE messages SET body='MYR 75, delivery in 2 days.' WHERE id='retry-timeout'")
        stale = self.start_retry(original, review).json["evaluation"]
        with patch.object(worker, "_request_model") as request:
            with self.assertRaises(QuoteAnalysisError):
                worker._analyze(stale["id"])
        request.assert_not_called()
        fresh = self.post("/api/quote-previews", {
            "message_ids": original["source_message_ids"]}).json
        updated = self.start_retry(original, fresh).json["evaluation"]
        with patch.object(worker, "_request_model", return_value={"quotes": [{
            "quote_key": "QUOTE_2", "currency": "MYR", "total_price": 75,
        }]}) as request:
            worker._analyze(updated["id"])
        request.assert_called_once()
        self.assertEqual(self.store.quote_evaluation(updated["id"])["status"], "completed")

    def test_retry_cancellation_keeps_original_and_discards_late_recovered_result(self):
        original, review, worker = self.mixed_retry_fixture()
        retry = self.start_retry(original, review).json["evaluation"]
        started, release = threading.Event(), threading.Event()

        def delayed(*_):
            started.set()
            release.wait(5)
            return {"quotes": [{"quote_key": "QUOTE_2", "currency": "MYR", "total_price": 80}]}

        self.app.extensions["quote_worker"] = worker
        try:
            with patch.object(worker, "_request_model", side_effect=delayed):
                worker.submit(retry["id"])
                self.assertTrue(started.wait(3))
                cancelled = self.post(f"/api/quote-evaluations/{retry['id']}/cancel", {})
                self.assertEqual(cancelled.status_code, 200)
                release.set()
                worker.jobs.join()
            saved = self.store.quote_evaluation(retry["id"])
            self.assertEqual(saved["status"], "cancelled")
            self.assertEqual([quote["message_id"] for quote in saved["result"]["quotes"]], ["retry-good"])
            self.assertEqual(self.store.quote_evaluation(original["id"])["result"], original["result"])
            # A stopped retry can itself be retried without losing its successful fact.
            self.app.extensions["quote_worker"] = self.worker
            self.assertEqual(self.start_retry(saved, review).status_code, 202)
        finally:
            release.set()

    def test_retry_source_deletion_removes_original_and_retry_without_resurrection(self):
        original, review, worker = self.mixed_retry_fixture()
        retry = self.start_retry(original, review).json["evaluation"]

        def delete_during_request(*_):
            with self.store.db() as db:
                db.execute("DELETE FROM messages WHERE id='retry-timeout'")
            return {"quotes": [{"quote_key": "QUOTE_2", "currency": "MYR", "total_price": 80}]}

        with patch.object(worker, "_request_model", side_effect=delete_during_request):
            worker._analyze(retry["id"])
        self.assertIsNone(self.store.quote_evaluation(original["id"]))
        self.assertIsNone(self.store.quote_evaluation(retry["id"]))
        self.assertEqual(self.start_retry(original, review).status_code, 404)

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

        review = self.post("/api/quote-previews", {"message_ids": ["quote-one"]})
        self.assertEqual(review.status_code, 200)
        started = self.post("/api/quote-evaluations", {
            "message_ids": ["quote-one"], "priority": "price",
            "review_token": review.json["review_token"]})
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

    def test_review_is_private_local_and_preserves_original_attachments(self):
        self.add_message("review-doc", kind="document", filename="quotation.txt", mime_type="text/plain")
        path = self.store.attachments / "quotation.txt"
        original = b"Vendor Test <script>alert(1)</script>\nMYR 123, 4 days."
        path.write_bytes(original)
        self.store.media_saved(self.store.message("review-doc"), path, "text/plain", "quotation.txt")
        body = {"message_ids": ["review-doc"]}
        self.assertEqual(self.post("/api/quote-previews", body).status_code, 401)
        self.login()
        self.assertEqual(self.client.post("/api/quote-previews", json=body).status_code, 403)
        with patch("src.quote_analysis.requests.get") as get, patch("src.quote_analysis.requests.post") as post:
            response = self.post("/api/quote-previews", body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("MYR 123", response.json["sources"][0]["text"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.worker.jobs, [])
        get.assert_not_called()
        post.assert_not_called()
        self.assertEqual(self.post("/api/quote-evaluations", {
            "message_ids": ["review-doc"], "priority": "price"}).status_code, 400)

    def test_only_confirmed_reviewed_subset_is_sent_to_model(self):
        self.login()
        self.add_message("unconfirmed", "PRIVATE NOT CONFIRMED MYR 999")
        self.add_message("confirmed", "MYR 123, delivery in 4 days")
        review = self.post("/api/quote-previews", {"message_ids": ["unconfirmed", "confirmed"]})
        started = self.post("/api/quote-evaluations", {
            "message_ids": ["confirmed"], "priority": "price",
            "review_token": review.json["review_token"]})
        self.assertEqual(started.status_code, 202)
        worker = QuoteAnalysisWorker(self.store, "https://example.api.runpod.ai", "test-key")
        worker.model_id = "test-model"
        with patch.object(worker, "_request_model", return_value={"quotes": [{
            "quote_key": "QUOTE_1", "vendor": "Test", "currency": "MYR", "total_price": 123,
        }]}) as model:
            worker._analyze(started.json["evaluation"]["id"])
        payload = str(model.call_args.args[0])
        self.assertIn("MYR 123", payload)
        self.assertNotIn("PRIVATE NOT CONFIRMED", payload)
        self.assertEqual(self.store.quote_evaluation(started.json["evaluation"]["id"])["status"], "completed")

    def test_failed_or_unreviewed_sources_cannot_be_confirmed(self):
        self.login()
        self.add_message("good", "MYR 123")
        self.add_message("other", "MYR 999")
        self.add_message("bad", kind="document", filename="broken.xyz")
        path = self.store.attachments / "broken.xyz"
        path.write_bytes(b"not a readable document")
        self.store.media_saved(self.store.message("bad"), path, "application/octet-stream", "broken.xyz")
        review = self.post("/api/quote-previews", {"message_ids": ["good", "bad"]})
        self.assertTrue(review.json["sources"][1]["error"])
        for ids in (["bad"], ["other"]):
            response = self.post("/api/quote-evaluations", {
                "message_ids": ids, "priority": "price", "review_token": review.json["review_token"]})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.worker.jobs, [])

    def test_review_tokens_expire_and_are_session_bound(self):
        from itsdangerous import URLSafeTimedSerializer

        self.login()
        self.add_message("review-token", "MYR 123")
        review = self.post("/api/quote-previews", {"message_ids": ["review-token"]})
        serializer = URLSafeTimedSerializer(self.app.secret_key, salt="quote-review-v1")
        payload = serializer.loads(review.json["review_token"])
        with patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=int(time.time()) - 601):
            expired = serializer.dumps(payload)
        data = {"message_ids": ["review-token"], "priority": "price",
                "review_token": expired}
        self.assertEqual(self.post("/api/quote-evaluations", data).status_code, 409)
        data["review_token"] = review.json["review_token"]
        self.login()  # new session and CSRF token, same shared password
        self.assertEqual(self.post("/api/quote-evaluations", data).status_code, 400)
        data["review_token"] += "tampered"
        self.assertEqual(self.post("/api/quote-evaluations", data).status_code, 400)

    def test_source_changes_after_review_are_not_sent(self):
        self.login()
        self.add_message("changed", kind="document", filename="changed.txt", mime_type="text/plain")
        path = self.store.attachments / "changed.txt"
        path.write_text("MYR 100", encoding="utf-8")
        self.store.media_saved(self.store.message("changed"), path, "text/plain", "changed.txt")
        review = self.post("/api/quote-previews", {"message_ids": ["changed"]})
        started = self.post("/api/quote-evaluations", {
            "message_ids": ["changed"], "priority": "price",
            "review_token": review.json["review_token"]})
        path.write_text("MYR 999", encoding="utf-8")
        worker = QuoteAnalysisWorker(self.store, "https://example.api.runpod.ai", "test-key")
        with patch.object(worker, "_request_model") as model:
            with self.assertRaisesRegex(QuoteAnalysisError, "changed after review"):
                worker._analyze(started.json["evaluation"]["id"])
        model.assert_not_called()

    def test_hybrid_pdf_ocr_reads_an_image_only_pricing_page(self):
        pdf = Path(self.directory.name) / "hybrid-quotation.pdf"
        self._make_hybrid_pdf(pdf, [
            "SEARCHABLE COVER: Northwind Tools quotation number Q-3001. Please see the "
            "following pages for product and delivery details.",
            None,
            "SEARCHABLE FOOTER: Northwind Tools confirms this quotation is valid for "
            "thirty days and delivery terms are subject to availability.",
        ])

        metadata = {}
        text = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertEqual(metadata["pdf_page_count"], 3)
        self.assertIn("SEARCHABLE COVER", text)
        self.assertIn("Northwind Tools", text)
        self.assertIn("MYR 128", text)
        self.assertIn("[OCR page 2]", text)
        self.assertFalse(metadata["source_warnings"])

    def test_hybrid_pdf_ocr_reads_image_price_table_beside_searchable_quote_text(self):
        pdf = Path(self.directory.name) / "image-price-table.pdf"
        self._make_hybrid_image_table_pdf(pdf)

        metadata = {}
        text = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertEqual(metadata["pdf_page_count"], 1)
        self.assertIn("Supplier: Northwind Tools", text)
        self.assertIn("Delivery is included within six calendar days", text)
        self.assertIn("MYR 128", text)
        self.assertIn("[OCR page 1]", text)
        self.assertFalse(metadata["source_warnings"])

    def test_decorative_logo_does_not_trigger_pdf_ocr_or_a_coverage_warning(self):
        pdf = Path(self.directory.name) / "decorative-logo.pdf"
        self._make_hybrid_image_table_pdf(pdf, decorative=True)

        metadata = {}
        real_run = subprocess.run
        commands = []

        def track_commands(args, **kwargs):
            commands.append(args[0])
            return real_run(args, **kwargs)

        with patch("src.quote_analysis.subprocess.run", side_effect=track_commands):
            text = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertIn("Supplier: Northwind Tools", text)
        self.assertFalse(metadata["source_warnings"])
        self.assertNotIn("tesseract", commands)

    def test_image_price_table_after_page_five_is_disclosed_without_ocr(self):
        pdf = Path(self.directory.name) / "image-price-table-page-six.pdf"
        self._make_hybrid_image_table_pdf(pdf, page_count=6, image_page=6)
        real_run = subprocess.run
        commands = []

        def track_commands(args, **kwargs):
            commands.append(args[0])
            return real_run(args, **kwargs)

        metadata = {}
        with patch("src.quote_analysis.subprocess.run", side_effect=track_commands):
            text = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertEqual(metadata["pdf_page_count"], 6)
        self.assertTrue(metadata["ocr_truncated"])
        self.assertTrue(any(
            "sizeable image region on page 6" in warning
            and "first 5 PDF pages" in warning
            for warning in metadata["source_warnings"]
        ))
        self.assertIn("Supplier: Northwind Tools quotation Q-3006", text)
        self.assertNotIn("[OCR page 6]", text)
        self.assertNotIn("MYR 128", text)
        self.assertNotIn("tesseract", commands)

    def test_image_table_ocr_failure_is_disclosed_in_review_and_saved_result(self):
        self.login()
        self.add_message("image-table-failure", kind="document", filename="image-table.pdf",
                         mime_type="application/pdf")
        path = self.store.attachments / "image-table.pdf"
        self._make_hybrid_image_table_pdf(path)
        original = path.read_bytes()
        self.store.media_saved(self.store.message("image-table-failure"), path,
                               "application/pdf", "image-table.pdf")
        real_run = subprocess.run

        def fail_only_ocr(args, **kwargs):
            if args[0] == "tesseract":
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"isolated OCR failure")
            return real_run(args, **kwargs)

        with patch("src.quote_analysis.subprocess.run", side_effect=fail_only_ocr):
            review = self.post("/api/quote-previews", {"message_ids": ["image-table-failure"]})
            self.assertEqual(review.status_code, 200)
            source = review.json["sources"][0]
            self.assertIn("including the detected image region", source["source_warnings"][0])
            self.assertIn("Supplier: Northwind Tools", source["text"])
            self.assertIn("Source coverage warning", source["text"])
            started = self.post("/api/quote-evaluations", {
                "message_ids": ["image-table-failure"], "priority": "price",
                "review_token": review.json["review_token"]})
            self.assertEqual(started.status_code, 202)
            worker = QuoteAnalysisWorker(
                self.store, "https://example.api.runpod.ai", "test-key")
            worker.model_id = "test-model"
            with patch.object(worker, "_get_model_id", return_value="isolated-test-model"), \
                 patch.object(worker, "_request_model", return_value={"quotes": [{
                "quote_key": "QUOTE_1", "currency": "MYR", "total_price": 128,
                "source_warnings": [],
            }]}) as model:
                worker._analyze(started.json["evaluation"]["id"])
        self.assertIn("Source coverage warning", str(model.call_args.args[0]))
        saved = self.store.quote_evaluation(started.json["evaluation"]["id"])["result"]["quotes"][0]
        self.assertEqual(saved["source_warnings"], source["source_warnings"])
        self.assertTrue(saved["ocr_truncated"])
        self.assertEqual(path.read_bytes(), original)

    def test_hybrid_pdf_ocr_failure_is_disclosed_in_review_and_saved_result(self):
        self.login()
        self.add_message("hybrid-partial", kind="document", filename="hybrid.pdf",
                         mime_type="application/pdf")
        path = self.store.attachments / "hybrid.pdf"
        self._make_hybrid_pdf(path, [
            "SEARCHABLE COVER: Northwind Tools quotation number Q-3002. Please see the "
            "following pages for product and delivery details.",
            None,
            "SEARCHABLE FOOTER: This searchable page contains warranty and delivery "
            "terms but no pricing details.",
        ])
        original = path.read_bytes()
        self.store.media_saved(self.store.message("hybrid-partial"), path,
                               "application/pdf", "hybrid.pdf")
        real_run = subprocess.run

        def fail_only_ocr(args, **kwargs):
            if args[0] == "tesseract":
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"isolated OCR failure")
            return real_run(args, **kwargs)

        with patch("src.quote_analysis.subprocess.run", side_effect=fail_only_ocr):
            review = self.post("/api/quote-previews", {"message_ids": ["hybrid-partial"]})
            self.assertEqual(review.status_code, 200)
            source = review.json["sources"][0]
            self.assertIn("page 2", source["source_warnings"][0])
            self.assertIn("SEARCHABLE COVER", source["text"])
            self.assertIn("Source coverage warning", source["text"])
            started = self.post("/api/quote-evaluations", {
                "message_ids": ["hybrid-partial"], "priority": "price",
                "review_token": review.json["review_token"]})
            self.assertEqual(started.status_code, 202)
            worker = QuoteAnalysisWorker(
                self.store, "https://example.api.runpod.ai", "test-key")
            worker.model_id = "test-model"
            with patch.object(worker, "_request_model", return_value={"quotes": [{
                "quote_key": "QUOTE_1", "currency": "MYR", "total_price": 128,
                "source_warnings": [],
            }]}) as model:
                worker._analyze(started.json["evaluation"]["id"])
        payload = str(model.call_args.args[0])
        saved = self.store.quote_evaluation(started.json["evaluation"]["id"])["result"]["quotes"][0]
        self.assertEqual(saved["source_warnings"], source["source_warnings"])
        self.assertTrue(saved["ocr_truncated"])
        self.assertIn("SEARCHABLE COVER", payload)
        self.assertIn("Source coverage warning", payload)
        self.assertEqual(path.read_bytes(), original)

    def test_hybrid_scanned_pricing_after_page_five_is_omitted_and_warned(self):
        pdf = Path(self.directory.name) / "hybrid-page-six.pdf"
        searchable_pages = [
            f"Searchable quotation page {index}: Supplier Northwind section {index} "
            f"contains terms, warranty details, and information about delivery."
            for index in range(1, 6)
        ]
        self._make_hybrid_pdf(pdf, searchable_pages + [None])
        commands = []
        real_run = subprocess.run

        def track_ocr(args, **kwargs):
            if args[0] == "tesseract":
                commands.append(args)
            return real_run(args, **kwargs)

        metadata = {}
        with patch("src.quote_analysis.subprocess.run", side_effect=track_ocr):
            text = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertEqual(metadata["pdf_page_count"], 6)
        self.assertTrue(metadata["ocr_truncated"])
        self.assertIn("page 6", metadata["source_warnings"][0])
        self.assertIn("first 5 PDF pages", metadata["source_warnings"][0])
        self.assertFalse(commands, "Pages after the five-page OCR boundary must not be OCRed.")
        self.assertNotIn("MYR 128", text)

    def test_scanned_pdf_fixture_is_ocrd_locally(self):
        from playwright.sync_api import sync_playwright

        chromium = (
            os.getenv("CHROMIUM_PATH")
            or shutil.which("chromium")
            or ("/repl/tools/bin/chromium" if Path("/repl/tools/bin/chromium").exists() else None)
        )
        self.assertIsNotNone(chromium, "Chromium is needed to build the scanned-PDF fixture.")
        fixture = Path(__file__).parent / "fixtures" / "scanned-quotation.html"
        pdf = Path(self.directory.name) / "scanned-quotation.pdf"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=chromium,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 1000})
                page.goto(fixture.resolve().as_uri(), wait_until="load")
                scan_png = base64.b64encode(page.screenshot(type="png")).decode("ascii")
                page.set_content(
                    "<!doctype html><style>@page{size:A4;margin:0}"
                    "html,body{margin:0;width:210mm;height:297mm}"
                    "img{display:block;width:100%;height:auto}</style>"
                    f'<img alt="" src="data:image/png;base64,{scan_png}">',
                    wait_until="load",
                )
                page.pdf(path=str(pdf), print_background=True)
            finally:
                browser.close()

        metadata = {}
        text = _document_text(pdf, "application/pdf", metadata=metadata).casefold()
        self.assertEqual(metadata["pdf_page_count"], 1)
        self.assertFalse(metadata["ocr_truncated"])
        self.assertFalse(metadata["source_warnings"])
        self.assertIn("northwind", text)
        self.assertIn("myr 128", text)
        self.assertIn(f"first {MAX_OCR_PAGES} pages", text)

    def test_scanned_pdf_ocr_is_capped_at_five_pages(self):
        pdf = Path(self.directory.name) / "scanned-multipage.pdf"
        pdf.write_bytes(b"test PDF")
        commands = []

        def fake_run(args, **kwargs):
            commands.append((args, kwargs))
            if args[0] == "pdfinfo":
                return SimpleNamespace(returncode=0, stdout=b"Pages: 7\n")
            if args[0] == "pdftotext":
                return SimpleNamespace(returncode=0, stdout=b"")
            if args[0] == "pdftoppm":
                prefix = Path(args[-1])
                for index in range(1, MAX_OCR_PAGES + 3):
                    Path(f"{prefix}-{index:02}.jpg").write_bytes(b"jpeg")
                return SimpleNamespace(returncode=0, stdout=b"")
            return SimpleNamespace(returncode=0, stdout=b"MYR 128")

        metadata = {}
        with patch("src.quote_analysis.subprocess.run", side_effect=fake_run):
            result = _document_text(pdf, "application/pdf", metadata=metadata)
        self.assertEqual(metadata["pdf_page_count"], 7)
        self.assertTrue(metadata["ocr_truncated"])
        self.assertIn("pages 6–7 are omitted", metadata["source_warnings"][0])
        self.assertEqual(
            sum(args[0] == "tesseract" for args, _ in commands),
            MAX_OCR_PAGES,
        )
        render_args = next(args for args, _ in commands if args[0] == "pdftoppm")
        self.assertEqual(render_args[render_args.index("-l") + 1], str(MAX_OCR_PAGES))
        self.assertIn("MYR 128", result)
        self.assertTrue(all(
            kwargs.get("timeout", 0) <= MAX_OCR_PAGE_SECONDS
            for args, kwargs in commands if args[0] == "tesseract"
        ))

    def test_ocr_warning_survives_review_and_is_saved_without_model_cooperation(self):
        self.login()
        self.add_message("scan-seven", kind="document", filename="scan-seven.pdf",
                         mime_type="application/pdf")
        path = self.store.attachments / "scan-seven.pdf"
        original = b"isolated scanned PDF fixture"
        path.write_bytes(original)
        self.store.media_saved(self.store.message("scan-seven"), path,
                               "application/pdf", "scan-seven.pdf")
        commands = []
        def fake_run(args, **kwargs):
            commands.append(args)
            if args[0] == "pdfinfo":
                return SimpleNamespace(returncode=0, stdout=b"Pages: 7\n")
            if args[0] == "pdftotext":
                return SimpleNamespace(returncode=0, stdout=b"")
            if args[0] == "pdftoppm":
                for index in range(1, 8):
                    Path(f"{args[-1]}-{index:02}.jpg").write_bytes(b"image")
                return SimpleNamespace(returncode=0, stdout=b"")
            page_number = int(Path(args[1]).stem.split("-")[-1])
            return SimpleNamespace(returncode=0, stdout=f"PAGE_{page_number}_PRICE MYR 128".encode())

        with patch("src.quote_analysis.subprocess.run", side_effect=fake_run):
            review = self.post("/api/quote-previews", {"message_ids": ["scan-seven"]})
            self.assertEqual(review.status_code, 200)
            source = review.json["sources"][0]
            self.assertEqual(source["pdf_page_count"], 7)
            self.assertEqual(source["ocr_page_limit"], 5)
            self.assertTrue(source["ocr_truncated"])
            self.assertIn("first 5", source["source_warnings"][0])
            self.assertNotIn("PAGE_6_PRICE", source["text"])
            self.assertNotIn("PAGE_7_PRICE", source["text"])
            started = self.post("/api/quote-evaluations", {
                "message_ids": ["scan-seven"], "priority": "price",
                "review_token": review.json["review_token"]})
            self.assertEqual(started.status_code, 202)
            worker = QuoteAnalysisWorker(self.store, "https://example.api.runpod.ai", "test-key")
            worker.model_id = "test-model"
            with patch.object(worker, "_request_model", return_value={"quotes": [{
                "quote_key": "QUOTE_1", "currency": "MYR", "total_price": 128,
                "source_warnings": [],  # The model cannot suppress local warnings.
            }]}) as model:
                worker._analyze(started.json["evaluation"]["id"])
        payload = str(model.call_args.args[0])
        self.assertNotIn("PAGE_6_PRICE", payload)
        self.assertNotIn("PAGE_7_PRICE", payload)
        self.assertIn("first 5", payload)
        saved = self.store.quote_evaluation(started.json["evaluation"]["id"])["result"]["quotes"][0]
        self.assertEqual(saved["source_warnings"], source["source_warnings"])
        self.assertEqual(saved["pdf_page_count"], 7)
        self.assertTrue(saved["ocr_truncated"])
        self.assertEqual(sum(args[0] == "tesseract" for args in commands), 10)
        self.assertEqual(path.read_bytes(), original)

    def test_ocr_warning_boundary_unknown_count_and_searchable_pdf(self):
        pdf = Path(self.directory.name) / "boundary.pdf"
        pdf.write_bytes(b"test PDF")
        for count in (5, 6, None):
            metadata = {}
            with patch("src.quote_analysis._pdf_page_count", return_value=count), \
                 patch("src.quote_analysis._pdf_pages_with_large_images", return_value=([], False)), \
                 patch("src.quote_analysis.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"")), \
                 patch("src.quote_analysis._ocr_pdf", return_value="MYR 128"):
                _document_text(pdf, "application/pdf", metadata=metadata)
            self.assertEqual(bool(metadata["source_warnings"]), count != 5)
            self.assertEqual(metadata["ocr_truncated"], None if count is None else count > 5)
        metadata = {}
        searchable = "\f".join(
            f"Searchable page {index} has enough distinct native quotation text for "
            f"products, prices, delivery terms, and service details number {index}."
            for index in range(1, 9)
        ) + "\f"
        with patch("src.quote_analysis._pdf_page_count", return_value=8), \
             patch("src.quote_analysis._pdf_pages_with_large_images", return_value=([], False)), \
             patch("src.quote_analysis.subprocess.run", return_value=SimpleNamespace(
                 returncode=0, stdout=searchable.encode("utf-8"))):
            self.assertIn(searchable, _document_text(pdf, "application/pdf", metadata=metadata))
        self.assertEqual(metadata["pdf_page_count"], 8)
        self.assertFalse(metadata.get("source_warnings"))
    def test_scanned_pdf_renderer_timeout_is_a_clear_per_file_error(self):
        pdf = Path(self.directory.name) / "stalled-scan.pdf"
        pdf.write_bytes(b"test PDF")

        def timeout_renderer(args, **kwargs):
            if args[0] == "pdftotext":
                return SimpleNamespace(returncode=0, stdout=b"")
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        with patch("src.quote_analysis.subprocess.run", side_effect=timeout_renderer):
            with self.assertRaisesRegex(QuoteAnalysisError, "local OCR time limit"):
                _document_text(pdf, "application/pdf")

    def test_legacy_xls_fixture_is_read_and_conversion_time_is_bounded(self):
        fixture = Path(__file__).parent / "fixtures" / "legacy-quotation.csv"
        xls = Path(self.directory.name) / "legacy-quotation.xls"
        created = subprocess.run(
            ["ssconvert", str(fixture), str(xls)],
            capture_output=True,
            timeout=MAX_XLS_CONVERT_SECONDS,
            check=False,
        )
        self.assertEqual(created.returncode, 0, created.stderr.decode("utf-8", errors="replace"))
        extracted = _document_text(xls, "application/vnd.ms-excel")
        self.assertIn("Northwind Tools", extracted)
        self.assertIn("MYR 128", extracted)
        self.assertIn("6 working days", extracted)

        with patch(
            "src.quote_analysis.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ssconvert", MAX_XLS_CONVERT_SECONDS),
        ) as conversion:
            with self.assertRaisesRegex(QuoteAnalysisError, "25-second limit"):
                _document_text(xls, "application/vnd.ms-excel")
        self.assertEqual(conversion.call_args.kwargs["timeout"], MAX_XLS_CONVERT_SECONDS)

    def test_legacy_xls_size_limit_fails_before_conversion(self):
        oversized = Path(self.directory.name) / "oversized.xls"
        with oversized.open("wb") as stream:
            stream.truncate(MAX_DOCUMENT_BYTES + 1)
        with patch("src.quote_analysis.subprocess.run") as conversion:
            with self.assertRaisesRegex(QuoteAnalysisError, "20 MB"):
                _document_text(oversized, "application/vnd.ms-excel")
        conversion.assert_not_called()

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
        worker.model_id = "test-model"
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
            self.assertTrue(current["result"]["partial"])
            self.assertEqual(current["result"]["quotes"], [])
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
        with patch("src.quote_analysis._pdf_page_count", return_value=1), \
             patch("src.quote_analysis._pdf_pages_with_large_images", return_value=([], False)), \
             patch("src.quote_analysis.subprocess.run", return_value=SimpleNamespace(
                 returncode=0, stdout=(
                     b"Northwind Tools quotation includes MYR 88 and delivery in 2 calendar days. "
                     b"Please review the written payment and warranty conditions before ordering."
                 ))) as run:
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

    def test_multimodal_readiness_uses_only_synthetic_image_and_ogg_samples(self):
        worker = QuoteAnalysisWorker(None, "https://example.api.runpod.ai", "fake-test-key")
        models = SimpleNamespace(
            status_code=200, ok=True,
            json=lambda: {"data": [{"id": "served-synthetic-model"}]},
        )
        image_result = SimpleNamespace(
            status_code=200, ok=True,
            json=lambda: {"choices": [{"message": {"content": "blue"}}]},
        )
        audio_result = SimpleNamespace(
            status_code=200, ok=True,
            json=lambda: {"choices": [{"message": {"content": "The word is amber."}}]},
        )
        with patch("src.quote_analysis.requests.get", return_value=models) as get_model, \
                patch("src.quote_analysis.requests.post",
                      side_effect=[image_result, audio_result]) as post:
            result = worker.check_multimodal_readiness()

        self.assertEqual(result["model_id"], "served-synthetic-model")
        self.assertTrue(result["model_ready"])
        self.assertTrue(result["ready"])
        self.assertTrue(result["image"]["ready"])
        self.assertTrue(result["ogg_voice"]["ready"])
        get_model.assert_called_once()
        image_part = post.call_args_list[0].kwargs["json"]["messages"][0]["content"][1]
        self.assertEqual(image_part["type"], "image_url")
        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/png;base64,"))
        audio_part = post.call_args_list[1].kwargs["json"]["messages"][0]["content"][1]
        self.assertEqual(audio_part["type"], "input_audio")
        self.assertEqual(audio_part["input_audio"]["format"], "ogg")
        self.assertTrue(
            base64.b64decode(audio_part["input_audio"]["data"]).startswith(b"OggS")
        )

    def test_multimodal_readiness_reports_per_format_rejection(self):
        worker = QuoteAnalysisWorker(None, "https://example.api.runpod.ai", "fake-test-key")
        models = SimpleNamespace(
            status_code=200, ok=True,
            json=lambda: {"data": [{"id": "served-synthetic-model"}]},
        )
        image_rejected = SimpleNamespace(status_code=415, ok=False)
        audio_result = SimpleNamespace(
            status_code=200, ok=True,
            json=lambda: {"choices": [{"message": {"content": "The word is amber."}}]},
        )
        with patch("src.quote_analysis.requests.get", return_value=models), \
                patch("src.quote_analysis.requests.post",
                      side_effect=[image_rejected, audio_result]):
            result = worker.check_multimodal_readiness()

        self.assertFalse(result["ready"])
        self.assertFalse(result["image"]["ready"])
        self.assertIn("image input", result["image"]["message"])
        self.assertTrue(result["ogg_voice"]["ready"])

    def test_readiness_endpoint_is_staff_only_and_returns_check_results(self):
        url = "/api/quote-analysis-readiness"
        self.assertEqual(self.client.post(
            url, json={}, headers={"X-CSRF-Token": self.csrf}).status_code, 401)
        self.login()
        checker = Mock(return_value={
            "model_id": "synthetic-test-model",
            "model_ready": True,
            "ready": False,
            "image": {"ready": False, "message": "Image format rejected."},
            "ogg_voice": {"ready": True, "message": "Synthetic sample recognized successfully."},
        })
        self.app.extensions["quote_worker"].check_multimodal_readiness = checker
        response = self.post(url, {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["readiness"]["model_id"], "synthetic-test-model")
        self.assertFalse(response.json["readiness"]["image"]["ready"])
        checker.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
