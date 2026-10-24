---
name: Quotation review trust
description: Why review authorization binds to content and staff session rather than a browser checkbox alone.
---

Treat a quotation review as confirmation of specific content, not permission to send a message ID's latest content.

**Why:** OCR and file conversion are local, and attachments or message history can change between preview and analysis. A browser checkbox alone cannot protect against changed or unreviewed sources.

**How to apply:** Preserve session-bound, expiring review authorization and check the reviewed content before any model request. Quote numbering may change when staff confirm a subset; it is not source content. Extraction is intentionally repeated rather than caching private document/media payloads server-side. If adding caching, keep a bounded, short lifetime and validate current sources before reuse.

Coverage is part of approved content. Keep locally determined truncation disclosures attached to confirmation and the saved result; do not let the model remove or replace them.

**Why:** Confirming a five-page excerpt is not the same as confirming an entire longer quotation. A model may omit warnings even when the reader correctly detected missing pages.

**How to apply:** Bind meaningful coverage changes to review authorization. Any future extraction cache must retain the coverage disclosure along with the extracted content.
