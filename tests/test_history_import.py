"""Safe tests for importing text-only chat exports; never contacts real clients."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import tempfile
import unittest
from zoneinfo import ZoneInfo

from src.history_import import HistoryImportError, parse_whatsapp_export
from src.server import create_app


class HistoryImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": self.directory.name,
            "SECRET_KEY": "history-import-test-session",
            "STAFF_PASSWORD": "history-import-test-password",
            "META_ACCESS_TOKEN": "",
        })
        self.store = self.app.extensions["store"]
        self.store.learn_phone("123456789", "+60 123 456 789")
        self.client = self.app.test_client()
        csrf = self.client.get("/api/session").json["csrf_token"]
        login = self.client.post("/api/login",
                                 json={"password": "history-import-test-password"},
                                 headers={"X-CSRF-Token": csrf})
        self.assertEqual(login.status_code, 200)
        self.csrf = login.json["csrf_token"]

    def tearDown(self):
        self.directory.cleanup()

    def upload(self, text, filename="WhatsApp Chat.txt", **fields):
        data = {
            "file": (BytesIO(text), filename),
            "client_phone": "+60 123 456 789",
            "client_name": "Example Client",
            "date_order": "YMD",
            "timezone": "UTC",
            **fields,
        }
        return self.client.post(
            "/api/history/import", data=data,
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_parser_understands_export_formats_multiline_and_timezone(self):
        content = (
            "[31/12/2024, 9:05 PM] You: Sent a reply\n"
            "continued on a second line\n"
            "2025-01-01, 00:10 - Client: New Year message\n"
        ).encode()
        parsed = parse_whatsapp_export(
            content, date_order="DMY", timezone_name="Asia/Kuala_Lumpur")
        self.assertEqual(len(parsed["messages"]), 2)
        self.assertEqual(parsed["messages"][0]["direction"], "outbound")
        self.assertIn("continued on a second line", parsed["messages"][0]["body"])
        expected = int(datetime(2025, 1, 1, 0, 10,
                                tzinfo=ZoneInfo("Asia/Kuala_Lumpur")).timestamp())
        self.assertEqual(parsed["messages"][1]["timestamp"], expected)
        self.assertEqual(parsed["contact_sender"], "Client")

    def test_import_is_searchable_idempotent_and_does_not_open_reply_window(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        first = now - timedelta(minutes=3)
        second = now - timedelta(minutes=2)
        export = (
            f"{first:%Y-%m-%d}, {first:%H:%M} - Example Client: please find the invoice\n"
            f"{second:%Y-%m-%d}, {second:%H:%M} - You: I will check it\n"
        ).encode()

        response = self.upload(export)
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["messages_imported"], 2)
        self.assertEqual(len(self.app.extensions["graph"].__dict__.get("calls", [])), 0)

        conversation = self.store.conversation(response.json["conversation_id"])
        self.assertEqual(conversation["last_inbound_at"], 0)
        self.assertFalse(conversation["window_open"])
        history = self.client.get(
            f"/api/conversations/{conversation['id']}/messages").json["messages"]
        self.assertEqual([item["direction"] for item in history], ["inbound", "outbound"])
        self.assertTrue(all(item["status"] == "history" for item in history))
        self.assertEqual(self.client.get("/api/messages/search?q=invoice").json["results"][0]["snippet"],
                         "please find the invoice")
        self.assertIn("please find the invoice", self.store.log_path.read_text())

        duplicate = self.upload(export)
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.json["messages_imported"], 0)
        self.assertEqual(len(self.store.messages(conversation["id"])["messages"]), 2)
        clear = self.client.delete(
            f"/api/conversations/{conversation['id']}/history",
            json={"confirmation": "DELETE", "delete_attachments": False},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(clear.status_code, 200)
        reimport = self.upload(export)
        self.assertEqual(reimport.status_code, 201)
        self.assertEqual(reimport.json["messages_imported"], 0)
        self.assertEqual(self.store.messages(conversation["id"])["messages"], [])

    def test_import_requires_staff_and_rejects_nontext_or_group_exports(self):
        stranger = self.app.test_client()
        csrf = stranger.get("/api/session").json["csrf_token"]
        response = stranger.post(
            "/api/history/import",
            data={"file": (BytesIO(b"unused"), "chat.txt")},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 401)

        csrf_response = self.client.post(
            "/api/history/import",
            data={"file": (BytesIO(b"unused"), "chat.txt")},
        )
        self.assertEqual(csrf_response.status_code, 403)
        self.assertEqual(self.upload(b"unused", filename="chat.zip").status_code, 400)

        group = (
            "01/01/2025, 10:00 - Alice: hello\n"
            "01/01/2025, 10:01 - Bob: hi\n"
        ).encode()
        result = self.upload(group, date_order="DMY")
        self.assertEqual(result.status_code, 400)
        self.assertIn("one-to-one", result.json["error"])

    def test_date_order_and_invalid_timezone_are_validated(self):
        parsed = parse_whatsapp_export(
            b"03/04/2025, 09:00 - You: hi\n", date_order="MDY", timezone_name="UTC")
        expected = int(datetime(2025, 3, 4, 9, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(parsed["messages"][0]["timestamp"], expected)
        with self.assertRaises(HistoryImportError):
            parse_whatsapp_export(b"01/02/2025, 09:00 - You: hi\n",
                                  timezone_name="../../etc/passwd")
        with self.assertRaises(HistoryImportError):
            parse_whatsapp_export(b"\xff", timezone_name="UTC")

    def test_history_pagination_keeps_imported_messages_in_time_order(self):
        conversation = self.store.get_or_create_conversation(
            "123456789", "60123456789", "Example Client")
        live_start = int(datetime.now(timezone.utc).timestamp()) - 3 * 3600
        with self.store.db() as db:
            db.executemany("""
                INSERT INTO messages
                (id,conversation_id,direction,type,body,timestamp,status,attachment_state,raw)
                VALUES (?,?,?,'text',?,?,'received','none','{}')
            """, [(f"live-{index}", conversation["id"], "inbound", f"live message {index}",
                   live_start + index * 60) for index in range(120)])

        older_time = datetime.fromtimestamp(live_start - 120, timezone.utc)
        newer_time = older_time + timedelta(minutes=1)
        export = (
            f"{older_time:%Y-%m-%d}, {older_time:%H:%M} - Example Client: "
            "historical search token\n"
            f"{newer_time:%Y-%m-%d}, {newer_time:%H:%M} - You: archived response\n"
        ).encode()
        self.assertEqual(self.upload(export).status_code, 201)

        newest = self.store.messages(conversation["id"])
        self.assertTrue(newest["has_more"])
        cursor = newest["next_before"]
        older = self.store.messages(
            conversation["id"], before_timestamp=cursor["timestamp"], before_seq=cursor["seq"])
        combined = older["messages"] + newest["messages"]
        self.assertEqual(len(combined), 122)
        self.assertEqual(
            [(item["timestamp"], item["seq"]) for item in combined],
            sorted((item["timestamp"], item["seq"]) for item in combined),
        )
        hit = self.store.search_messages("historical search token")[0]
        around = self.store.messages(
            conversation["id"], around_timestamp=hit["timestamp"], around_seq=hit["seq"])
        self.assertIn(hit["message_id"], [item["id"] for item in around["messages"]])


if __name__ == "__main__":
    unittest.main()
