"""PostgreSQL transaction adapter for the inbox's existing parameterized queries.

No schema is changed at application startup. Apply schema/postgres.sql to the
development database; Replit's Publish flow applies the production schema.
"""
from contextlib import contextmanager
import re

import psycopg
from psycopg.rows import dict_row

if __package__:
    from .storage import StorageBusyError
else:
    from storage import StorageBusyError


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, cursor, returning_id=False):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.fetchone()["id"] if returning_id else None

    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    """Translate only the small, known SQLite dialect used by Store."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        query = query.strip().rstrip(";")
        if query == "BEGIN IMMEDIATE":
            # The enclosing transaction already holds the inbox advisory lock.
            return self.connection.cursor()
        ignore = query.upper().startswith("INSERT OR IGNORE")
        query = re.sub(r"^INSERT OR IGNORE", "INSERT", query, flags=re.I)
        if re.match(r"INSERT OR REPLACE INTO settings\b", query, re.I):
            query = re.sub(r"INSERT OR REPLACE", "INSERT", query, count=1, flags=re.I)
            query += " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"
        elif re.match(r"INSERT OR REPLACE INTO delivery\b", query, re.I):
            query = re.sub(r"INSERT OR REPLACE", "INSERT", query, count=1, flags=re.I)
            query += (" ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,"
                      "timestamp=EXCLUDED.timestamp,error=EXCLUDED.error")
        if ignore:
            query += " ON CONFLICT DO NOTHING"
        query = re.sub(r"\bLIKE\b", "ILIKE", query).replace(" COLLATE NOCASE", "")
        query = re.sub(r"\bMAX\((\w+),\?\)", r"GREATEST(\1,?)", query)
        # PostgreSQL doesn't need the SQLite history-deletion trigger.
        match = re.match(r"DELETE FROM messages(\s+WHERE .*)?$", query, re.I | re.S)
        if match:
            predicate = match.group(1) or ""
            self.execute(
                "DELETE FROM quote_evaluations WHERE id IN ("
                "SELECT evaluation_id FROM quote_evaluation_sources WHERE message_id IN ("
                "SELECT id FROM messages" + predicate + "))", params)
        returning_id = bool(re.match(r"INSERT INTO quote_evaluations\b", query, re.I))
        if returning_id:
            query += " RETURNING id"
        # Parameters, including wildcard searches, remain bound by psycopg.
        query = query.replace("%", "%%").replace("?", "%s")
        cursor = self.connection.execute(query, params)
        return Result(cursor, returning_id)

    def executemany(self, query, rows):
        for params in rows:
            self.execute(query, params)


@contextmanager
def transaction(database_url):
    try:
        with psycopg.connect(
            database_url, connect_timeout=5, row_factory=dict_row,
        ) as connection:
            connection.execute("SET LOCAL statement_timeout = 15000")
            connection.execute("SET LOCAL lock_timeout = 5000")
            # Preserves Store's atomic read/check/write semantics across threads
            # and processes, without relying on an ephemeral filesystem lock.
            connection.execute("SELECT pg_advisory_xact_lock(734120251)")
            yield Connection(connection)
    except (psycopg.OperationalError, psycopg.errors.LockNotAvailable,
            psycopg.errors.QueryCanceled):
        raise StorageBusyError("Durable inbox storage is unavailable. Please retry shortly.") from None
