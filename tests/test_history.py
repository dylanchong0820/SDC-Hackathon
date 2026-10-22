"""History deletion uses temporary archives only; no live data or Meta requests."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from src.server import create_app
from src.whatsapp import process_event


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = {
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": self.directory.name,
            "SECRET_KEY": "history-test-session-only",
            "STAFF_PASSWORD": "history-test-password",
        }
        self.app = create_app(self.config)
        self.store = self.app.extensions["store"]
        self.client = self.app.test_client()
        csrf = self.client.get("/api/session").json["csrf_token"]
        login = self.client.post("/api/login", json={"password": self.config["STAFF_PASSWORD"]},
                                 headers={"X-CSRF-Token": csrf})
        self.csrf = login.json["csrf_token"]

    def tearDown(self):
        self.directory.cleanup()

    def receive(self, message_id, sender="60123456789", kind="text"):
        message = {"id": message_id, "from": sender, "timestamp": str(int(time.time())), "type": kind}
        message[kind] = {"body": "Private text " + message_id} if kind == "text" else {
            "id": "media-" + message_id, "filename": message_id + ".pdf"}
        payload = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "123456789"},
            "messages": [message],
        }}]}]}
        process_event(self.store, payload)
        return payload, self.store.conversations()[0]["id"]

    def save_document(self, message_id, sender="60123456789"):
        _, conversation_id = self.receive(message_id, sender, "document")
        path = self.store.attachments / (message_id + ".pdf")
        path.write_bytes(b"%PDF-test-only")
        self.store.media_saved(self.store.message(message_id), path, "application/pdf", path.name)
        return conversation_id, path

    def delete(self, conversation_id=None, attachments=False):
        url = f"/api/conversations/{conversation_id}/history" if conversation_id else "/api/history"
        phrase = "DELETE" if conversation_id else "DELETE ALL"
        return self.client.delete(url, json={"confirmation": phrase, "delete_attachments": attachments},
                                  headers={"X-CSRF-Token": self.csrf})

    def test_requires_login_csrf_explicit_confirmation_and_boolean_choice(self):
        stranger = self.app.test_client()
        csrf = stranger.get("/api/session").json["csrf_token"]
        self.assertEqual(stranger.delete("/api/history", json={}, headers={"X-CSRF-Token": csrf}).status_code, 401)
        self.assertEqual(self.client.delete("/api/history", json={}).status_code, 403)
        for body in ({}, {"confirmation": "DELETE", "delete_attachments": True},
                     {"confirmation": "DELETE ALL", "delete_attachments": "false"}):
            result = self.client.delete("/api/history", json=body, headers={"X-CSRF-Token": self.csrf})
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.delete(999).status_code, 404)

    def test_single_chat_keeps_attachments_other_chats_and_configuration(self):
        target, kept_path = self.save_document("target-file")
        self.receive("target-text")
        other, other_path = self.save_document("other-file", "60987654321")
        self.receive("other-text", "60987654321")
        before = self.store.conversation(target)
        self.store.set_setting("custom_setting", "unchanged")
        response = self.delete(target)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["deleted_messages"], 2)
        self.assertTrue(response.json["attachments_kept"])
        self.assertTrue(kept_path.exists())
        self.assertTrue(other_path.exists())
        self.assertEqual(self.store.messages(target)["messages"], [])
        self.assertEqual(len(self.store.messages(other)["messages"]), 2)
        after = self.store.conversation(target)
        self.assertEqual(after["last_inbound_at"], before["last_inbound_at"])
        self.assertEqual(after["unread_count"], 0)
        self.assertEqual(after["last_message"], "")
        self.assertEqual(after["history_revision"], 1)
        self.assertEqual(self.store.setting("custom_setting"), "unchanged")
        archive = self.store.log_path.read_text()
        self.assertNotIn("target-text", archive)
        self.assertNotIn("target-file", archive)
        self.assertIn("other-text", archive)
        self.assertIn("other-file", archive)
        self.assertEqual(self.client.get("/api/media/target-file").status_code, 404)
        with self.client.get("/api/media/other-file") as media:
            self.assertEqual(media.status_code, 200)

    def test_single_chat_can_later_delete_previously_retained_files(self):
        conversation_id, path = self.save_document("retained")
        self.assertEqual(self.delete(conversation_id).status_code, 200)
        self.assertTrue(path.exists())
        result = self.delete(conversation_id, attachments=True)
        self.assertEqual(result.json["deleted_attachments"], 1)
        self.assertFalse(path.exists())

    def test_whole_inbox_cleanup_includes_retained_and_unindexed_files(self):
        first, kept = self.save_document("kept")
        self.delete(first)
        _, other = self.save_document("another", "60987654321")
        orphan = self.store.attachments / "legacy-orphan.pdf"
        orphan.write_bytes(b"old-file")
        result = self.delete(attachments=True)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["deleted_attachments"], 3)
        self.assertFalse(any(path.exists() for path in [kept, other, orphan]))
        self.assertEqual(self.store.log_path.read_text(), "")
        self.assertEqual(len(self.store.conversations()), 2)  # Contacts are kept.
        for conversation in self.store.conversations():
            self.assertEqual(self.store.messages(conversation["id"])["messages"], [])

    def test_whole_inbox_can_keep_files_and_does_not_reimport_after_restart(self):
        conversation_id, path = self.save_document("saved")
        result = self.delete()
        self.assertEqual(result.status_code, 200)
        self.assertTrue(path.exists())
        restarted = create_app(self.config).extensions["store"]
        self.assertEqual(restarted.messages(conversation_id)["messages"], [])
        self.assertEqual(restarted.log_path.read_text(), "")

    def test_webhook_redelivery_and_status_cannot_resurrect_deleted_history(self):
        payload, conversation_id = self.receive("no-resurrection")
        self.assertEqual(self.delete(conversation_id).status_code, 200)
        process_event(self.store, payload)
        self.store.delivery_status({"id": "no-resurrection", "status": "read"})
        self.assertIsNone(self.store.message("no-resurrection"))
        self.assertEqual(self.store.messages(conversation_id)["messages"], [])
        self.assertEqual(self.store.log_path.read_text(), "")
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM delivery").fetchone()[0], 0)
        self.receive("new-message")
        self.assertIsNotNone(self.store.message("new-message"))

    def test_deleted_pending_download_is_cancelled_and_late_completion_is_removed(self):
        _, conversation_id = self.receive("pending-file", kind="document")
        row = self.store.claim_media()
        self.assertIsNotNone(row)
        self.assertEqual(self.delete(conversation_id).status_code, 200)
        late_path = self.store.attachments / "late.pdf"
        late_path.write_bytes(b"late completion")
        self.store.media_saved(row, late_path, "application/pdf", "late.pdf")
        self.store.media_failed(row, "late failure")
        self.assertFalse(late_path.exists())
        self.assertIsNone(self.store.claim_media())
        self.assertEqual(self.store.log_path.read_text(), "")

    def test_busy_send_blocks_deletion_and_deleted_send_cannot_be_retried(self):
        _, conversation_id = self.receive("incoming")
        conversation = self.store.conversation(conversation_id)
        outgoing, _ = self.store.new_outgoing(conversation, "test reply", "history-client-request-001")
        self.assertEqual(self.delete(conversation_id).status_code, 409)
        self.assertIsNotNone(self.store.message("incoming"))
        self.store.finish_outgoing(outgoing["id"], platform_id="sent-platform-id")
        self.assertEqual(self.delete(conversation_id).status_code, 200)
        with self.assertRaisesRegex(ValueError, "history was deleted"):
            self.store.new_outgoing(conversation, "test reply", "history-client-request-001")

    def test_unsafe_path_does_not_delete_external_file_or_history(self):
        conversation_id, _ = self.save_document("unsafe")
        outside = Path(self.directory.name) / "do-not-delete.txt"
        outside.write_text("unrelated file")
        with self.store.db() as db:
            db.execute("UPDATE messages SET attachment_path=? WHERE id='unsafe'", (str(outside),))
        self.assertEqual(self.delete(conversation_id, attachments=True).status_code, 409)
        self.assertTrue(outside.exists())
        self.assertIsNotNone(self.store.message("unsafe"))

    def test_send_completion_returns_receipt_if_history_is_cleared_before_logging(self):
        _, conversation_id = self.receive("send-race")
        row, _ = self.store.new_outgoing(self.store.conversation(conversation_id),
                                         "reply", "clear-during-completion-001")
        original_log = self.store.log_message

        def clear_before_logging(completed):
            self.assertEqual(self.delete(conversation_id).status_code, 200)
            original_log(completed)

        with patch.object(self.store, "log_message", side_effect=clear_before_logging):
            receipt = self.store.finish_outgoing(row["id"], platform_id="completed-platform")
        self.assertEqual(self.store.public_message(receipt)["status"], "accepted")
        self.assertIsNone(self.store.message(row["id"]))
        self.assertEqual(self.store.log_path.read_text(), "")

    def test_other_employees_receive_new_revision_and_empty_history(self):
        _, conversation_id = self.receive("shared")
        colleague = self.app.test_client()
        csrf = colleague.get("/api/session").json["csrf_token"]
        colleague.post("/api/login", json={"password": self.config["STAFF_PASSWORD"]},
                       headers={"X-CSRF-Token": csrf})
        url = f"/api/conversations/{conversation_id}/messages"
        before = colleague.get(url).json
        self.delete(conversation_id)
        after = colleague.get(url).json
        self.assertEqual(after["history_revision"], before["history_revision"] + 1)
        self.assertEqual(after["messages"], [])
