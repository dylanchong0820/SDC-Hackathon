---
name: Preview database stalls
description: Runtime SQLite connection contention and why public liveness checks can miss a broken inbox.
---

A successful page, health check or session-token response is not sufficient to
verify this inbox. Check staff sign-in and database-backed inbox requests too.

**Why:** A long-lived server kept answering public routes while new SQLite
connections hung in the runtime library's shared file-descriptor/inode mutex.
A separate process could access the same database, so ordinary file-lock checks
did not reveal the in-process stall. Parallel request/media connection lifecycles
need serialization and bounded waits; do not remove that safeguard as a cosmetic
performance optimization.

**How to apply:** Keep network requests and attachment downloads outside database
contexts. After concurrency changes, exercise simultaneous staff access and
verify authenticated status/conversation requests against the running app.
Use credentials only inside runtime scripts and never print them. Do not dump
request bodies or local variables when tracing a live server.
