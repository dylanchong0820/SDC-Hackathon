"""Fail-closed factory for a single-process, always-running production service."""
import os

from .server import create_app


def create_production_app():
    required = ("DATABASE_URL", "SESSION_SECRET", "STAFF_PASSWORD", "META_APP_SECRET",
                "META_ACCESS_TOKEN", "WEBHOOK_VERIFY_TOKEN", "RUNPOD_API_KEY")
    if any(not os.getenv(key) for key in required):
        raise RuntimeError("Production credentials are incomplete. Check publishing secrets.")
    if len(os.environ["STAFF_PASSWORD"]) < 12:
        raise RuntimeError("The production staff password must contain at least 12 characters.")
    from replit.object_storage import Client
    objects = Client()
    # Do not silently publish a local-only fallback if bucket access is missing.
    objects.list(prefix="quote-bot/readiness/", max_results=1)
    return create_app({
        "PRODUCTION": True, "STORAGE_BACKEND": "postgres",
        "OBJECT_STORAGE_CLIENT": objects,
    })
