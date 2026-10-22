"""Prevent parallel staff/media access from overlapping SQLite lifecycles."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src import storage
from src.server import create_app


class StorageConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "MEDIA_WORKER": False, "DATA_DIR": self.directory.name,
            "SECRET_KEY": "concurrency-test-session",
            "STAFF_PASSWORD": "concurrency-test-password",
            "META_ACCESS_TOKEN": "",
        })
        self.store = self.app.extensions["store"]

    def tearDown(self):
        self.directory.cleanup()

    def test_connection_lifecycles_do_not_overlap_between_threads(self):
        original_connect = sqlite3.connect
        counter_lock = threading.Lock()
        active = 0
        maximum = 0

        class TrackedConnection(sqlite3.Connection):
            def __init__(connection, *args, **kwargs):
                nonlocal active, maximum
                super().__init__(*args, **kwargs)
                with counter_lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.001)  # Give other staff/media threads time to enter.

            def close(connection):
                nonlocal active
                try:
                    super().close()
                finally:
                    with counter_lock:
                        active -= 1

        def tracked_connect(*args, **kwargs):
            return original_connect(*args, factory=TrackedConnection, **kwargs)

        def employee(number):
            for attempt in range(15):
                self.store.set_setting(f"employee-{number}", attempt)
                self.assertEqual(self.store.setting(f"employee-{number}"), str(attempt))

        with patch.object(storage.sqlite3, "connect", side_effect=tracked_connect):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(employee, range(8)))
        self.assertEqual(maximum, 1)
        self.assertEqual(active, 0)

    def test_external_write_lock_returns_retryable_api_error(self):
        client = self.app.test_client()
        csrf = client.get("/api/session").json["csrf_token"]
        external = sqlite3.connect(self.store.db_path)
        external.execute("BEGIN IMMEDIATE")
        try:
            with patch.object(storage, "SQLITE_WAIT_SECONDS", 0.02):
                response = client.post(
                    "/api/login", json={"password": "concurrency-test-password"},
                    headers={"X-CSRF-Token": csrf})
            self.assertEqual(response.status_code, 503)
            self.assertIn("busy", response.json["error"])
            self.assertEqual(response.headers["Retry-After"], "5")
        finally:
            external.rollback()
            external.close()
        # A temporarily busy database must not permanently block later access.
        response = client.post(
            "/api/login", json={"password": "concurrency-test-password"},
            headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/conversations").status_code, 200)

    def test_connection_queue_wait_is_bounded(self):
        acquired = threading.Event()
        release = threading.Event()

        def holder():
            with storage._SQLITE_LOCK:
                acquired.set()
                release.wait(3)

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(acquired.wait(2))
        try:
            with patch.object(storage, "SQLITE_WAIT_SECONDS", 0.02):
                with self.assertRaises(storage.StorageBusyError):
                    self.store.setting("phone_number_id")
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(self.store.setting("unused", "available"), "available")
