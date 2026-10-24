---
name: Quotation review trust
description: Why review authorization binds to content and staff session rather than a browser checkbox alone.
---

Treat a quotation review as confirmation of specific content, not permission to send a message ID's latest content.

**Why:** OCR and file conversion are local, and attachments or message history can change between preview and analysis. A browser checkbox alone cannot protect against changed or unreviewed sources.

**How to apply:** Preserve session-bound, expiring review authorization and check the reviewed content before any model request. Quote numbering may change when staff confirm a subset; it is not source content. Extraction is intentionally repeated rather than caching private document/media payloads server-side. If adding caching, keep a bounded, short lifetime and validate current sources before reuse.
