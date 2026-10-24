"""One threaded worker: comparison jobs run locally; media leases live in PostgreSQL."""
import os

bind = "0.0.0.0:" + os.getenv("PORT", "5000")
workers = 1
worker_class = "gthread"
threads = 8
timeout = 180
graceful_timeout = 30
accesslog = None  # Never log customer identifiers or webhook payloads.
errorlog = "-"
capture_output = True
preload_app = False  # Start background workers after fork, not in the master.
