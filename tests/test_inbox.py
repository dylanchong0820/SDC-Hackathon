"""Isolated integration tests: no real customer messages or Meta API calls."""
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from src.server import create_app
from src.storage import Store
from src.whatsapp import GraphClient, GraphError, MediaWorker, process_event


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.fail = False

    def send_text(self, phone, recipient, text):
        self.calls.append((phone, recipient, text))
        if self.fail:
            raise GraphError("Meta rejected this test message.")
        return "wamid.outgoing-" + str(len(self.calls))

    def download_media(self, row, store):
        path = store.attachments / row["type"] / (row["id"] + ".bin")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"test attachment")
        return path, "application/octet-stream", row["filename"] or "attachment.bin"


def event(messages=None, statuses=None, phone="123456789"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{
        "field": "messages", "value": {
            "metadata": {"phone_number_id": phone, "display_phone_number": "+60 123 456 789"},
            "contacts": [{"wa_id": "60123456789", "profile": {"name": "Test Customer"}}],
            "messages": messages or [], "statuses": statuses or [],
        }
    }]}]}


def text_message(identifier="wamid.inbound", body="Hello", timestamp=None):
    return {"id": identifier, "from": "60123456789", "type": "text",
            "timestamp": str(timestamp or int(time.time())), "text": {"body": body}}


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.graph = FakeGraph()
        self.app = create_app({
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": self.directory.name,
            "SECRET_KEY": "test-session-secret-not-used-outside-tests",
            "STAFF_PASSWORD": "test-staff-password-only",
            "META_APP_SECRET": "test-app-secret",
            "WEBHOOK_VERIFY_TOKEN": "test-verification-token",
            "META_ACCESS_TOKEN": "test-access-token",
            "GRAPH_CLIENT": self.graph,
        })
        self.store = self.app.extensions["store"]
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/session").json["csrf_token"]

    def tearDown(self):
        self.directory.cleanup()

    def post(self, url, data):
        return self.client.post(url, json=data, headers={"X-CSRF-Token": self.csrf})

    def login(self):
        result = self.post("/api/login", {"password": "test-staff-password-only"})
        self.assertEqual(result.status_code, 200)
        self.csrf = result.json["csrf_token"]

    def webhook(self, payload):
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-app-secret", raw, hashlib.sha256).hexdigest()
        return self.client.post("/webhook", data=raw, content_type="application/json",
                                headers={"X-Hub-Signature-256": signature})

    def receive(self, message=None):
        response = self.webhook(event([message or text_message()]))
        self.assertEqual(response.status_code, 200)
        return self.store.conversations()[0]

    def test_auth_csrf_and_no_public_private_data(self):
        for path in ("/api/conversations", "/api/status", "/api/settings",
                     "/api/conversations/1/messages", "/api/media/any"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"password": "x"}).status_code, 403)
        self.assertEqual(self.post("/api/login", {"password": "wrong"}).status_code, 401)
        self.login()
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.assertEqual(self.post("/api/logout", {}).status_code, 200)
        self.assertEqual(self.client.get("/api/conversations").status_code, 401)

    def test_multiple_employees_share_password_with_independent_sessions(self):
        self.receive()
        employees = [self.app.test_client() for _ in range(3)]

        def sign_in(client):
            csrf = client.get("/api/session").json["csrf_token"]
            result = client.post("/api/login",
                                 json={"password": "test-staff-password-only"},
                                 headers={"X-CSRF-Token": csrf})
            self.assertEqual(result.status_code, 200)
            return result.json["csrf_token"]

        with ThreadPoolExecutor(max_workers=3) as pool:
            tokens = list(pool.map(sign_in, employees))
        self.assertEqual(len(set(tokens)), 3)
        for employee in employees:
            self.assertTrue(employee.get("/api/session").json["authenticated"])
            self.assertEqual(employee.get("/api/status").status_code, 200)
            conversations = employee.get("/api/conversations")
            self.assertEqual(conversations.status_code, 200)
            self.assertEqual(len(conversations.json["conversations"]), 1)

        # Tokens belong to each employee's own browser, even with one password.
        self.assertEqual(employees[0].post("/api/logout", json={},
                         headers={"X-CSRF-Token": tokens[1]}).status_code, 403)
        self.assertEqual(employees[0].post("/api/logout", json={},
                         headers={"X-CSRF-Token": tokens[0]}).status_code, 200)
        self.assertEqual(employees[0].get("/api/conversations").status_code, 401)
        for employee in employees[1:]:
            self.assertEqual(employee.get("/api/conversations").status_code, 200)

        # Signing in again must not replace anyone else's active session.
        sign_in(employees[0])
        for employee in employees:
            self.assertTrue(employee.get("/api/session").json["authenticated"])

    def test_login_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.post("/api/login", {"password": "wrong"}).status_code, 401)
        self.assertEqual(self.post("/api/login", {"password": "wrong"}).status_code, 429)

    def test_token_handshake_and_missing_challenge(self):
        params = {"hub.mode": "subscribe", "hub.verify_token": "test-verification-token",
                  "hub.challenge": "12345"}
        result = self.client.get("/webhook", query_string=params)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.text, "12345")
        params["hub.verify_token"] = "bad"
        self.assertEqual(self.client.get("/webhook", query_string=params).status_code, 403)
        params["hub.verify_token"] = "test-verification-token"
        params.pop("hub.challenge")
        self.assertEqual(self.client.get("/webhook", query_string=params).status_code, 403)

    def test_signed_events_required_and_unconfigured_rejected(self):
        self.assertEqual(self.client.post("/webhook", json=event()).status_code, 403)
        self.app.config["META_APP_SECRET"] = ""
        self.assertEqual(self.client.post("/webhook", json=event()).status_code, 503)

    def test_text_logs_persistence_and_duplicate_delivery(self):
        conversation = self.receive(text_message(body="Hello\nSecond line"))
        self.receive(text_message(body="Hello\nSecond line"))
        rows = self.store.messages(conversation["id"])["messages"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"], "Hello\nSecond line")
        self.assertEqual(self.store.conversation(conversation["id"])["unread_count"], 1)
        self.assertIn("Message: Hello\\nSecond line", self.store.log_path.read_text())
        self.assertEqual(len(self.store.log_path.read_text().splitlines()), 1)
        reopened = Store(self.directory.name)
        self.assertEqual(len(reopened.messages(conversation["id"])["messages"]), 1)

    def test_all_media_types_are_queued_saved_and_private(self):
        messages = []
        for n, kind in enumerate(["image", "document", "audio", "video", "sticker"]):
            messages.append({
                "id": f"wamid.media-{n}", "from": "60123456789", "type": kind,
                "timestamp": str(int(time.time())),
                kind: {"id": str(1000 + n), "mime_type": "application/octet-stream",
                       "filename": f"file-{n}.bin", "caption": "Test caption"},
            })
        self.assertEqual(self.webhook(event(messages)).status_code, 200)
        worker = MediaWorker(self.store, self.graph)
        for message in messages:
            self.assertTrue(worker.process_one())
            row = self.store.message(message["id"])
            self.assertEqual(row["attachment_state"], "saved")
            self.assertTrue(Path(row["attachment_path"]).exists())
            self.assertEqual(self.client.get(f"/api/media/{row['id']}").status_code, 401)
        self.login()
        result = self.client.get("/api/media/wamid.media-1")
        self.assertEqual(result.data, b"test attachment")
        self.assertIn("attachment", result.headers["Content-Disposition"])
        result.close()
        self.assertIn("[ATTACHMENT]", self.store.log_path.read_text())

    def test_all_entries_changes_and_unknown_types_are_kept(self):
        payload = event([text_message("wamid.one")])
        other = event([{
            "id": "wamid.location", "from": "60123456789", "type": "location",
            "location": {"latitude": 3.14, "longitude": 101.5, "name": "Office"},
        }, {
            "id": "wamid.future", "from": "60123456789", "type": "future_type",
            "future_type": {"example": "retain this"},
        }])
        payload["entry"].extend(other["entry"])
        self.assertEqual(self.webhook(payload).status_code, 200)
        conversation = self.store.conversations()[0]
        messages = self.store.messages(conversation["id"])["messages"]
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[1]["body"], "Office")
        self.assertEqual(messages[2]["raw"]["future_type"]["example"], "retain this")

    def test_invalid_payload_is_not_silently_acknowledged(self):
        for payload in (None, [], {}, {"entry": ["invalid"]}):
            self.assertEqual(self.webhook(payload).status_code, 400)

    def test_text_send_idempotency_and_status_updates(self):
        conversation = self.receive()
        self.login()
        body = {"conversation_id": conversation["id"], "text": "Thanks for your message",
                "client_id": "test-client-id-123456789"}
        response = self.post("/api/messages", body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["message"]["status"], "accepted")
        self.assertEqual(len(self.graph.calls), 1)
        self.assertEqual(self.post("/api/messages", body).status_code, 200)
        self.assertEqual(len(self.graph.calls), 1)
        for status in ["sent", "delivered", "read", "sent"]:
            self.webhook(event(statuses=[{"id": "wamid.outgoing-1", "status": status,
                                          "timestamp": str(int(time.time()))}]))
        row = self.store.message(response.json["message"]["id"])
        self.assertEqual(row["status"], "read")
        body["text"] = "Different"
        self.assertEqual(self.post("/api/messages", body).status_code, 409)

    def test_send_error_is_saved_and_not_faked_as_sent(self):
        conversation = self.receive()
        self.login()
        self.graph.fail = True
        result = self.post("/api/messages", {
            "conversation_id": conversation["id"], "text": "Test",
            "client_id": "test-error-client-id-123456",
        })
        self.assertEqual(result.json["message"]["status"], "failed")
        self.assertIn("rejected", result.json["message"]["error"])

    def test_reply_window_phone_settings_and_search(self):
        old = self.receive(text_message(timestamp=int(time.time()) - 90000))
        self.login()
        result = self.post("/api/messages", {
            "conversation_id": old["id"], "text": "Test",
            "client_id": "test-expired-client-id-123456",
        })
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.graph.calls), 0)
        self.assertEqual(len(self.client.get("/api/conversations?q=Customer").json["conversations"]), 1)
        self.assertEqual(len(self.client.get("/api/conversations?q=missing").json["conversations"]), 0)
        self.assertEqual(self.post("/api/settings", {"phone_number_id": "987654321"}).status_code, 200)
        self.assertEqual(self.client.get("/api/settings").json["phone_number_id"], "987654321")
        created = self.post("/api/conversations", {"recipient": "+60 199 999 999", "name": "New contact"})
        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.json["conversation"]["window_open"])
        self.assertEqual(self.post(f"/api/conversations/{old['id']}/read", {}).status_code, 200)
        self.assertEqual(self.store.conversation(old["id"])["unread_count"], 0)

    def test_media_failure_is_retryable(self):
        self.receive({
            "id": "wamid.failed-media", "from": "60123456789", "type": "document",
            "document": {"id": "100000", "filename": "report.pdf"},
        })
        self.graph.download_media = lambda *args: (_ for _ in ()).throw(GraphError("Expired media URL"))
        worker = MediaWorker(self.store, self.graph)
        for _ in range(3):
            with self.store.db() as db:
                db.execute("UPDATE messages SET next_attempt=0")
            worker.process_one()
        self.assertEqual(self.store.message("wamid.failed-media")["attachment_state"], "failed")
        self.login()
        result = self.post("/api/messages/wamid.failed-media/retry-media", {})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.store.message("wamid.failed-media")["attachment_state"], "pending")

    def test_media_path_cannot_escape_attachments(self):
        conversation = self.receive()
        with self.store.db() as db:
            db.execute("UPDATE messages SET attachment_path=?,attachment_state='saved'",
                       (str(Path(self.directory.name) / "messages.txt"),))
        self.login()
        self.assertEqual(self.client.get("/api/media/wamid.inbound").status_code, 404)

    def test_document_extensions_and_same_second_files_do_not_overwrite(self):
        graph = GraphClient("fake-test-token")
        for number, extension in enumerate((".pdf", ".xlsx", ".docx", ".csv", ".zip", ".pptx", ".txt")):
            identifier = f"wamid.filetype-{number}"
            self.receive({
                "id": identifier, "from": "60123456789", "type": "document",
                "document": {"id": str(2000 + number), "filename": "report" + extension},
            })
            graph.call = lambda *args: {"url": "https://lookaside.fbsbx.com/attachment",
                                        "mime_type": "application/octet-stream"}
            with patch("src.whatsapp.requests.get") as download:
                response = download.return_value.__enter__.return_value
                response.status_code = 200
                response.headers = {}
                response.iter_content.return_value = [b"first", b"second"]
                path, mime, filename = graph.download_media(self.store.message(identifier), self.store)
            self.assertEqual(path.suffix, extension)
            self.assertEqual(path.read_bytes(), b"firstsecond")
            self.assertEqual(filename, "report" + extension)
        self.assertEqual(len(list((self.store.attachments / "document").iterdir())), 7)

    def test_malformed_staff_json_returns_400_not_server_error(self):
        self.login()
        for endpoint in ("/api/settings", "/api/messages", "/api/conversations"):
            self.assertEqual(self.post(endpoint, []).status_code, 400)

    def test_graph_request_payload_and_error_handling(self):
        graph = GraphClient("fake-test-token")
        with patch("src.whatsapp.requests.request") as request:
            request.return_value.ok = True
            request.return_value.json.return_value = {"messages": [{"id": "wamid.accepted"}]}
            self.assertEqual(graph.send_text("12345", "60123456789", "Hello"), "wamid.accepted")
            args = request.call_args.kwargs
            self.assertEqual(args["json"]["type"], "text")
            self.assertEqual(args["json"]["to"], "60123456789")
            self.assertEqual(args["headers"]["Authorization"], "Bearer fake-test-token")
            request.return_value.ok = False
            request.return_value.json.return_value = {"error": {"code": 190, "message": "expired"}}
            with self.assertRaisesRegex(GraphError, "expired or invalid"):
                graph.send_text("12345", "60123456789", "Hello")

    def test_untrusted_media_url_is_rejected_before_download(self):
        graph = GraphClient("fake-test-token")
        graph.call = lambda *args: {"url": "https://attacker.example/file", "mime_type": "image/png"}
        self.receive({
            "id": "wamid.untrusted", "from": "60123456789", "type": "image", "image": {"id": "12345"},
        })
        row = self.store.message("wamid.untrusted")
        with patch("src.whatsapp.requests.get") as download:
            with self.assertRaisesRegex(GraphError, "untrusted"):
                graph.download_media(row, self.store)
            download.assert_not_called()

    def test_legacy_logs_are_indexed_without_rewriting_or_duplicating(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "messages.txt"
            original = "[2026-01-01 12:00:00] From: 60123456789 | Type: text | Message: Historical message\n"
            log.write_text(original)
            store = Store(directory)
            conversation = store.conversations()[0]
            self.assertEqual(store.messages(conversation["id"])["messages"][0]["body"], "Historical message")
            self.assertEqual(log.read_text(), original)
            store = Store(directory)
            self.assertEqual(len(store.messages(conversation["id"])["messages"]), 1)


if __name__ == "__main__":
    unittest.main()
