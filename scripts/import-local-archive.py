#!/usr/bin/env python
"""One-time DEVELOPMENT data import. Originals are never edited or deleted.

Production schema/data are managed by Replit Publish, not by this script.
Stop the SQLite-backed workflow before running this import, then select
STORAGE_BACKEND=postgres and restart it. This command refuses a nonempty target.
"""
import argparse
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from src.cloud_storage import CloudStore, object_key
from src.storage import Store

TABLES = (
    "settings", "phones", "conversations", "messages", "delivery",
    "deleted_messages", "deleted_requests", "retained_attachments",
    "login_attempts", "quote_evaluations", "quote_evaluation_sources",
)


def import_archive(source, target):
    connection = sqlite3.connect(f"file:{source / 'inbox.sqlite3'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    uploaded = []
    try:
        rows = {table: [dict(row) for row in connection.execute("SELECT * FROM " + table)]
                for table in TABLES}
        with target.db() as db:
            if db.execute("SELECT value FROM settings WHERE key='durable_import_completed'").fetchone():
                raise RuntimeError("This archive was already imported. Originals remain untouched.")
            for table in ("messages", "conversations", "quote_evaluations", "settings"):
                if db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]:
                    raise RuntimeError("Target is not empty. Refusing to overwrite durable data.")
            mapping = {}
            folder = (source / "attachments").resolve()
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(folder):
                    raise RuntimeError("An archived attachment has an unsafe path.")
                uri = target.upload_attachment(resolved)
                uploaded.append(object_key(uri))
                mapping[str(resolved)] = uri

            conversations = {row["id"]: row for row in rows["conversations"]}
            # Attribute original archive lines to their chat, so later explicit
            # per-chat deletion also removes its original readable log records.
            lines_to_chat = {}
            formatting = Store.__new__(Store)
            for row in rows["messages"]:
                lines_to_chat[Store.format_log_message(
                    formatting, row, conversations[row["conversation_id"]])] = row["conversation_id"]
            ids_to_chat = {row["id"]: row["conversation_id"] for row in rows["messages"]}

            for table in TABLES:
                for original in rows[table]:
                    row = dict(original)
                    field = ("attachment_path" if table == "messages" else
                             "path" if table == "retained_attachments" else None)
                    if field and row.get(field):
                        uri = mapping.get(str(Path(row[field]).resolve()))
                        if not uri:
                            raise RuntimeError("An indexed attachment is missing; import was rolled back.")
                        row[field] = uri
                    columns = list(row)
                    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns))
                    db.connection.execute(query, [row[column] for column in columns])

            log = source / "messages.txt"
            if log.exists():
                for line in log.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
                    chat = lines_to_chat.get(line)
                    if line.startswith("[ATTACHMENT] Message: "):
                        message_id = line.split(" | ", 1)[0].removeprefix("[ATTACHMENT] Message: ")
                        chat = ids_to_chat.get(message_id)
                    if chat is None:
                        # Older copies of a known message can differ in saved
                        # path/status. Use its exact date/sender/type prefix.
                        prefix = line.split(" | ", 2)[:2]
                        chat = next((cid for known, cid in lines_to_chat.items()
                                     if known.split(" | ", 2)[:2] == prefix), None)
                    db.execute("INSERT INTO archive_entries(conversation_id,entry) VALUES (?,?)",
                               (chat, line))
            else:
                for row in rows["messages"]:
                    db.execute("INSERT INTO archive_entries(conversation_id,entry) VALUES (?,?)",
                               (row["conversation_id"], Store.format_log_message(
                                   formatting, row, conversations[row["conversation_id"]])))
            for table, column in (
                ("conversations", "id"), ("messages", "seq"), ("quote_evaluations", "id")
            ):
                db.connection.execute(sql.SQL(
                    "SELECT setval(pg_get_serial_sequence(%s,%s),"
                    "COALESCE((SELECT MAX({col}) FROM {table}),1),"
                    "EXISTS(SELECT 1 FROM {table}))"
                ).format(col=sql.Identifier(column), table=sql.Identifier(table)), (table, column))
            db.execute("INSERT INTO settings VALUES ('durable_import_completed','1')")
        target.refresh_log_cache()
        return {"messages": len(rows["messages"]), "attachments": len(mapping),
                "comparisons": len(rows["quote_evaluations"])}
    except Exception:
        # Network uploads cannot roll back with SQL. Persist a deletion queue if
        # the import failed; never discard the workspace originals.
        with target.db() as db:
            for key in uploaded:
                db.execute("INSERT OR IGNORE INTO durable_objects VALUES (?)", (key,))
                db.execute("INSERT OR IGNORE INTO object_deletions VALUES (?)", (key,))
        target.flush_deletions()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-development", action="store_true", required=True)
    parser.add_argument("--source", type=Path, default=Path("whatsapp_data"))
    args = parser.parse_args()
    if os.getenv("REPLIT_DEPLOYMENT") == "1":
        raise SystemExit("This development-only importer must not run in a deployment.")
    target = CloudStore(args.source, os.environ["DATABASE_URL"], recover_jobs=False)
    print(import_archive(args.source.resolve(), target))


if __name__ == "__main__":
    main()
