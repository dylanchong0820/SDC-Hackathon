"""Real PostgreSQL integration tests in disposable schemas; no customer data."""
import io
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from src.cloud_storage import CloudStore, object_key
from src.server import create_app
from src.storage import StorageBusyError


class Objects:
    def __init__(self):
        self.files = {}
        self.fail_delete = False

    def upload_from_filename(self, key, filename):
        self.files[key] = Path(filename).read_bytes()

    def download_to_filename(self, key, filename):
        Path(filename).write_bytes(self.files[key])

    def delete(self, key, ignore_not_found=False):
        if self.fail_delete:
            raise OSError("Synthetic storage outage")
        self.files.pop(key, None)

    def copy(self, source, destination):
        self.files[destination] = self.files[source]


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1" and os.getenv("DATABASE_URL"),
                     "Set RUN_POSTGRES_TESTS=1 for isolated PostgreSQL tests.")
class CloudStorageTests(unittest.TestCase):
    def setUp(self):
        if os.getenv("REPLIT_DEPLOYMENT") == "1":
            self.skipTest("Never create test schemas in a deployment.")
        self.schema = "inbox_test_" + uuid.uuid4().hex
        self.base_url = os.environ["DATABASE_URL"]
        with psycopg.connect(self.base_url) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            db.execute(Path("schema/postgres.sql").read_text())
        self.url = make_conninfo(self.base_url, options=f"-c search_path={self.schema}")
        self.directory = tempfile.TemporaryDirectory()
        self.objects = Objects()
        self.store = self.new_store()
        self.store.learn_phone("123456")
        self.chat = self.store.get_or_create_conversation("123456", "60111111111", "Synthetic Client")

    def tearDown(self):
        self.directory.cleanup()
        with psycopg.connect(self.base_url) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def new_store(self):
        return CloudStore(self.directory.name, self.url, self.objects, recover_jobs=False)

    def receive(self, message_id="synthetic-message", media=False):
        record = {
            "id": message_id, "type": "image" if media else "text",
            "body": "" if media else "Quoted total MYR 100; delivery in 4 days.",
            "caption": "", "timestamp": int(time.time()), "filename": "",
            "mime_type": "image/png" if media else "", "media_id": "12345" if media else None,
            "raw": {"synthetic": True}, "context_id": None,
        }
        return self.store.insert_inbound(self.chat["id"], record)

    def save_media(self):
        self.receive(media=True)
        path = self.store.attachments / "synthetic.png"
        path.write_bytes(b"synthetic-image-bytes")
        self.store.media_saved(self.store.message("synthetic-message"), path, "image/png", path.name)
        return self.store.message("synthetic-message")

    def test_messages_archive_settings_and_search_survive_fresh_cache(self):
        self.assertTrue(self.receive())
        self.assertFalse(self.receive())
        self.store.set_setting("synthetic", "kept")
        shutil.rmtree(self.store.root)
        restored = self.new_store()
        self.assertEqual(restored.setting("synthetic"), "kept")
        self.assertIn("Quoted total", restored.archive_text())
        self.assertEqual(len(restored.search_messages("quoted")), 1)
        self.assertEqual(restored.conversation(self.chat["id"])["unread_count"], 1)
        self.assertIn("Quoted total", restored.log_path.read_text())

    def test_media_rehydrates_and_keep_then_delete_has_durable_tombstone(self):
        row = self.save_media()
        self.assertTrue(row["attachment_path"].startswith("object://"))
        self.assertEqual(self.store.media_path(row).read_bytes(), b"synthetic-image-bytes")
        shutil.rmtree(self.store.root)
        restored = self.new_store()
        self.assertEqual(restored.media_path(restored.message(row["id"])).read_bytes(),
                         b"synthetic-image-bytes")
        result = restored.clear_history(self.chat["id"], False)
        self.assertTrue(result["attachments_kept"])
        self.assertEqual(len(self.objects.files), 1)
        self.assertEqual(restored.archive_text(), "")
        result = restored.clear_history(self.chat["id"], True)
        self.assertEqual(result["deleted_attachments"], 1)
        self.assertEqual(len(self.objects.files), 0)
        self.assertFalse(self.receive())

    def test_deletion_outage_is_reported_and_retried_after_restart(self):
        self.save_media()
        self.objects.fail_delete = True
        result = self.store.clear_history(None, True)
        self.assertEqual(result["attachment_deletions_pending"], 1)
        self.assertEqual(result["deleted_attachments"], 0)
        self.assertIsNone(self.store.message("synthetic-message"))
        self.objects.fail_delete = False
        restored = self.new_store()
        restored.flush_deletions()
        self.assertEqual(len(self.objects.files), 0)
        with restored.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM object_deletions").fetchone()[0], 0)

    def test_history_import_does_not_open_window_and_delete_prevents_reimport(self):
        records = [{"timestamp": int(time.time()), "direction": "inbound",
                    "body": "Synthetic imported quote", "author": "Client"}]
        result = self.store.import_history("123456", "60111111111", "Synthetic Client", records)
        self.assertEqual(result["messages_imported"], 1)
        self.assertFalse(self.store.conversation(self.chat["id"])["window_open"])
        self.assertEqual(self.store.import_history(
            "123456", "60111111111", "", records)["messages_imported"], 0)
        self.store.clear_history(self.chat["id"])
        self.assertEqual(self.store.import_history(
            "123456", "60111111111", "", records)["messages_imported"], 0)

    def test_comparison_sources_and_delete_work_with_postgres_identity(self):
        self.receive()
        result = self.store.create_quote_evaluation(["synthetic-message"], "price")
        evaluation_id = result["id"]
        self.assertIsInstance(evaluation_id, int)
        self.store.update_quote_evaluation(
            evaluation_id, "processing", {"partial": True, "quotes": []})
        self.assertEqual(self.store.quote_evaluation(evaluation_id)["status"], "processing")
        self.store.cancel_quote_evaluation(evaluation_id)
        self.assertEqual(self.store.quote_evaluation(evaluation_id)["status"], "cancelled")
        self.store.clear_history(self.chat["id"])
        self.assertIsNone(self.store.quote_evaluation(evaluation_id))

    def test_archive_is_staff_only_and_api_login_and_media_work(self):
        row = self.save_media()
        app = create_app({
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": self.directory.name,
            "STORAGE_BACKEND": "postgres", "DATABASE_URL": self.url,
            "OBJECT_STORAGE_CLIENT": self.objects, "SECRET_KEY": "synthetic-session",
            "STAFF_PASSWORD": "synthetic-staff-password",
        })
        client = app.test_client()
        self.assertEqual(client.get("/api/archive/messages.txt").status_code, 401)
        csrf = client.get("/api/session").json["csrf_token"]
        response = client.post("/api/login", json={"password": "synthetic-staff-password"},
                               headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/conversations").status_code, 200)
        self.assertEqual(client.get("/api/archive/messages.txt").status_code, 200)
        response = client.get("/api/media/" + row["id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"synthetic-image-bytes")
        response.close()

    def test_outgoing_idempotency_and_delivery_order_remain_atomic(self):
        conversation = self.store.conversation(self.chat["id"])
        row, created = self.store.new_outgoing(conversation, "Synthetic reply", "request-test")
        self.assertTrue(created)
        self.store.delivery_status({"id": "wamid.synthetic", "status": "read",
                                    "timestamp": str(int(time.time()))})
        saved = self.store.finish_outgoing(row["id"], "wamid.synthetic")
        self.assertEqual(saved["status"], "read")
        again, created = self.store.new_outgoing(conversation, "Synthetic reply", "request-test")
        self.assertFalse(created)
        self.assertEqual(again["id"], row["id"])
        self.assertIn("Synthetic reply", self.store.archive_text())

    def test_rejects_unsafe_paths_and_production_sqlite(self):
        for value in ("object://other/file", "object://quote-bot/attachments/../secret",
                      "/tmp/file", "object://quote-bot/attachments/folder/file"):
            with self.assertRaises(ValueError):
                object_key(value)
        with self.assertRaises(RuntimeError):
            create_app({"PRODUCTION": True, "STORAGE_BACKEND": "sqlite"})

    def test_copied_development_metadata_cannot_delete_development_objects(self):
        original = self.save_media()
        source_key = object_key(original["attachment_path"])
        published = CloudStore(self.directory.name, self.url, self.objects,
                               recover_jobs=False, namespace="production")
        copied = published.message(original["id"])
        destination_key = object_key(copied["attachment_path"])
        self.assertNotEqual(source_key, destination_key)
        self.assertIn("/production/", destination_key)
        self.assertEqual(self.objects.files[source_key], self.objects.files[destination_key])
        published.clear_history(None, True)
        self.assertNotIn(destination_key, self.objects.files)
        self.assertIn(source_key, self.objects.files)

    def test_quote_attachment_download_does_not_hold_database_transaction(self):
        self.save_media()
        original_download = self.objects.download_to_filename
        def download(key, filename):
            self.assertIsNone(getattr(self.store._transactions, "connection", None))
            original_download(key, filename)
        self.objects.download_to_filename = download
        result = self.store.create_quote_evaluation(["synthetic-message"], "price")
        self.assertEqual(result["status"], "queued")

    def test_concurrent_archive_reads_and_receives_do_not_invert_locks(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for index in range(6):
                futures.append(executor.submit(self.receive, f"synthetic-{index}"))
                futures.append(executor.submit(self.store.refresh_log_cache))
            for future in futures:
                future.result(timeout=10)
        self.assertEqual(len(self.store.search_messages("Quoted total")), 6)


if __name__ == "__main__":
    unittest.main()
