"""Explicit deletion of the local archive; never calls WhatsApp's API."""
from pathlib import Path
import os
import tempfile


def clear_history(store, conversation_id=None, delete_attachments=False):
    where = "" if conversation_id is None else " WHERE conversation_id=?"
    params = () if conversation_id is None else (conversation_id,)
    with store.log_lock, store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        if conversation_id is not None and not db.execute(
                "SELECT 1 FROM conversations WHERE id=?", params).fetchone():
            raise LookupError("Conversation not found.")
        rows = [dict(row) for row in db.execute("SELECT * FROM messages" + where, params)]
        if any(row["direction"] == "outbound" and row["status"] == "sending" for row in rows):
            raise ValueError("A message is being sent. Wait for it to finish before clearing history.")

        retained = [dict(row) for row in db.execute(
            "SELECT * FROM retained_attachments" + where, params)]
        paths = {row["attachment_path"] for row in rows if row["attachment_path"]}
        paths.update(row["path"] for row in retained)
        # Whole-inbox cleanup also covers previously retained/unindexed files.
        if conversation_id is None and delete_attachments:
            paths.update(str(path) for path in store.attachments.rglob("*")
                         if path.is_file() or path.is_symlink())
        safe_paths = set()
        for value in paths:
            path = Path(value).absolute()
            if not path.resolve().is_relative_to(store.attachments):
                raise ValueError("An attachment has an unsafe storage path. History was not cleared.")
            safe_paths.add(path)

        for row in rows:
            if row["client_id"]:
                db.execute("INSERT OR IGNORE INTO deleted_requests VALUES (?)", (row["client_id"],))
            for message_id in {row["id"], row["platform_id"]} - {None, ""}:
                # Keep only opaque IDs so Meta retries cannot restore deleted content.
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
        # Keep contact details and last inbound time (Meta's actual reply window).
        # Rewrite the readable archive from remaining indexed history. No deleted
        # message text/raw payload or attachment log entry survives in this file.
        remaining = [dict(row) for row in db.execute("SELECT * FROM messages ORDER BY seq")]
        conversations = {row["id"]: dict(row) for row in db.execute("SELECT * FROM conversations")}
        def log_line(row):
            return store.format_log_message(row, conversations[row["conversation_id"]])

        referenced = {Path(row["attachment_path"]).resolve() for row in remaining if row["attachment_path"]}
        referenced.update(Path(row["path"]).resolve() for row in db.execute("SELECT path FROM retained_attachments"))
        safe_paths = {path for path in safe_paths if path.resolve() not in referenced}
        # Preserve unrelated, unindexed archive notes for a single-chat clear.
        # Known message lines are regenerated to avoid ambiguous legacy log IDs.
        extra_lines = []
        if conversation_id is not None and store.log_path.exists():
            known_prefixes = {
                " | ".join(log_line(row).split(" | ", 2)[:2])
                for row in rows + remaining
            }
            known_ids = {row["id"] for row in rows + remaining}
            with store.log_path.open(encoding="utf-8", errors="replace") as old_log:
                for line in old_log:
                    prefix = " | ".join(line.split(" | ", 2)[:2])
                    attachment_id = (line.split(" | ", 1)[0].removeprefix("[ATTACHMENT] Message: ")
                                     if line.startswith("[ATTACHMENT] Message: ") else None)
                    if prefix not in known_prefixes and attachment_id not in known_ids:
                        extra_lines.append(line)
        fd, temporary = tempfile.mkstemp(prefix=".history-", dir=store.root)
        deleted_files = 0
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.writelines(extra_lines)
                for row in remaining:
                    handle.write(log_line(row))
                    if row["attachment_path"]:
                        handle.write(f"[ATTACHMENT] Message: {row['id']} | Saved: {row['attachment_path']}\n")
                handle.flush()
                os.fsync(handle.fileno())
            if delete_attachments:
                for path in safe_paths:
                    if path.is_file() or path.is_symlink():
                        path.unlink()
                        deleted_files += 1
            os.replace(temporary, store.log_path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"deleted_messages": len(rows), "deleted_attachments": deleted_files,
                "attachments_kept": not delete_attachments}
