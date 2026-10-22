"""Meta Cloud API calls, signed-event processing, and durable attachment jobs."""
from datetime import datetime
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse

import requests
from werkzeug.utils import secure_filename

MEDIA_TYPES = {"image", "document", "audio", "video", "sticker"}
MIME_MAP = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    "audio/aac": ".aac", "video/mp4": ".mp4", "video/3gpp": ".3gp",
    "application/pdf": ".pdf",
}


class GraphError(Exception):
    pass


class GraphClient:
    def __init__(self, token, version="v23.0"):
        self.token = token
        if not re.fullmatch(r"v\d+\.\d+", version):
            raise ValueError("Invalid META_GRAPH_VERSION.")
        self.base_url = f"https://graph.facebook.com/{version}"

    @property
    def headers(self):
        if not self.token:
            raise GraphError("META_ACCESS_TOKEN is not configured in Replit Secrets.")
        return {"Authorization": f"Bearer {self.token}"}

    def call(self, method, resource, payload=None):
        try:
            response = requests.request(method, f"{self.base_url}/{resource}",
                                        headers=self.headers, json=payload, timeout=(5, 25))
            data = response.json()
        except (requests.RequestException, ValueError):
            raise GraphError(
                "Could not confirm Meta's response. Check connectivity and the recipient before resending."
            ) from None
        if not isinstance(data, dict):
            raise GraphError("Meta returned an unexpected response; delivery is not confirmed.")
        if not response.ok or data.get("error"):
            error = data.get("error", {})
            if not isinstance(error, dict):
                error = {}
            detail = str(error.get("message") or "Meta rejected this request.")
            code = error.get("code")
            if code == 131047:
                detail = "The 24-hour reply window has closed. An approved WhatsApp template is required."
            if code == 190:
                detail = "Meta's access token is expired or invalid. Update META_ACCESS_TOKEN in Replit Secrets."
            if self.token:
                detail = detail.replace(self.token, "[redacted]")
            raise GraphError(f"{detail} (Meta code {code})" if code else detail)
        return data

    def send_text(self, phone_id, recipient, text):
        data = self.call("POST", f"{phone_id}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": recipient, "type": "text", "text": {"body": text, "preview_url": True},
        })
        try:
            return data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError):
            raise GraphError("Meta did not return a message ID; delivery is not confirmed.") from None

    @staticmethod
    def safe_media_url(url):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and any(host == domain or host.endswith("." + domain)
                        for domain in ("fbsbx.com", "fbcdn.net", "facebook.com", "whatsapp.net")))

    def download_media(self, row, store):
        media_id = row["media_id"]
        if not re.fullmatch(r"\d+", media_id):
            raise GraphError("The attachment has an invalid Meta media ID.")
        info = self.call("GET", media_id)
        url = info.get("url", "")
        mime = (info.get("mime_type") or row["mime_type"] or "application/octet-stream").split(";")[0]
        limit = 110 * 1024 * 1024  # Includes WhatsApp's 100 MB document maximum.
        if int(info.get("file_size") or 0) > limit:
            raise GraphError("Attachment exceeds WhatsApp's supported document size.")
        original = re.sub(r"[\x00-\x1f\x7f]", "",
                          (row["filename"] or "").replace("\\", "/").rsplit("/", 1)[-1])[:240]
        extension = Path(secure_filename(original)).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = MIME_MAP.get(mime) or mimetypes.guess_extension(mime) or ".bin"
        conversation = store.conversation(row["conversation_id"])
        sender = re.sub(r"\D", "", conversation["wa_id"]) or "contact"
        date = datetime.fromtimestamp(row["timestamp"]).strftime("%Y-%m-%d_%H-%M-%S")
        suffix = hashlib.sha256(row["id"].encode()).hexdigest()[:12]
        folder = store.attachments / row["type"]
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{sender}_{date}_{suffix}{extension}"
        partial = destination.with_suffix(destination.suffix + ".part")
        try:
            for redirect in range(4):
                if not self.safe_media_url(url):
                    raise GraphError("Meta returned an untrusted media download URL.")
                with requests.get(url, headers=self.headers, stream=True,
                                  timeout=(5, 60), allow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = response.headers.get("Location", "")
                        continue
                    if response.status_code != 200:
                        raise GraphError(f"Meta media download returned HTTP {response.status_code}.")
                    if int(response.headers.get("Content-Length") or 0) > limit:
                        raise GraphError("Attachment is too large.")
                    total = 0
                    started = time.monotonic()
                    with partial.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if time.monotonic() - started > 240:
                                raise GraphError("Media download took too long; it will be retried.")
                            total += len(chunk)
                            if total > limit:
                                raise GraphError("Attachment exceeded the safe download size.")
                            handle.write(chunk)
                    os.replace(partial, destination)
                    return destination, mime, original or destination.name
            raise GraphError("Meta media download redirected too many times.")
        except requests.RequestException:
            raise GraphError("Media download interrupted; it will be retried.") from None
        finally:
            partial.unlink(missing_ok=True)


def describe_message(message):
    kind = message.get("type", "unknown")
    content = message.get(kind, {})
    if kind == "text":
        return str(content.get("body", ""))
    if kind == "location":
        return (content.get("name") or content.get("address") or
                f"Location: {content.get('latitude', '?')}, {content.get('longitude', '?')}")
    if kind == "contacts":
        return "Contact shared: " + ", ".join(
            c.get("name", {}).get("formatted_name", "Unnamed contact") for c in content)
    if kind == "reaction":
        return f"Reaction: {content.get('emoji') or 'removed'}"
    if kind == "interactive":
        selected = content.get("button_reply") or content.get("list_reply") or content.get("nfm_reply") or {}
        return selected.get("title") or selected.get("body") or "Interactive reply"
    if kind == "button":
        return content.get("text") or content.get("payload") or "Button reply"
    if kind == "system":
        return content.get("body") or "System notification"
    if kind in MEDIA_TYPES:
        return ""
    return f"[{kind}]"


def process_event(store, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("entry"), list):
        raise ValueError("Expected a WhatsApp webhook object with an entry array.")
    for entry in payload["entry"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("changes"), list):
            raise ValueError("Invalid webhook entry.")
        for change in entry["changes"]:
            value = change.get("value", {})
            if not isinstance(value, dict):
                raise ValueError("Invalid webhook change.")
            metadata = value.get("metadata") or {}
            phone_id = str(metadata.get("phone_number_id") or "")
            messages = value.get("messages", [])
            statuses = value.get("statuses", [])
            if not isinstance(messages, list) or not isinstance(statuses, list):
                raise ValueError("Invalid webhook message/status arrays.")
            if messages and not re.fullmatch(r"\d+", phone_id):
                raise ValueError("Incoming messages require a business phone_number_id.")
            if phone_id:
                store.learn_phone(phone_id, str(metadata.get("display_phone_number") or ""))
            contacts = {c.get("wa_id"): c.get("profile", {}).get("name", "")
                        for c in value.get("contacts", []) if isinstance(c, dict)}
            for message in messages:
                if not isinstance(message, dict) or not message.get("id") or not message.get("from"):
                    raise ValueError("Incoming message is missing its ID or sender.")
                kind = str(message.get("type") or "unknown")
                media = message.get(kind) if kind in MEDIA_TYPES else {}
                media = media if isinstance(media, dict) else {}
                if kind in MEDIA_TYPES and not media.get("id"):
                    raise ValueError("Media message is missing its media ID.")
                sender = str(message["from"])
                if not re.fullmatch(r"\d{5,20}", sender):
                    raise ValueError("Invalid sender ID.")
                timestamp = int(message.get("timestamp") or time.time())
                if timestamp < 0 or timestamp > int(time.time()) + 300:
                    raise ValueError("Invalid message timestamp.")
                conversation = store.get_or_create_conversation(phone_id, sender, contacts.get(sender, ""))
                store.insert_inbound(conversation["id"], {
                    "id": str(message["id"]), "type": kind, "body": describe_message(message),
                    "caption": str(media.get("caption") or ""), "timestamp": timestamp,
                    "filename": str(media.get("filename") or ""),
                    "mime_type": str(media.get("mime_type") or ""),
                    "media_id": str(media["id"]) if media.get("id") else None,
                    "raw": message, "context_id": (message.get("context") or {}).get("id"),
                })
            for status in statuses:
                if isinstance(status, dict):
                    store.delivery_status(status)
    store.set_setting("last_event_at", int(time.time()))


class MediaWorker:
    def __init__(self, store, graph):
        self.store = store
        self.graph = graph
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="whatsapp-media", daemon=True)

    def process_one(self):
        row = self.store.claim_media()
        if row is None:
            return False
        try:
            path, mime, filename = self.graph.download_media(row, self.store)
            self.store.media_saved(row, path, mime, filename)
        except Exception as error:
            # Never log credentials or expiring Meta media URLs.
            detail = str(error) if isinstance(error, GraphError) else "Attachment could not be saved."
            self.store.media_failed(row, detail)
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                active = self.process_one()
            except Exception:
                logging.error("The attachment queue is unavailable. Pending jobs will be retried.")
                self.stop_event.wait(5)
                active = False  # Durable pending jobs remain in SQLite for the next pass.
            if not active:
                self.stop_event.wait(1)

    def start(self):
        self.thread.start()
