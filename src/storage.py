"""Local inbox index alongside the original text logs and attachment folders."""
from contextlib import contextmanager
from datetime import datetime
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


# Serialize the complete connection lifecycle, not just writes. Concurrent
# open/close calls have stalled in the runtime's SQLite inode/FD-list mutex.
# Keep network requests and file downloads outside this critical section.
_SQLITE_LOCK = threading.RLock()
SQLITE_WAIT_SECONDS = 5


class StorageBusyError(RuntimeError):
    """The archive is busy; the caller may safely retry later."""


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
                CREATE TABLE IF NOT EXISTS quote_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    selected_message_id TEXT,
                    error TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS quote_evaluation_sources (
                    evaluation_id INTEGER NOT NULL
                        REFERENCES quote_evaluations(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    conversation_id INTEGER NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (evaluation_id, message_id),
                    UNIQUE (evaluation_id, position)
                );
                CREATE INDEX IF NOT EXISTS quote_sources_message
                    ON quote_evaluation_sources(message_id);
                CREATE INDEX IF NOT EXISTS quote_evaluations_recent
                    ON quote_evaluations(created_at DESC);
                CREATE TRIGGER IF NOT EXISTS delete_quote_evaluations_with_history
                BEFORE DELETE ON messages
                BEGIN
                    DELETE FROM quote_evaluations
                    WHERE id IN (
                        SELECT evaluation_id FROM quote_evaluation_sources
                        WHERE message_id=OLD.id
                    );
                END;
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(conversations)")}
            if "history_revision" not in columns:
                db.execute("ALTER TABLE conversations ADD COLUMN history_revision INTEGER NOT NULL DEFAULT 0")
            source_columns = {row["name"] for row in db.execute("PRAGMA table_info(quote_evaluation_sources)")}
            if "reviewed_digest" not in source_columns:
                db.execute("ALTER TABLE quote_evaluation_sources ADD COLUMN reviewed_digest TEXT")
            db.execute("""
                UPDATE quote_evaluations
                SET status='failed', error='The server restarted before analysis finished.',
                    updated_at=?
                WHERE status IN ('queued','processing')
            """, (int(time.time()),))
        self.import_legacy()

    @contextmanager
    def db(self):
        if not _SQLITE_LOCK.acquire(timeout=SQLITE_WAIT_SECONDS):
            raise StorageBusyError("The inbox archive is busy. Please try again shortly.")
        try:
            connection = sqlite3.connect(self.db_path, timeout=SQLITE_WAIT_SECONDS)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                with connection:
                    yield connection
            finally:
                connection.close()
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise StorageBusyError("The inbox archive is busy. Please try again shortly.") from error
            raise
        finally:
            _SQLITE_LOCK.release()

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

    def quote_candidates(self, conversation_id=None, limit=500):
        query = """
            SELECT m.*,c.name AS client_name,c.wa_id
            FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.direction='inbound'
              AND (trim(m.body)<>'' OR trim(m.caption)<>'' OR m.media_id IS NOT NULL
                   OR m.attachment_path IS NOT NULL)
        """
        params = []
        if conversation_id is not None:
            query += " AND m.conversation_id=?"
            params.append(conversation_id)
        query += " ORDER BY m.timestamp DESC,m.seq DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self.db() as db:
            rows = [dict(row) for row in db.execute(query, params).fetchall()]

        candidates = []
        from urllib.parse import quote
        for row in rows:
            path = self.media_path(row)
            is_text = row["type"] == "text" and bool((row["body"] or "").strip())
            ready = is_text or path is not None
            size = None
            if path:
                try:
                    size = path.stat().st_size
                except OSError:
                    ready = is_text
            candidates.append({
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "client_name": row["client_name"] or row["wa_id"],
                "wa_id": row["wa_id"],
                "type": row["type"],
                "body": row["body"] or "",
                "caption": row["caption"] or "",
                "filename": row["filename"] or "",
                "mime_type": row["mime_type"] or "",
                "timestamp": row["timestamp"],
                "attachment_state": row["attachment_state"],
                "ready": ready,
                "media_url": ("/api/media/" + quote(row["id"], safe="") if path else None),
                "attachment_size": size,
            })
        return candidates

    def quote_preview_inputs(self, message_ids):
        if (not isinstance(message_ids, list) or not 1 <= len(message_ids) <= 8
                or any(not isinstance(item, str) or not item or len(item) > 200 for item in message_ids)):
            raise ValueError("Select between 1 and 8 valid client messages or attachments.")
        ids = list(dict.fromkeys(message_ids))
        with self.db() as db:
            rows = db.execute(f"""
                SELECT m.*,c.name AS client_name FROM messages m
                JOIN conversations c ON c.id=m.conversation_id
                WHERE m.id IN ({','.join('?' for _ in ids)})
            """, ids).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        if len(by_id) != len(ids):
            raise ValueError("One or more selected messages are no longer available.")
        if any(row["direction"] != "inbound" for row in rows):
            raise ValueError("Only client messages can be reviewed.")
        return [by_id[item] for item in ids]

    def create_quote_evaluation(self, message_ids, priority, reviewed_digests=None):
        if priority not in {"price", "time", "balanced"}:
            raise ValueError("Choose price, delivery time, or balanced comparison.")
        if not isinstance(message_ids, list) or not 1 <= len(message_ids) <= 8:
            raise ValueError("Select between 1 and 8 client messages or attachments.")
        ids = []
        for message_id in message_ids:
            if not isinstance(message_id, str) or not message_id or len(message_id) > 200:
                raise ValueError("A selected message ID is invalid.")
            if message_id not in ids:
                ids.append(message_id)
        if not ids:
            raise ValueError("Select at least one client message or attachment.")

        placeholders = ",".join("?" for _ in ids)
        with self.db() as db:
            rows = db.execute(f"""
                SELECT id,conversation_id,direction,type,body,caption,attachment_state,
                       attachment_path,media_id
                FROM messages WHERE id IN ({placeholders})
            """, ids).fetchall()
            by_id = {row["id"]: dict(row) for row in rows}
            if len(by_id) != len(ids):
                raise ValueError("One or more selected messages are no longer available.")

            for message_id in ids:
                row = by_id[message_id]
                if row["direction"] != "inbound":
                    raise ValueError("Only client messages can be compared.")
                if row["type"] == "text" and (row["body"] or "").strip():
                    continue
                attachment = self.media_path(row)
                if not attachment:
                    raise ValueError("A selected attachment is not saved yet or is no longer available.")

            now = int(time.time())
            cursor = db.execute("""
                INSERT INTO quote_evaluations(created_at,updated_at,priority,status)
                VALUES (?,?,?,'queued')
            """, (now, now, priority))
            evaluation_id = cursor.lastrowid
            db.executemany("""
                INSERT INTO quote_evaluation_sources
                    (evaluation_id,message_id,conversation_id,position,reviewed_digest)
                VALUES (?,?,?,?,?)
            """, [(evaluation_id, message_id, by_id[message_id]["conversation_id"], position,
                   (reviewed_digests or {}).get(message_id))
                  for position, message_id in enumerate(ids)])
        return self.quote_evaluation(evaluation_id)

    def quote_evaluation_inputs(self, evaluation_id):
        with self.db() as db:
            evaluation = db.execute(
                "SELECT id,priority,status FROM quote_evaluations WHERE id=?",
                (evaluation_id,)).fetchone()
            if not evaluation:
                return None
            rows = db.execute("""
                SELECT m.*,c.name AS client_name,c.wa_id,s.reviewed_digest
                FROM quote_evaluation_sources s
                JOIN messages m ON m.id=s.message_id
                JOIN conversations c ON c.id=s.conversation_id
                WHERE s.evaluation_id=? ORDER BY s.position
            """, (evaluation_id,)).fetchall()
        return {"evaluation": dict(evaluation), "messages": [dict(row) for row in rows]}

    def update_quote_evaluation(self, evaluation_id, status, result=None, error=None):
        if status not in {"queued", "processing", "completed", "failed", "cancelled"}:
            raise ValueError("Invalid quote evaluation status.")
        with self.db() as db:
            if result is None:
                db.execute("""
                    UPDATE quote_evaluations SET status=?,error=?,updated_at=?
                    WHERE id=? AND status IN ('queued','processing')
                """, (status, str(error or "")[:1200], int(time.time()), evaluation_id))
            else:
                db.execute("""
                    UPDATE quote_evaluations
                    SET status=?,result_json=?,error=?,updated_at=? WHERE id=?
                    AND status IN ('queued','processing')
                """, (status, json.dumps(result, ensure_ascii=False),
                      str(error or "")[:1200], int(time.time()), evaluation_id))
            exists = db.execute(
                "SELECT 1 FROM quote_evaluations WHERE id=?", (evaluation_id,)).fetchone()
        return self.quote_evaluation(evaluation_id) if exists else None

    def cancel_quote_evaluation(self, evaluation_id):
        """Cancel an active comparison without allowing late worker results to replace it."""
        with self.db() as db:
            db.execute("""
                UPDATE quote_evaluations
                SET status='cancelled',error='Stopped by staff.',updated_at=?
                WHERE id=? AND status IN ('queued','processing')
            """, (int(time.time()), evaluation_id))
            exists = db.execute(
                "SELECT 1 FROM quote_evaluations WHERE id=?", (evaluation_id,)).fetchone()
        return self.quote_evaluation(evaluation_id) if exists else None

    def quote_evaluation(self, evaluation_id):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM quote_evaluations WHERE id=?", (evaluation_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["result"] = json.loads(result.pop("result_json") or "{}")
        except (TypeError, ValueError):
            result["result"] = {}
            result.pop("result_json", None)
        return result

    def quote_evaluations(self, limit=20):
        with self.db() as db:
            rows = db.execute("""
                SELECT * FROM quote_evaluations
                ORDER BY created_at DESC,id DESC LIMIT ?
            """, (max(1, min(int(limit), 50)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["result"] = json.loads(item.pop("result_json") or "{}")
            except (TypeError, ValueError):
                item["result"] = {}
                item.pop("result_json", None)
            result.append(item)
        return result

    def upgrade_quote_currency(self, converter):
        """Upgrade old snapshots without changing team choices or analysis dates."""
        with self.db() as db:
            rows = db.execute(
                "SELECT id,priority,result_json FROM quote_evaluations WHERE status='completed'"
            ).fetchall()
        for row in rows:
            try:
                result = json.loads(row["result_json"] or "{}")
                quotes = result.get("quotes", [])
                if not quotes or not isinstance(quotes, list):
                    continue
                retry = any(item.get("conversion_error") for item in quotes)
                if result.get("myr_version") == 1 and (
                        not retry or time.time() - result.get("fx_checked_at", 0) < 60):
                    continue
                converted = converter(quotes, row["priority"])
            except (ValueError, TypeError, AttributeError):
                continue
            with self.db() as db:
                db.execute("""
                    UPDATE quote_evaluations SET result_json=?
                    WHERE id=? AND status='completed' AND result_json=?
                """, (json.dumps(converted, ensure_ascii=False), row["id"], row["result_json"]))

    def quotation_register(self):
        """Return one row per source in every completed comparison."""
        with self.db() as db:
            rows = db.execute("""
                SELECT e.id AS comparison_id,e.created_at AS comparison_created_at,
                       e.updated_at AS analyzed_at,e.priority,e.selected_message_id,
                       e.result_json,s.message_id,s.position,m.timestamp AS received_at,
                       m.type AS message_type,m.filename,m.mime_type,m.body,m.caption,
                       c.name AS client_name,c.wa_id
                FROM quote_evaluations e
                JOIN quote_evaluation_sources s ON s.evaluation_id=e.id
                JOIN messages m ON m.id=s.message_id
                JOIN conversations c ON c.id=s.conversation_id
                WHERE e.status='completed'
                ORDER BY e.created_at DESC,e.id DESC,s.position
            """).fetchall()

        register = []
        decoded = {}
        for row in rows:
            comparison_id = row["comparison_id"]
            if comparison_id not in decoded:
                try:
                    decoded[comparison_id] = json.loads(row["result_json"] or "{}")
                except (TypeError, ValueError):
                    decoded[comparison_id] = {}
            result = decoded[comparison_id]
            quotes = result.get("quotes", []) if isinstance(result, dict) else []
            if not isinstance(quotes, list):
                quotes = []
            quote = next((
                item for item in quotes
                if isinstance(item, dict)
                and str(item.get("message_id", "")) == str(row["message_id"])
            ), None)
            if not quote:
                continue
            notes = quote.get("notes") or []
            if isinstance(notes, list):
                notes = " · ".join(str(item) for item in notes if item)
            else:
                notes = str(notes)
            register.append({
                "comparison_id": comparison_id,
                "message_id": row["message_id"],
                "received_at": row["received_at"],
                "analyzed_at": row["analyzed_at"],
                "priority": row["priority"],
                "client_name": row["client_name"] or row["wa_id"],
                "wa_id": row["wa_id"],
                "message_type": row["message_type"],
                "filename": row["filename"] or quote.get("filename") or "",
                "vendor": quote.get("vendor") or "",
                "total_price": quote.get("total_price"),
                "currency": quote.get("currency") or "",
                "myr_total_price": quote.get("myr_total_price"),
                "exchange_rate": quote.get("exchange_rate"),
                "exchange_rate_date": quote.get("exchange_rate_date") or "",
                "exchange_rate_source": quote.get("exchange_rate_source") or "",
                "conversion_error": quote.get("conversion_error") or "",
                "price_basis": quote.get("price_basis") or "",
                "delivery_days": quote.get("delivery_days"),
                "delivery_unit": quote.get("delivery_unit") or "",
                "delivery_date": quote.get("delivery_date") or "",
                "delivery_text": quote.get("delivery_text") or "",
                "rank": quote.get("rank"),
                "recommended": bool(quote.get("recommended")),
                "team_choice": str(row["selected_message_id"] or "") == str(row["message_id"]),
                "confidence": quote.get("confidence"),
                "evidence": quote.get("evidence") or "",
                "notes": notes,
                "extraction_error": quote.get("extraction_error") or "",
                "message_excerpt": (row["body"] or row["caption"] or "")[:1000],
            })
        return register

    def select_quote(self, evaluation_id, message_id):
        with self.db() as db:
            evaluation = db.execute(
                "SELECT status FROM quote_evaluations WHERE id=?", (evaluation_id,)).fetchone()
            if not evaluation:
                return None
            if evaluation["status"] != "completed":
                raise ValueError("Wait for the comparison to finish before choosing a quotation.")
            source = db.execute("""
                SELECT 1 FROM quote_evaluation_sources
                WHERE evaluation_id=? AND message_id=?
            """, (evaluation_id, message_id)).fetchone()
            if not source:
                raise ValueError("Choose one of the quotations in this comparison.")
            db.execute("""
                UPDATE quote_evaluations SET selected_message_id=?,updated_at=? WHERE id=?
            """, (message_id, int(time.time()), evaluation_id))
        return self.quote_evaluation(evaluation_id)

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

    def messages(self, conversation_id, before=None, before_timestamp=None, before_seq=None,
                 around_timestamp=None, around_seq=None):
        with self.db() as db:
            db.execute("BEGIN")
            conversation = db.execute("SELECT history_revision FROM conversations WHERE id=?",
                                      (conversation_id,)).fetchone()
            if around_timestamp is not None and around_seq is not None:
                older = db.execute("""
                    SELECT * FROM messages WHERE conversation_id=?
                    AND (timestamp<? OR (timestamp=? AND seq<?))
                    ORDER BY timestamp DESC,seq DESC LIMIT 51
                """, (conversation_id, around_timestamp, around_timestamp, around_seq)).fetchall()
                newer = db.execute("""
                    SELECT * FROM messages WHERE conversation_id=?
                    AND (timestamp>? OR (timestamp=? AND seq>=?))
                    ORDER BY timestamp,seq LIMIT 50
                """, (conversation_id, around_timestamp, around_timestamp, around_seq)).fetchall()
                visible = list(reversed(older[:50])) + newer
                has_more = len(older) > 50
            elif before_timestamp is not None and before_seq is not None:
                rows = db.execute("""
                    SELECT * FROM messages WHERE conversation_id=?
                    AND (timestamp<? OR (timestamp=? AND seq<?))
                    ORDER BY timestamp DESC,seq DESC LIMIT 101
                """, (conversation_id, before_timestamp, before_timestamp, before_seq)).fetchall()
                visible, has_more = rows[:100], len(rows) > 100
            else:
                # The seq-only cursor remains for compatibility with older clients.
                where = "AND seq<?" if before is not None else ""
                params = (conversation_id, before) if before is not None else (conversation_id,)
                rows = db.execute(
                    f"SELECT * FROM messages WHERE conversation_id=? {where} "
                    "ORDER BY timestamp DESC,seq DESC LIMIT 101", params).fetchall()
                visible, has_more = rows[:100], len(rows) > 100
            chronological = list(reversed(visible))
            next_before = None
            if has_more and chronological:
                next_before = {"timestamp": chronological[0]["timestamp"],
                               "seq": chronological[0]["seq"]}
        return {
            "messages": [self.public_message(dict(row)) for row in chronological],
            "has_more": has_more, "next_before": next_before,
            "history_revision": conversation["history_revision"] if conversation else 0,
        }

    def search_messages(self, query, limit=40):
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self.db() as db:
            rows = db.execute("""
                SELECT m.id AS message_id,m.seq,m.conversation_id,m.direction,m.type,
                       m.body,m.caption,m.filename,m.timestamp,c.name,c.wa_id
                FROM messages m JOIN conversations c ON c.id=m.conversation_id
                WHERE m.body LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR m.caption LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR m.filename LIKE ? ESCAPE '\\' COLLATE NOCASE
                ORDER BY m.timestamp DESC,m.seq DESC LIMIT ?
            """, (pattern, pattern, pattern, limit)).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            content = item["body"] or item["caption"] or item["filename"] or item["type"]
            index = content.casefold().find(query.casefold())
            start = max(0, index - 70) if index >= 0 else 0
            end = min(len(content), start + 180)
            item["snippet"] = ("…" if start else "") + content[start:end] + (
                "…" if end < len(content) else "")
            results.append(item)
        return results

    def import_history(self, phone_number_id, wa_id, display_name, records):
        """Add a parsed export once without changing reply-window or delete state."""
        from collections import Counter
        from hashlib import sha256

        with self.log_lock:
            log_existed = self.log_path.exists()
            log_size = self.log_path.stat().st_size if log_existed else 0
            try:
                with self.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    configured = db.execute(
                        "SELECT value FROM settings WHERE key='phone_number_id'").fetchone()
                    if not configured or configured["value"] != phone_number_id:
                        raise ValueError("The selected business number is no longer configured.")

                    db.execute(
                        "INSERT OR IGNORE INTO conversations(phone_number_id,wa_id,name) "
                        "VALUES (?,?,?)", (phone_number_id, wa_id, display_name or wa_id))
                    if display_name:
                        db.execute(
                            "UPDATE conversations SET name=? WHERE phone_number_id=? AND wa_id=? "
                            "AND (name='' OR name=wa_id)",
                            (display_name, phone_number_id, wa_id))
                    conversation = db.execute("""
                        SELECT c.*,COALESCE(p.display_phone_number,'') AS display_phone_number
                        FROM conversations c LEFT JOIN phones p ON p.id=c.phone_number_id
                        WHERE c.phone_number_id=? AND c.wa_id=?
                    """, (phone_number_id, wa_id)).fetchone()
                    conversation = dict(conversation)

                    duplicate_counts = Counter()
                    inserted_rows = []
                    for record in records:
                        identity = (f"{phone_number_id}\0{wa_id}\0{record['timestamp']}\0"
                                    f"{record['direction']}\0{record['body']}")
                        occurrence = duplicate_counts[identity]
                        duplicate_counts[identity] += 1
                        message_id = "history_" + sha256(
                            f"{identity}\0{occurrence}".encode("utf-8")).hexdigest()
                        if db.execute("SELECT 1 FROM deleted_messages WHERE id=?",
                                      (message_id,)).fetchone():
                            continue
                        cursor = db.execute("""
                            INSERT OR IGNORE INTO messages
                            (id,conversation_id,direction,type,body,timestamp,status,
                             attachment_state,raw)
                            VALUES (?,?,?,'text',?,?,'history','none',?)
                        """, (message_id, conversation["id"], record["direction"],
                              record["body"], record["timestamp"],
                              json.dumps({"source": "whatsapp_text_export",
                                          "sender": record.get("author", "")},
                                         ensure_ascii=False)))
                        if cursor.rowcount:
                            inserted_rows.append({
                                "id": message_id, "conversation_id": conversation["id"],
                                "direction": record["direction"], "type": "text",
                                "body": record["body"], "caption": "", "timestamp": record["timestamp"],
                                "status": "history", "media_id": None,
                            })

                    if inserted_rows:
                        latest = max(inserted_rows, key=lambda row: (row["timestamp"], row["id"]))
                        db.execute("""
                            UPDATE conversations
                            SET last_message=?,last_message_at=?
                            WHERE id=? AND last_message_at<=?
                        """, (latest["body"][:500], latest["timestamp"],
                              conversation["id"], latest["timestamp"]))

                    if inserted_rows:
                        with self.log_path.open("a", encoding="utf-8") as handle:
                            for row in inserted_rows:
                                handle.write(self.format_log_message(row, conversation))
                            handle.flush()
                            os.fsync(handle.fileno())
            except Exception:
                try:
                    if log_existed:
                        with self.log_path.open("r+b") as handle:
                            handle.truncate(log_size)
                            handle.flush()
                            os.fsync(handle.fileno())
                    elif self.log_path.exists():
                        self.log_path.unlink()
                except OSError:
                    pass
                raise
        return {"conversation_id": conversation["id"], "messages_imported": len(inserted_rows),
                "duplicates_skipped": len(records) - len(inserted_rows)}

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
