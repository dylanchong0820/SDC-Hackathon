"""Durable inbox metadata, private object storage, and disposable local caches."""
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
import threading
import time
import uuid

if __package__:
    from .postgres import transaction
    from .quote_timing import advance_timings
    from .storage import Store, StorageBusyError
else:
    from postgres import transaction
    from quote_timing import advance_timings
    from storage import Store, StorageBusyError

OBJECT_PREFIX = "quote-bot/attachments/"
URI_PREFIX = "object://"


def object_key(value):
    if not isinstance(value, str) or not value.startswith(URI_PREFIX):
        raise ValueError("Attachment does not have a durable storage reference.")
    key = value[len(URI_PREFIX):]
    parts = PurePosixPath(key).parts
    if (not key.startswith(OBJECT_PREFIX) or ".." in parts or "\\" in key
            or len(parts) not in (3, 4)
            or (len(parts) == 4 and parts[2] not in {"development", "production"})):
        raise ValueError("Attachment has an unsafe durable storage reference.")
    return key


class CloudStore(Store):
    """Store API with PostgreSQL authority and private App Storage attachments.

    The readable archive is stored verbatim in archive_entries, not merely in
    local messages.txt. Both that file and downloaded media are rebuildable caches.
    """

    def __init__(self, data_dir, database_url, object_client=None, recover_jobs=True,
                 namespace="development"):
        if namespace not in {"development", "production"}:
            raise ValueError("Invalid storage environment.")
        self.namespace = namespace
        self.object_prefix = OBJECT_PREFIX + namespace + "/"
        self.root = (Path(data_dir) / "cloud-cache" / namespace).resolve()
        self.attachments = self.root / "attachments"
        self.attachments.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "messages.txt"
        self.log_lock = threading.RLock()
        self._media_lock = threading.Lock()
        self._transactions = threading.local()
        self.database_url = database_url
        if not database_url:
            raise RuntimeError("Durable inbox storage requires DATABASE_URL.")
        if object_client is None:
            from replit.object_storage import Client
            object_client = Client()
        self.objects = object_client
        # SELECT only: schema changes belong to development setup and Publish.
        with self.db() as db:
            db.execute("SELECT key FROM settings LIMIT 1")
            db.execute("SELECT id FROM archive_entries LIMIT 1")
            if recover_jobs:
                for row in db.execute(
                    "SELECT id,result_json FROM quote_evaluations "
                    "WHERE status IN ('queued','processing')"
                ):
                    result = json.loads(row["result_json"] or "{}")
                    if result.get("timings"):
                        result["timings"] = advance_timings(result["timings"], None)
                    db.execute(
                        "UPDATE quote_evaluations SET status='failed',error=?,"
                        "updated_at=?,result_json=? WHERE id=?",
                        ("The server restarted before analysis finished.", int(time.time()),
                         json.dumps(result, ensure_ascii=False), row["id"]))
        self.adopt_copied_objects()
        self.refresh_log_cache()

    def owns(self, key):
        return key.startswith(self.object_prefix) or (
            self.namespace == "development" and len(PurePosixPath(key).parts) == 3)

    def adopt_copied_objects(self):
        """Copy attachments when Publish copies dev metadata into production.

        Never let the two database environments delete each other's objects.
        Source copies remain owned by their original environment.
        """
        with self.db() as db:
            foreign = [row["object_key"] for row in db.execute(
                "SELECT object_key FROM durable_objects") if not self.owns(row["object_key"])]
        for source in foreign:
            object_key(URI_PREFIX + source)
            destination = (self.object_prefix + hashlib.sha256(source.encode()).hexdigest()
                           + PurePosixPath(source).suffix)
            try:
                self.objects.copy(source, destination)
            except Exception:
                raise StorageBusyError(
                    "Could not copy imported attachments into this environment's private storage."
                ) from None
            with self.db() as db:
                db.execute("INSERT OR IGNORE INTO durable_objects VALUES (?)", (destination,))
                db.execute("UPDATE messages SET attachment_path=? WHERE attachment_path=?",
                           (URI_PREFIX + destination, URI_PREFIX + source))
                db.execute("UPDATE retained_attachments SET path=? WHERE path=?",
                           (URI_PREFIX + destination, URI_PREFIX + source))
                db.execute("DELETE FROM durable_objects WHERE object_key=?", (source,))
        with self.db() as db:
            # A copied cleanup queue belongs to the source database. It must
            # never delete the source environment's attachment objects here.
            foreign_deletions = [row["object_key"] for row in db.execute(
                "SELECT object_key FROM object_deletions") if not self.owns(row["object_key"])]
            for key in foreign_deletions:
                db.execute("DELETE FROM object_deletions WHERE object_key=?", (key,))

    @contextmanager
    def db(self):
        current = getattr(self._transactions, "connection", None)
        if current is not None:
            yield current
            return
        with transaction(self.database_url) as connection:
            self._transactions.connection = connection
            try:
                yield connection
            finally:
                self._transactions.connection = None

    def insert_inbound(self, conversation_id, record):
        # Message, unread state and readable archive commit together.
        with self.log_lock, self.db():
            return super().insert_inbound(conversation_id, record)

    def finish_outgoing(self, message_id, platform_id=None, error=None):
        with self.log_lock, self.db():
            return super().finish_outgoing(message_id, platform_id, error)

    def create_quote_evaluation(self, message_ids, priority, reviewed_digests=None, retry_of=None):
        # Base Store validates the sources again inside its atomic transaction.
        # Rehydrate beforehand so an object download cannot hold the DB lock.
        for row in self.quote_preview_inputs(message_ids):
            if row["attachment_path"]:
                self.media_path(row)
        return super().create_quote_evaluation(
            message_ids, priority, reviewed_digests, retry_of)

    def archive_text(self):
        with self.db() as db:
            return "".join(row["entry"] for row in db.execute(
                "SELECT entry FROM archive_entries ORDER BY id"))

    def refresh_log_cache(self):
        with self.log_lock:
            self.log_path.write_text(self.archive_text(), encoding="utf-8")

    def log_message(self, row):
        if not row:
            return
        with self.log_lock, self.db() as db:
            current = db.execute("SELECT * FROM messages WHERE id=?", (row["id"],)).fetchone()
            if not current:
                return
            conversation = db.execute(
                "SELECT * FROM conversations WHERE id=?", (current["conversation_id"],)).fetchone()
            db.execute("INSERT INTO archive_entries(conversation_id,entry) VALUES (?,?)",
                       (current["conversation_id"], self.format_log_message(current, conversation)))
        self.refresh_log_cache()

    def import_history(self, phone_number_id, wa_id, display_name, records):
        # Reuse the reviewed importer, but journal its archive lines in the same
        # database transaction as the messages (not an ephemeral file).
        from collections import Counter
        from hashlib import sha256
        with self.db() as db:
            configured = db.execute(
                "SELECT value FROM settings WHERE key='phone_number_id'").fetchone()
            if not configured or configured["value"] != phone_number_id:
                raise ValueError("The selected business number is no longer configured.")
            db.execute(
                "INSERT OR IGNORE INTO conversations(phone_number_id,wa_id,name) VALUES (?,?,?)",
                (phone_number_id, wa_id, display_name or wa_id))
            if display_name:
                db.execute("UPDATE conversations SET name=? WHERE phone_number_id=? AND wa_id=? "
                           "AND (name='' OR name=wa_id)", (display_name, phone_number_id, wa_id))
            conversation = db.execute(
                "SELECT * FROM conversations WHERE phone_number_id=? AND wa_id=?",
                (phone_number_id, wa_id)).fetchone()
            counts, inserted = Counter(), []
            for record in records:
                identity = (f"{phone_number_id}\0{wa_id}\0{record['timestamp']}\0"
                            f"{record['direction']}\0{record['body']}")
                occurrence = counts[identity]
                counts[identity] += 1
                message_id = "history_" + sha256(
                    f"{identity}\0{occurrence}".encode()).hexdigest()
                if db.execute("SELECT 1 FROM deleted_messages WHERE id=?", (message_id,)).fetchone():
                    continue
                result = db.execute(
                    "INSERT OR IGNORE INTO messages "
                    "(id,conversation_id,direction,type,body,timestamp,status,attachment_state,raw) "
                    "VALUES (?,?,?,'text',?,?,'history','none',?)",
                    (message_id, conversation["id"], record["direction"], record["body"],
                     record["timestamp"], json.dumps({
                         "source": "whatsapp_text_export", "sender": record.get("author", "")
                     }, ensure_ascii=False)))
                if result.rowcount:
                    row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
                    inserted.append(row)
                    db.execute("INSERT INTO archive_entries(conversation_id,entry) VALUES (?,?)",
                               (conversation["id"], self.format_log_message(row, conversation)))
            if inserted:
                latest = max(inserted, key=lambda row: (row["timestamp"], row["id"]))
                db.execute(
                    "UPDATE conversations SET last_message=?,last_message_at=? "
                    "WHERE id=? AND last_message_at<=?",
                    (latest["body"][:500], latest["timestamp"], conversation["id"], latest["timestamp"]))
        self.refresh_log_cache()
        return {"conversation_id": conversation["id"], "messages_imported": len(inserted),
                "duplicates_skipped": len(records) - len(inserted)}

    def upload_attachment(self, path):
        path = Path(path)
        suffix = path.suffix.lower()
        if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
            suffix = ".bin"
        key = self.object_prefix + uuid.uuid4().hex + suffix
        # Register before upload so whole-inbox cleanup can also find orphaned
        # uploads if the worker stops between network upload and message update.
        with self.db() as db:
            db.execute("INSERT INTO durable_objects VALUES (?)", (key,))
        try:
            self.objects.upload_from_filename(key, str(path))
        except Exception:
            with self.db() as db:
                db.execute("INSERT OR IGNORE INTO object_deletions VALUES (?)", (key,))
            raise StorageBusyError("Attachment upload failed; the download will be retried.") from None
        return URI_PREFIX + key

    def media_saved(self, row, path, mime, filename):
        uri = self.upload_attachment(path)
        key = object_key(uri)
        with self.log_lock, self.db() as db:
            result = db.execute(
                "UPDATE messages SET attachment_path=?,mime_type=?,filename=?,"
                "attachment_state='saved',attachment_error=NULL WHERE id=?",
                (uri, mime, filename, row["id"]))
            if result.rowcount:
                db.execute("INSERT INTO archive_entries(conversation_id,entry) VALUES (?,?)",
                           (row["conversation_id"],
                            f"[ATTACHMENT] Message: {row['id']} | Saved: {uri}\n"))
            else:
                db.execute("INSERT OR IGNORE INTO object_deletions VALUES (?)", (key,))
        Path(path).unlink(missing_ok=True)
        self.flush_deletions()
        self.refresh_log_cache()

    def media_path(self, row):
        if not row or not row["attachment_path"]:
            return None
        key = object_key(row["attachment_path"])
        path = self.attachments / PurePosixPath(key).name
        if path.is_file():
            return path
        with self._media_lock:
            if not path.is_file():
                fd, temporary = tempfile.mkstemp(dir=self.attachments, prefix=".download-")
                os.close(fd)
                try:
                    self.objects.download_to_filename(key, temporary)
                    os.replace(temporary, path)
                except Exception:
                    raise StorageBusyError("Saved attachment storage is unavailable. Please retry.") from None
                finally:
                    Path(temporary).unlink(missing_ok=True)
        return path

    def flush_deletions(self):
        with self.db() as db:
            keys = [row["object_key"] for row in db.execute(
                "SELECT object_key FROM object_deletions LIMIT 20")]
        for key in keys:
            object_key(URI_PREFIX + key)  # Never delete outside the app's prefix.
            if not self.owns(key):
                raise StorageBusyError("Attachment cleanup belongs to a different storage environment.")
            try:
                self.objects.delete(key, ignore_not_found=True)
            except Exception:
                # This is a durable cleanup queue, not a claim of successful
                # deletion. The API reports the outstanding count to staff.
                continue
            with self.db() as db:
                db.execute("DELETE FROM object_deletions WHERE object_key=?", (key,))
                db.execute("DELETE FROM durable_objects WHERE object_key=?", (key,))
            (self.attachments / PurePosixPath(key).name).unlink(missing_ok=True)

    def clear_history(self, conversation_id=None, delete_attachments=False):
        where = "" if conversation_id is None else " WHERE conversation_id=?"
        params = () if conversation_id is None else (conversation_id,)
        with self.log_lock, self.db() as db:
            if conversation_id is not None and not db.execute(
                    "SELECT 1 FROM conversations WHERE id=?", params).fetchone():
                raise LookupError("Conversation not found.")
            rows = list(db.execute("SELECT * FROM messages" + where, params))
            if any(row["direction"] == "outbound" and row["status"] == "sending" for row in rows):
                raise ValueError("A message is being sent. Wait for it to finish before clearing history.")
            paths = {row["attachment_path"] for row in rows if row["attachment_path"]}
            paths.update(row["path"] for row in db.execute(
                "SELECT path FROM retained_attachments" + where, params))
            if conversation_id is None and delete_attachments:
                paths.update(URI_PREFIX + row["object_key"]
                             for row in db.execute("SELECT object_key FROM durable_objects"))
            for path in paths:
                object_key(path)
            for row in rows:
                if row["client_id"]:
                    db.execute("INSERT OR IGNORE INTO deleted_requests VALUES (?)", (row["client_id"],))
                for message_id in {row["id"], row["platform_id"]} - {None, ""}:
                    db.execute("INSERT OR IGNORE INTO deleted_messages VALUES (?)", (message_id,))
                    db.execute("DELETE FROM delivery WHERE id=?", (message_id,))
                if not delete_attachments and row["attachment_path"]:
                    db.execute("INSERT OR IGNORE INTO retained_attachments VALUES (?,?)",
                               (row["attachment_path"], row["conversation_id"]))
            if delete_attachments:
                db.execute("DELETE FROM retained_attachments" + where, params)
            db.execute("DELETE FROM messages" + where, params)
            if conversation_id is None:
                db.execute("DELETE FROM delivery")
            conversation_where = "" if conversation_id is None else " WHERE id=?"
            db.execute("UPDATE conversations SET last_message='',last_message_at=0,unread_count=0,"
                       "history_revision=history_revision+1" + conversation_where, params)
            db.execute("DELETE FROM archive_entries" + where, params)
            references = {row["attachment_path"] for row in db.execute(
                "SELECT attachment_path FROM messages WHERE attachment_path IS NOT NULL")}
            references.update(row["path"] for row in db.execute("SELECT path FROM retained_attachments"))
            to_delete = paths - references if delete_attachments else set()
            for path in to_delete:
                db.execute("INSERT OR IGNORE INTO object_deletions VALUES (?)", (object_key(path),))
        self.flush_deletions()
        self.refresh_log_cache()
        with self.db() as db:
            pending_keys = {row["object_key"] for row in db.execute(
                "SELECT object_key FROM object_deletions")}
        pending_selected = {object_key(path) for path in to_delete} & pending_keys
        return {"deleted_messages": len(rows),
                "deleted_attachments": len(to_delete) - len(pending_selected),
                "attachments_kept": not delete_attachments,
                "attachment_deletions_pending": len(pending_keys)}
