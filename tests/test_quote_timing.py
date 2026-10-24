"""Stage timing and terminal-state regressions using isolated archives."""
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.quote_analysis import QuoteAnalysisError, QuoteAnalysisWorker
from src.quote_timing import advance_timings
from src.storage import Store


class QuoteTimingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        contact = self.store.get_or_create_conversation("test-phone", "test-contact", "Synthetic")
        self.store.insert_inbound(contact["id"], {
            "id": "synthetic-source", "type": "text", "body": "MYR 20",
            "timestamp": 1, "raw": {}, "caption": "", "filename": "",
            "mime_type": "", "media_id": None, "context_id": None,
        })
        self.now = 1000
        self.clock = patch("src.quote_timing.time", SimpleNamespace(time=lambda: self.now))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.job = self.store.create_quote_evaluation(["synthetic-source"], "price")

    def saved(self):
        return self.store.quote_evaluation(self.job["id"])

    def test_stage_transitions_are_cumulative_and_terminal_values_freeze(self):
        self.now += 7
        self.store.update_quote_evaluation(self.job["id"], "processing")
        self.now += 13
        partial = {"partial": True, "quotes": [{"vendor": "Synthetic vendor"}],
                   "progress": {"stage": "connecting"}}
        self.store.update_quote_evaluation(self.job["id"], "processing", result=partial)
        self.now += 19
        partial["progress"]["stage"] = "analyzing"
        self.store.update_quote_evaluation(self.job["id"], "processing", result=partial)
        self.now += 23
        self.store.update_quote_evaluation(self.job["id"], "processing", result=partial)
        self.now += 2
        with self.assertLogs(level="INFO") as logs:
            saved = self.store.cancel_quote_evaluation(self.job["id"])
        self.assertEqual(saved["result"]["timings"], {
            "queue": {"seconds": 7}, "reading": {"seconds": 13},
            "connecting": {"seconds": 19}, "analyzing": {"seconds": 25}})
        self.assertEqual(saved["result"]["quotes"], partial["quotes"])
        self.assertNotIn("Synthetic", " ".join(logs.output))
        self.assertNotIn("synthetic-source", " ".join(logs.output))
        self.now += 100
        self.store.update_quote_evaluation(self.job["id"], "completed", result={"quotes": []})
        self.assertEqual(self.saved(), saved)

    def test_queued_cancellation_and_failure_freeze_without_fabricating_stages(self):
        self.now += 8
        saved = self.store.cancel_quote_evaluation(self.job["id"])
        self.assertEqual(saved["result"]["timings"], {"queue": {"seconds": 8}})
        self.assertEqual(saved["status"], "cancelled")

    def test_restart_closes_active_timer_and_preserves_partial_facts(self):
        self.store.update_quote_evaluation(self.job["id"], "processing", result={
            "partial": True, "quotes": [{"vendor": "Synthetic"}],
            "progress": {"stage": "analyzing"}})
        self.now += 5
        restarted = Store(self.directory.name).quote_evaluation(self.job["id"])
        self.assertEqual(restarted["status"], "failed")
        self.assertEqual(restarted["result"]["timings"]["analyzing"], {"seconds": 5})
        self.assertEqual(restarted["result"]["quotes"], [{"vendor": "Synthetic"}])

    def test_worker_publishes_before_reading_and_discovery_and_freezes_on_failure(self):
        for stage, method in (("reading", "_prepare_message"), ("connecting", "_get_model_id")):
            with self.subTest(stage=stage):
                job = self.store.create_quote_evaluation(["synthetic-source"], "price")
                worker = QuoteAnalysisWorker(self.store, "https://example.api.runpod.ai", "fake")
                entered, release = threading.Event(), threading.Event()

                def delayed(*args, **kwargs):
                    entered.set()
                    release.wait(5)
                    raise QuoteAnalysisError("Synthetic failure.")

                with patch.object(worker, method, side_effect=delayed):
                    worker.submit(job["id"])
                    try:
                        self.assertTrue(entered.wait(3))
                        current = self.store.quote_evaluation(job["id"])
                        self.assertEqual(current["result"]["progress"]["stage"], stage)
                        self.assertIn("started_at", current["result"]["timings"][stage])
                        self.now += 12
                    finally:
                        release.set()
                        worker.jobs.join()
                saved = self.store.quote_evaluation(job["id"])
                self.assertEqual(saved["status"], "failed")
                self.assertEqual(saved["result"]["timings"][stage], {"seconds": 12})
                self.assertTrue(saved["result"]["partial"])

    def test_cancel_during_reading_or_discovery_cannot_reopen_timer(self):
        for stage, method in (("reading", "_prepare_message"), ("connecting", "_get_model_id")):
            with self.subTest(stage=stage):
                job = self.store.create_quote_evaluation(["synthetic-source"], "price")
                worker = QuoteAnalysisWorker(self.store, "https://example.api.runpod.ai", "fake")
                original = getattr(worker, method)

                def cancel(*args, **kwargs):
                    self.now += 4
                    self.store.cancel_quote_evaluation(job["id"])
                    self.now += 20
                    return original(*args, **kwargs) if stage == "reading" else "fake-model"

                with patch.object(worker, method, side_effect=cancel), \
                        patch.object(worker, "_request_model") as model:
                    worker._analyze(job["id"])
                model.assert_not_called()
                saved = self.store.quote_evaluation(job["id"])
                self.assertEqual(saved["status"], "cancelled")
                self.assertEqual(saved["result"]["timings"][stage], {"seconds": 4})

    def test_completed_worker_records_all_stages(self):
        worker = QuoteAnalysisWorker(self.store, "https://example.api.runpod.ai", "fake")

        def model(*args):
            self.now += 30
            return {"quotes": [{"quote_key": "QUOTE_1", "currency": "MYR", "total_price": 20}]}

        with patch.object(worker, "_get_model_id", return_value="fake-model"), \
                patch.object(worker, "_request_model", side_effect=model):
            worker._analyze(self.job["id"])
        saved = self.saved()
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["result"]["timings"]["analyzing"], {"seconds": 30})
        self.assertEqual(set(saved["result"]["timings"]),
                         {"queue", "reading", "connecting", "analyzing", "ranking"})
        self.assertNotIn("started_at", json.dumps(saved["result"]["timings"]))

    def test_backward_clock_change_never_produces_negative_duration(self):
        self.assertEqual(advance_timings({"queue": {"seconds": 0, "started_at": 10}},
                                         None, now=5), {"queue": {"seconds": 0}})


if __name__ == "__main__":
    unittest.main()
