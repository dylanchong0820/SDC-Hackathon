"""Local inbox index alongside the original text logs and attachment folders."""
from contextlib import contextmanager
from datetime import datetime
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


class Store:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve()
        self.attachments = self.root / "attachments"
        self.attachments.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "messages.txt"
        self.db_path = self.root / "inbox.sqlite3"
        self.log_lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS phones (
                    id TEXT PRIMARY KEY, display_phone_number TEXT DEFAULT '',
                    verified_name TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, phone_number_id TEXT NOT NULL,
                    wa_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                    last_message TEXT DEFAULT '', last_message_at INTEGER DEFAULT 0,
                    last_inbound_at INTEGER DEFAULT 0, unread_count INTEGER DEFAULT 0,
                    UNIQUE(phone_number_id, wa_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    platform_id TEXT UNIQUE, client_id TEXT UNIQUE,
                    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
                    direction TEXT NOT NULL, type TEXT NOT NULL, body TEXT DEFAULT '',
                    caption TEXT DEFAULT '', timestamp INTEGER NOT NULL,
                    status TEXT NOT NULL, filename TEXT DEFAULT '', mime_type TEXT DEFAULT '',
                    attachment_path TEXT, attachment_state TEXT DEFAULT 'none',
                    attachment_error TEXT, media_id TEXT, raw TEXT DEFAULT '{}',
                    error TEXT, context_id TEXT, attempts INTEGER DEFAULT 0,
                    next_attempt INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS messages_conversation
                    ON messages(conversation_id, timestamp, seq);
                CREATE TABLE IF NOT EXISTS delivery (
                    id TEXT PRIMARY KEY, status TEXT, timestamp INTEGER, error TEXT
                );
                CREATE TABLE IF NOT EXISTS deleted_messages (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS deleted_requests (client_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS retained_attachments (
                    path TEXT PRIMARY KEY, conversation_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    identity TEXT, timestamp INTEGER
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(conversations)")}
            if "history_revision" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN history_revision INTEGER NOT NULL DEFAULT 0")
        self.import_legacy()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def setting(self, key, default=""):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))

    def phones(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM phones ORDER BY id")]

    def learn_phone(self, phone_id, display=""):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO phones(id,display_phone_number) VALUES (?,?)",
                       (phone_id, display))
            if display:
                db.execute("UPDATE phones SET display_phone_number=? WHERE id=?",
                           (display, phone_id))
            if not db.execute("SELECT 1 FROM settings WHERE key='phone_number_id'").fetchone():
                db.execute("INSERT INTO settings VALUES ('phone_number_id',?)", (phone_id,))
            # Old text logs did not include a business phone ID. Adopt them into
            # the first real number, without losing either conversation's messages.
            for old in db.execute("SELECT * FROM conversations WHERE phone_number_id='legacy'").fetchall():
                existing = db.execute(
                    "SELECT id FROM conversations WHERE phone_number_id=? AND wa_id=?",
                    (phone_id, old["wa_id"])).fetchone()
                if existing:
                    db.execute("UPDATE messages SET conversation_id=? WHERE conversation_id=?",
                               (existing["id"], old["id"]))
                    db.execute("DELETE FROM conversations WHERE id=?", (old["id"],))
                else:
                    db.execute("UPDATE conversations SET phone_number_id=? WHERE id=?",
                               (phone_id, old["id"]))

    def conversation(self, conversation_id):
        with self.db() as db:
            row = db.execute("""
                SELECT c.*, COALESCE(p.display_phone_number,'') AS display_phone_number
                FROM conversations c LEFT JOIN phones p ON p.id=c.phone_number_id
                WHERE c.id=?""", (conversation_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["window_open"] = result["last_inbound_at"] > int(time.time()) - 86400
        return result

    def get_or_create_conversation(self, phone_id, wa_id, name=""):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO conversations(phone_number_id,wa_id,name) "
                       "VALUES (?,?,?)", (phone_id, wa_id, name or wa_id))
            if name:
                db.execute("UPDATE conversations SET name=? WHERE phone_number_id=? AND wa_id=?",
                           (name, phone_id, wa_id))
            row = db.execute("SELECT id FROM conversations WHERE phone_number_id=? AND wa_id=?",
                             (phone_id, wa_id)).fetchone()
        return self.conversation(row["id"])

    def conversations(self, search=""):
        with self.db() as db:
            rows = db.execute("""
                SELECT id FROM conversations WHERE name LIKE ? OR wa_id LIKE ?
                ORDER BY last_message_at DESC, id DESC LIMIT 500
            """, (f"%{search}%", f"%{search}%")).fetchall()
        return [self.conversation(row["id"]) for row in rows]

    def message(self, message_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def public_message(row):
        result = {k: row.get(k) for k in (
            "id", "seq", "conversation_id", "direction", "type", "body", "caption",
            "timestamp", "status", "filename", "mime_type", "attachment_state",
            "attachment_error", "error", "context_id")}
        result["raw"] = json.loads(row.get("raw") or "{}")
        from urllib.parse import quote
        result["media_url"] = ("/api/media/" + quote(row["id"], safe="")
                               if row["attachment_state"] == "saved" else None)
        return result

    def messages(self, conversation_id, before=None):
        with self.db() as db:
            db.execute("BEGIN")
            conversation = db.execute("SELECT history_revision FROM conversations WHERE id=?",
                                      (conversation_id,)).fetchone()
            rows = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND seq<? "
                "ORDER BY seq DESC LIMIT 101",
                (conversation_id, before or 9223372036854775807)).fetchall()
        return {"messages": [self.public_message(dict(r)) for r in reversed(rows[:100])],
                "has_more": len(rows) > 100,
                "history_revision": conversation["history_revision"] if conversation else 0}

    def insert_inbound(self, conversation_id, record):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM deleted_messages WHERE id=?", (record["id"],)).fetchone():
                return False
            result = db.execute("""
                INSERT OR IGNORE INTO messages
                (id,platform_id,conversation_id,direction,type,body,caption,timestamp,
                 status,filename,mime_type,attachment_state,media_id,raw,context_id)
                VALUES (?,?,?,'inbound',?,?,?,?, 'received',?,?,?,?,?,?)
            """, (record["id"], record["id"], conversation_id, record["type"],
                  record["body"], record["caption"], record["timestamp"],
                  record["filename"], record["mime_type"],
                  "pending" if record["media_id"] else "none", record["media_id"],
                  json.dumps(record["raw"], ensure_ascii=False), record["context_id"]))
            if result.rowcount == 0:
                return False
            preview = record["body"] or record["caption"] or f"[{record['type']}]"
            db.execute("""
                UPDATE conversations SET
                last_message=CASE WHEN last_message_at<=? THEN ? ELSE last_message END,
                last_message_at=MAX(last_message_at,?),
                last_inbound_at=MAX(last_inbound_at,?), unread_count=unread_count+1
                WHERE id=?
            """, (record["timestamp"], preview, record["timestamp"],
                  record["timestamp"], conversation_id))
        self.log_message(self.message(record["id"]))
        return True

    def format_log_message(self, row, conversation=None):
        conversation = conversation or self.conversation(row["conversation_id"])
        date = datetime.fromtimestamp(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        label = "From" if row["direction"] == "inbound" else "To"
        prefix = f"[{date}] {label}: {conversation['wa_id']} | Type: {row['type']} | "
        if row["type"] == "text":
            detail = "Message: " + row["body"]
        elif row["media_id"]:
            detail = "Saved: " + (row["attachment_path"] or row["attachment_state"])
            if row["caption"]:
                detail += " | Caption: " + row["caption"]
        else:
            detail = "Raw: " + row["raw"]
        # Keep the original text-log format. Escape embedded newlines so a
        # customer's text cannot forge a second log record.
        detail = detail.replace("\r", "\\r").replace("\n", "\\n")
        return prefix + detail + "\n"

    def log_message(self, row):
        with self.log_lock:
            if not row or not self.message(row["id"]):
                return
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(self.format_log_message(row))

    def clear_history(self, conversation_id=None, delete_attachments=False):
        try:
            from .history import clear_history
        except ImportError:
            from history import clear_history
        return clear_history(self, conversation_id, delete_attachments)

    def new_outgoing(self, conversation, body, client_id):
        message_id = "out_" + uuid.uuid4().hex
        now = int(time.time())
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM deleted_requests WHERE client_id=?", (client_id,)).fetchone():
                raise ValueError("This message's history was deleted. Start a new send request.")
            existing = db.execute("SELECT * FROM messages WHERE client_id=?", (client_id,)).fetchone()
            if existing:
                if existing["conversation_id"] != conversation["id"] or existing["body"] != body:
                    raise ValueError("This request ID was already used for a different message.")
                return dict(existing), False
            db.execute("""
                INSERT INTO messages(id,client_id,conversation_id,direction,type,body,timestamp,status)
                VALUES (?,?,?,'outbound','text',?,?,'sending')
            """, (message_id, client_id, conversation["id"], body, now))
            db.execute("UPDATE conversations SET last_message=?,last_message_at=? WHERE id=?",
                       (body, now, conversation["id"]))
        return self.message(message_id), True

    def finish_outgoing(self, message_id, platform_id=None, error=None):
        with self.db() as db:
            status = "failed" if error else "accepted"
            if platform_id:
                prior = db.execute("SELECT * FROM delivery WHERE id=?", (platform_id,)).fetchone()
                if prior:
                    status, error = prior["status"], prior["error"]
            db.execute("UPDATE messages SET platform_id=?,status=?,error=? WHERE id=?",
                       (platform_id, status, error, message_id))
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            result = dict(row) if row else None
        self.log_message(result)
        return result

    def delivery_status(self, payload):
        status = payload.get("status")
        if status not in {"sent", "delivered", "read", "failed"} or not payload.get("id"):
            return
        timestamp = int(payload.get("timestamp") or time.time())
        error = "; ".join(e.get("message") or e.get("title") or "Delivery failed"
                          for e in payload.get("errors", []) if isinstance(e, dict)) or None
        ranks = {"accepted": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 4}
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM deleted_messages WHERE id=?", (payload["id"],)).fetchone():
                return
            previous = db.execute("SELECT * FROM delivery WHERE id=?", (payload["id"],)).fetchone()
            if previous and (previous["timestamp"] > timestamp or
                             ranks.get(previous["status"], 0) > ranks[status]):
                return
            db.execute("INSERT OR REPLACE INTO delivery VALUES (?,?,?,?)",
                       (payload["id"], status, timestamp, error))
            db.execute("UPDATE messages SET status=?,error=? WHERE platform_id=? AND direction='outbound'",
                       (status, error, payload["id"]))

    def mark_read(self, conversation_id):
        with self.db() as db:
            db.execute("UPDATE conversations SET unread_count=0 WHERE id=?", (conversation_id,))

    def claim_media(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT * FROM messages WHERE attachment_state IN ('pending','downloading')
                AND next_attempt<=? ORDER BY seq LIMIT 1
            """, (int(time.time()),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE messages SET attachment_state='downloading',attempts=attempts+1,"
                       "next_attempt=? WHERE id=?", (int(time.time()) + 300, row["id"]))
        result = dict(row)
        result["attempts"] += 1
        return result

    def media_saved(self, row, path, mime, filename):
        with self.log_lock:
            with self.db() as db:
                result = db.execute("""
                    UPDATE messages SET attachment_path=?,mime_type=?,filename=?,
                    attachment_state='saved',attachment_error=NULL WHERE id=?
                """, (str(path), mime, filename, row["id"]))
            if not result.rowcount:
                # A clear-history action cancelled this job while it downloaded.
                candidate = Path(path).resolve()
                if candidate.is_relative_to(self.attachments) and candidate.is_file():
                    candidate.unlink()
                return
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[ATTACHMENT] Message: {row['id']} | Saved: {path}\n")

    def media_failed(self, row, error):
        with self.db() as db:
            db.execute("""
                UPDATE messages SET attachment_state=?,attachment_error=?,next_attempt=?
                WHERE id=?
            """, ("failed" if row["attempts"] >= 3 else "pending", error,
                  int(time.time()) + min(5 * 2 ** row["attempts"], 300), row["id"]))

    def retry_media(self, message_id):
        with self.db() as db:
            return db.execute("""
                UPDATE messages SET attachment_state='pending',attempts=0,next_attempt=0,
                attachment_error=NULL WHERE id=? AND media_id IS NOT NULL
                AND attachment_state='failed'
            """, (message_id,)).rowcount > 0

    def media_path(self, row):
        if not row or not row["attachment_path"]:
            return None
        path = Path(row["attachment_path"]).resolve()
        if not path.is_relative_to(self.attachments) or not path.is_file():
            return None
        return path

    def login_allowed(self, identity):
        now = int(time.time())
        with self.db() as db:
            db.execute("DELETE FROM login_attempts WHERE timestamp<?", (now - 900,))
            return db.execute("SELECT COUNT(*) FROM login_attempts WHERE identity=?",
                              (identity,)).fetchone()[0] < 5

    def login_failed(self, identity):
        with self.db() as db:
            db.execute("INSERT INTO login_attempts VALUES (?,?)", (identity, int(time.time())))

    def import_legacy(self):
        if self.setting("legacy_imported") == "1":
            return
        if self.log_path.exists():
            pattern = re.compile(
                r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] From: ([+\d]+) \| Type: (\w+) \| (.*)$")
            with self.log_path.open(encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle):
                    match = pattern.match(line.rstrip("\n"))
                    if not match:
                        continue
                    date, sender, kind, detail = match.groups()
                    try:
                        timestamp = int(datetime.strptime(date, "%Y-%m-%d %H:%M:%S").timestamp())
                    except ValueError:
                        continue
                    conversation = self.get_or_create_conversation("legacy", sender)
                    body = detail.removeprefix("Message: ") if kind == "text" else f"[{kind}]"
                    saved = None
                    caption = ""
                    if detail.startswith("Saved: "):
                        value, _, caption = detail[7:].partition(" | Caption: ")
                        candidate = Path(value).resolve()
                        if candidate.is_relative_to(self.attachments) and candidate.is_file():
                            saved = str(candidate)
                    with self.db() as db:
                        db.execute("""
                            INSERT OR IGNORE INTO messages
                            (id,conversation_id,direction,type,body,caption,timestamp,status,
                             attachment_path,attachment_state,filename,mime_type,raw)
                            VALUES (?,?,'inbound',?,?,?,?,'received',?,?,?,?,?)
                        """, (f"legacy_{line_number}", conversation["id"], kind, body, caption,
                              timestamp, saved, "saved" if saved else "none",
                              Path(saved).name if saved else "",
                              (mimetypes.guess_type(saved)[0] or "application/octet-stream") if saved else "",
                              json.dumps({"legacy_log": line.strip()})))
                        db.execute("""
                            UPDATE conversations SET last_message=?,last_message_at=?,
                            last_inbound_at=? WHERE id=? AND last_message_at<=?
                        """, (body, timestamp, timestamp, conversation["id"], timestamp))
        self.set_setting("legacy_imported", "1")
